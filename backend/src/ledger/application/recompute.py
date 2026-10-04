"""显式汇率重算（需求 §7，FX-05）：预览范围与差异 → 绑定预览摘要确认 → 分片事务执行。

每笔账目以预览时的版本为条件；若期间被修改，任务标记 superseded 并保留已完成进度。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.codec import entry_item
from ledger.domain.authz import Actor, require_family_admin
from ledger.domain.errors import DomainError, NotFound, RatePending, StaleRead, ValidationFailed
from ledger.domain.fx import FxSnapshot
from ledger.domain.money import convert_to_target

from . import actions, repo
from .actions import Built, Scope
from .context import AppContext
from .guard import guard_member, load_membership

MAX_CHANGES = 500
CHUNK = 20


def _months(start: date, end: date) -> list[str]:
    out, d = [], start.replace(day=1)
    while d <= end:
        out.append(d.isoformat()[:7])
        d = (d + timedelta(days=32)).replace(day=1)
    return out


def _view(item: dict[str, Any]) -> dict[str, Any]:
    out = {k: item[k] for k in ("job_id", "status", "date_from", "date_to")}
    for k in ("preview_digest", "preview", "applied_count", "failure_code"):
        if item.get(k) is not None:
            out[k] = item[k]
    return out


def create_preview(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    key: str,
    *,
    date_from: date,
    date_to: date,
    currencies: list[str] | None = None,
) -> dict[str, Any]:
    if date_to < date_from or (date_to - date_from).days > 366:
        raise ValidationFailed("日期范围无效（最长一年）")
    body = {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "currencies": sorted(currencies or []),
    }

    def build() -> Built[dict[str, Any]]:
        m = require_family_admin(load_membership(ctx, actor, fid))
        curs = repo.family_currencies(ctx, fid)
        changes: list[dict[str, Any]] = []
        pending: set[str] = set()
        deltas: dict[str, list[int]] = {}
        total = 0
        for month in _months(date_from, date_to):
            for e in repo.month_entries(ctx, fid, month):
                if not (date_from <= e.business_date <= date_to) or e.state != "active":
                    continue
                if currencies and e.currency not in currencies:
                    continue
                total += 1
                try:
                    snap = repo.resolve_snapshot(ctx, fid, e.business_date, e.currency)
                except RatePending:
                    pending.add(e.currency)
                    continue
                if (
                    snap.to_dict()["rates"] == e.fx_snapshot.to_dict()["rates"]
                    and snap.effective_date == e.fx_snapshot.effective_date
                ):
                    continue
                meta = curs[e.currency]
                nzd = convert_to_target(e.amount_minor, meta, "NZD", snap.usd())
                cny = convert_to_target(e.amount_minor, meta, "CNY", snap.usd())
                if (nzd, cny) == (e.nzd_minor, e.cny_minor) and snap.to_dict()[
                    "rates"
                ] == e.fx_snapshot.to_dict()["rates"]:
                    continue
                changes.append(
                    {
                        "eid": e.entry_id,
                        "v": e.version,
                        "month": e.month,
                        "snapshot": snap.to_dict(),
                        "nzd": nzd,
                        "cny": cny,
                        "old_nzd": e.nzd_minor,
                        "old_cny": e.cny_minor,
                    }
                )
                d = deltas.setdefault(e.month, [0, 0])
                sign = -1 if e.type == "refund" else 1
                d[0] += sign * (nzd - e.nzd_minor) if e.counts_in_stats else 0
                d[1] += sign * (cny - e.cny_minor) if e.counts_in_stats else 0
        if len(changes) > MAX_CHANGES:
            raise ValidationFailed(f"涉及 {len(changes)} 笔，超过单次 {MAX_CHANGES} 笔，请缩小范围")
        digest = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()
        jid = ctx.ids()
        preview = {
            "entry_count": total,
            "changed_count": len(changes),
            "months": [
                {"month": k, "nzd_delta_minor": v[0], "cny_delta_minor": v[1]}
                for k, v in sorted(deltas.items())
            ],
            "pending_currencies": sorted(pending),
        }
        item = {
            "PK": keys.family(fid),
            "SK": f"RECOMPUTE#{jid}",
            "type": "recompute",
            "job_id": jid,
            "status": "preview_ready",
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "preview_digest": digest,
            "preview": preview,
            "changes": changes,
            "progress": 0,
            "applied_count": 0,
            "created_by": actor.user_id,
            "created_at": keys.ts(ctx.clock()),
        }
        tx = ctx.store.tx()
        guard_member(tx, actor, m)
        tx.put_new(item)
        return Built(tx, _view(item), [{"object_type": "recompute", "object_id": jid}])

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        return get_job(ctx, actor, fid, r["results"][0]["object_id"])

    return actions.run(
        ctx, actor, Scope.family(fid, actor, key), key, "recompute.preview", body, build, replay
    )


def get_job(ctx: AppContext, actor: Actor, fid: str, jid: str) -> dict[str, Any]:
    require_family_admin(load_membership(ctx, actor, fid))
    item = ctx.store.get(keys.family(fid), f"RECOMPUTE#{jid}")
    if item is None:
        raise NotFound("重算任务不存在")
    return _view(item)


def confirm(
    ctx: AppContext, actor: Actor, fid: str, jid: str, *, preview_digest: str
) -> dict[str, Any]:
    """执行或继续执行。每片一个事务；重复调用从已完成进度继续（幂等）。"""
    m = require_family_admin(load_membership(ctx, actor, fid))
    item = ctx.store.get(keys.family(fid), f"RECOMPUTE#{jid}")
    if item is None:
        raise NotFound("重算任务不存在")
    if item["preview_digest"] != preview_digest:
        raise DomainError("预览已变化，请重新预览", code="version_conflict")
    if item["status"] in ("done", "superseded"):
        return _view(item)
    changes: list[dict[str, Any]] = item["changes"]
    progress = int(item["progress"])
    while progress < len(changes):
        chunk = changes[progress : progress + CHUNK]
        tx = ctx.store.tx()
        guard_member(tx, actor, m)
        tx.update(
            keys.family(fid),
            f"RECOMPUTE#{jid}",
            "SET progress = :np, applied_count = :np, #s = :applying",
            condition="progress = :p",
            names={"#s": "status"},
            values={":np": progress + len(chunk), ":p": progress, ":applying": "applying"},
            on_fail=lambda _: StaleRead("任务进度已变化"),
        )
        months: set[str] = set()
        superseded = False
        for c in chunk:
            try:
                e, sk = repo.get_entry(ctx, fid, c["eid"])
            except NotFound:
                superseded = True
                break
            if e.version != c["v"] or e.state != "active":
                superseded = True
                break
            new = replace(
                e,
                fx_snapshot=FxSnapshot.from_dict(c["snapshot"]),
                nzd_minor=c["nzd"],
                cny_minor=c["cny"],
                version=e.version + 1,
                updated_at=ctx.clock(),
            )
            tx.put(
                entry_item(new, sk),
                condition="version = :v",
                values={":v": c["v"]},
                on_fail=lambda _: StaleRead("账目已变化"),
            )
            months.add(e.month)
        if superseded:
            ctx.store.client.update_item(
                TableName=ctx.store.table,
                Key={"PK": {"S": keys.family(fid)}, "SK": {"S": f"RECOMPUTE#{jid}"}},
                UpdateExpression="SET #s = :s, failure_code = :f",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": {"S": "superseded"},
                    ":f": {"S": "entry_changed_since_preview"},
                },
            )
            break
        for mo in sorted(months):
            tx.update(
                keys.family(fid), keys.month_version(mo), "ADD version :one", values={":one": 1}
            )
        now = ctx.clock()
        tx.put_new(
            {
                "PK": keys.family(fid),
                "SK": keys.audit(now, ctx.ids()),
                "type": "audit",
                "actor": actor.user_id,
                "action": "rates.recompute",
                "object_id": jid,
                "fields": [c["eid"] for c in chunk],
                "at": keys.ts(now),
                "ttl": int((now + timedelta(days=180)).timestamp()),
            }
        )
        try:
            ctx.store.commit(tx)
        except StaleRead:
            fresh = ctx.store.get(keys.family(fid), f"RECOMPUTE#{jid}") or {}
            progress = int(fresh.get("progress", progress))
            continue
        progress += len(chunk)
    final = ctx.store.get(keys.family(fid), f"RECOMPUTE#{jid}")
    assert final is not None
    if final["status"] != "superseded" and int(final["progress"]) >= len(changes):
        ctx.store.client.update_item(
            TableName=ctx.store.table,
            Key={"PK": {"S": keys.family(fid)}, "SK": {"S": f"RECOMPUTE#{jid}"}},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": {"S": "done"}},
        )
        final["status"] = "done"
    return _view(final)


def confirm_action(
    ctx: AppContext, actor: Actor, fid: str, jid: str, key: str, *, preview_digest: str
) -> dict[str, Any]:
    _ = key  # 每片事务以进度为条件，确认本身可安全重复；key 仍按契约要求并校验格式
    if not actions.KEY_RE.match(key):
        raise ValidationFailed("Idempotency-Key 格式无效")
    return confirm(ctx, actor, fid, jid, preview_digest=preview_digest)

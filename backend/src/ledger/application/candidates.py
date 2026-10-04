"""候选批次：查看、编辑、删除、拆分、取消与原子确认（需求 §6.2；storage-design T7；ADR-0003）。

候选项只保存可编辑字段；汇率预览、状态与内容摘要在读取时按当前目录与汇率现场计算。
确认时比较客户端提交的摘要与当前重算的摘要，不一致即返回 candidate_stale（ISE-014）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.codec import dt_in, entry_item, entry_sk
from ledger.ai.normalize import CANDIDATE_TYPES, Draft
from ledger.domain.authz import Actor, require_candidate_owner, require_member
from ledger.domain.categories import Catalog
from ledger.domain.entries import Entry, EntryInput, check_payment_method, finalize, plan_create
from ledger.domain.errors import (
    DomainError,
    NotFound,
    RatePending,
    StaleRead,
    ValidationFailed,
    Violations,
)
from ledger.domain.money import CurrencyMeta, convert_to_target, format_minor, parse_amount

from . import actions, repo
from .actions import Built, Scope
from .context import AppContext
from .guard import guard_member, load_membership
from .recognition import stable_digest

EDITABLE = (
    "type",
    "business_date",
    "currency",
    "amount",
    "leaf_category_id",
    "payment_method",
    "note",
)
MAX_CANDIDATES = 10


def new_candidate_item(
    ctx: AppContext,
    fid: str,
    bid: str,
    cid: str,
    d: Draft,
    manifest: int,
    attachment_id: str | None,
    receipt_group: str | None,
) -> dict[str, Any]:
    return {
        "PK": keys.family(fid),
        "SK": f"BATCH#{bid}#CAND#{cid}",
        "type": "candidate",
        "candidate_id": cid,
        "batch_id": bid,
        "version": 1,
        "status": "open",
        "c_type": d.type,
        "business_date": d.business_date,
        "currency": d.currency,
        "amount": d.amount,
        "leaf_category_id": d.category_id,
        "payment_method": d.payment_method,
        "note": d.note,
        "field_sources": d.field_sources,
        "needs_review": d.needs_review,
        "problems": d.problems,
        "manifest_at_creation": manifest,
        "attachment_id": attachment_id,
        "receipt_group_id": receipt_group,
        "created_at": keys.ts(ctx.clock()),
    }


# ── 现场视图 ──


class _Env:
    def __init__(self, ctx: AppContext, actor: Actor, fid: str) -> None:
        self.ctx = ctx
        self.m = require_member(load_membership(ctx, actor, fid))
        self.cfg = repo.family_config(ctx, fid)
        self.now = ctx.clock()
        self.today = repo.family_today(self.cfg, self.now)
        self.catalog: Catalog = repo.catalog(ctx, fid)
        self.currencies: dict[str, CurrencyMeta] = repo.family_currencies(ctx, fid)
        self.fid = fid


def _fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": item.get("c_type"),
        "business_date": item.get("business_date"),
        "currency": item.get("currency"),
        "amount": item.get("amount"),
        "leaf_category_id": item.get("leaf_category_id"),
        "payment_method": item.get("payment_method"),
        "note": item.get("note", ""),
    }


def candidate_view(env: _Env, item: dict[str, Any], batch_open: bool) -> dict[str, Any]:
    f = _fields(item)
    missing = [k for k in ("type", "business_date", "currency", "amount") if not f[k]]
    if not f["leaf_category_id"]:
        missing.append("category")
    if f["type"] == "expense" and not f["payment_method"]:
        missing.append("payment_method")
    problems = list(item.get("problems") or [])
    category: dict[str, Any] | None = None
    if f["leaf_category_id"] and f["leaf_category_id"] in env.catalog.by_id:
        category = env.catalog.resolve(f["leaf_category_id"]).to_dict()
        if f["type"]:
            try:
                env.catalog.require_selectable(f["leaf_category_id"], f["type"])
            except DomainError as e:
                problems.append(e.code)  # category_inactive／category_kind_mismatch
    fx: dict[str, Any] | None = None
    if f["amount"] and f["currency"] in env.currencies and f["business_date"]:
        try:
            snap = repo.resolve_snapshot(
                env.ctx, env.fid, date.fromisoformat(f["business_date"]), f["currency"]
            )
            meta = env.currencies[f["currency"]]
            minor = parse_amount(f["amount"], meta, max_major=env.ctx.limits.max_major)
            nzd = convert_to_target(minor, meta, "NZD", snap.usd())
            cny = convert_to_target(minor, meta, "CNY", snap.usd())
            fx = {
                "status": "ok",
                "snapshot": snap.to_dict(),
                "display_amounts": {
                    "amount": format_minor(minor, meta.minor_digits),
                    "nzd": format_minor(nzd, 2),
                    "cny": format_minor(cny, 2),
                },
            }
        except RatePending as e:
            fx = {"status": "rate_pending", **(e.current or {})}
            problems.append("rate_pending")
        except (DomainError, ValueError):
            problems.append("amount_invalid")
    stored = item["status"]
    if stored in ("confirmed", "deleted", "split"):
        status = "confirmed" if stored == "confirmed" else "deleted"
    elif not batch_open:
        status = "expired"
    elif "category_inactive" in problems or "category_kind_mismatch" in problems:
        status = "needs_update"
    elif missing or problems:
        status = "incomplete"
    else:
        status = "ready"
    digest = stable_digest(
        {
            "fields": f,
            "version": item["version"],
            "fx": fx["snapshot"] if fx and fx["status"] == "ok" else None,
        }
    )
    out: dict[str, Any] = {
        "candidate_id": item["candidate_id"],
        "batch_id": item["batch_id"],
        "version": int(item["version"]),
        "status": status,
        "note": f["note"],
        "field_sources": item.get("field_sources") or {},
        "missing_fields": missing,
        "needs_review": item.get("needs_review") or [],
        "problems": sorted(set(problems)),
        "content_digest": digest,
        "payment_method": f["payment_method"],
        "amount": f["amount"],
    }
    for k in ("type", "business_date", "currency"):
        if f[k]:
            out[k] = f[k]
    if category:
        out["category"] = category
    if fx:
        out["fx_preview"] = fx
    for k in ("receipt_group_id", "attachment_id", "entry_id"):
        if item.get(k):
            out[k] = item[k]
    return out


def _batch_open(env: _Env, batch: dict[str, Any]) -> bool:
    expires = dt_in(batch["expires_at"])
    return batch["status"] == "open" and expires is not None and expires > env.now


def _load_batch(env: _Env, actor: Actor, bid: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    batch = env.ctx.store.get(keys.family(env.fid), f"BATCH#{bid}")
    if batch is None:
        raise NotFound("批次不存在")
    require_candidate_owner(actor, batch["actor_uid"])
    cands = list(env.ctx.store.query_all(keys.family(env.fid), f"BATCH#{bid}#CAND#"))
    return batch, cands


def batch_view(env: _Env, batch: dict[str, Any], cands: list[dict[str, Any]]) -> dict[str, Any]:
    is_open = _batch_open(env, batch)
    views = [
        candidate_view(env, c, is_open) for c in cands if c["status"] not in ("deleted", "split")
    ]
    status = batch["status"]
    if status == "open" and not is_open:
        status = "expired"
    return {
        "batch_id": batch["batch_id"],
        "source": batch["source"],
        "status": status,
        "expires_at": batch["expires_at"],
        "candidate_count": len(views),
        "version": int(batch["version"]),
        "candidates": views,
        "input_issues": batch.get("input_issues", []),
    }


def get_batch(ctx: AppContext, actor: Actor, fid: str, bid: str) -> dict[str, Any]:
    env = _Env(ctx, actor, fid)
    batch, cands = _load_batch(env, actor, bid)
    return batch_view(env, batch, cands)


def list_batches(
    ctx: AppContext, actor: Actor, fid: str, status: str | None = None
) -> list[dict[str, Any]]:
    env = _Env(ctx, actor, fid)
    out = []
    for b in ctx.store.query_all(keys.family(fid), "BATCH#"):
        if "#CAND#" in b["SK"] or b["actor_uid"] != actor.user_id:
            continue
        cands = list(ctx.store.query_all(keys.family(fid), f"BATCH#{b['batch_id']}#CAND#"))
        v = batch_view(env, b, cands)
        if status and v["status"] != status:
            continue
        out.append(
            {
                k: v[k]
                for k in (
                    "batch_id",
                    "source",
                    "status",
                    "expires_at",
                    "candidate_count",
                    "version",
                )
            }
        )
    return sorted(out, key=lambda x: x["expires_at"], reverse=True)


# ── 编辑、删除、拆分、取消 ──


def _stale_cand(_: dict[str, Any] | None) -> DomainError:
    return StaleRead("候选已变化")


def _require_editable(
    env: _Env, batch: dict[str, Any], cand: dict[str, Any], expected_version: int
) -> None:
    if not _batch_open(env, batch):
        raise DomainError("候选已过期或批次已关闭", code="candidate_expired")
    if cand["status"] != "open":
        raise DomainError("该候选已确认或已删除", code="candidate_stale")
    if int(cand["version"]) != expected_version:
        raise DomainError(
            "候选已被修改", code="version_conflict", current=candidate_view(env, cand, True)
        )


def update_candidate(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    bid: str,
    cid: str,
    key: str,
    patch: dict[str, Any],
    expected_version: int,
) -> dict[str, Any]:
    unknown = set(patch) - set(EDITABLE)
    if unknown:
        raise ValidationFailed(f"不可修改的字段：{sorted(unknown)}")

    def build() -> Built[dict[str, Any]]:
        env = _Env(ctx, actor, fid)
        batch, cands = _load_batch(env, actor, bid)
        cand = next((c for c in cands if c["candidate_id"] == cid), None)
        if cand is None:
            raise NotFound("候选不存在")
        _require_editable(env, batch, cand, expected_version)
        f = {**_fields(cand), **patch}
        v = Violations()
        if f["type"] is not None and f["type"] not in CANDIDATE_TYPES:
            v.add("type", "invalid")
        if f["currency"] is not None and f["currency"] not in env.currencies:
            v.add("currency", "not_enabled")
        if f["amount"] is not None and f["currency"] in env.currencies:
            try:
                parse_amount(
                    f["amount"], env.currencies[f["currency"]], max_major=ctx.limits.max_major
                )
            except DomainError:
                v.add("amount", "invalid")
        if "leaf_category_id" in patch and f["type"]:
            env.catalog.require_selectable(f["leaf_category_id"], f["type"])
        if f["type"] and f["type"] not in ("expense", "income"):
            f["payment_method"] = None  # 不计收支类型不填方式
        if f["payment_method"] is not None:
            check_payment_method(f["type"] or "expense", f["payment_method"], v)
        if len(f["note"] or "") > 200:
            v.add("note", "too_long")
        v.raise_if_any()
        sources = dict(cand.get("field_sources") or {})
        review = set(cand.get("needs_review") or [])
        problems = set(cand.get("problems") or [])
        for k in patch:
            name = "category_id" if k == "leaf_category_id" else k
            sources[name] = "user"
            review.discard(name)
        if "type" in patch:
            problems.discard("refund_not_supported")
        new = {
            **cand,
            "c_type": f["type"],
            "business_date": f["business_date"],
            "currency": f["currency"],
            "amount": f["amount"],
            "leaf_category_id": f["leaf_category_id"],
            "payment_method": f["payment_method"],
            "note": f["note"] or "",
            "field_sources": sources,
            "needs_review": sorted(review),
            "problems": sorted(problems),
            "version": expected_version + 1,
        }
        tx = ctx.store.tx()
        guard_member(tx, actor, env.m)
        tx.put(
            new,
            condition="version = :v AND #s = :open",
            names={"#s": "status"},
            values={":v": expected_version, ":open": "open"},
            on_fail=_stale_cand,
        )
        return Built(
            tx,
            candidate_view(env, new, True),
            [{"object_type": "candidate", "object_id": cid, "version": expected_version + 1}],
        )

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        env = _Env(ctx, actor, fid)
        batch, cands = _load_batch(env, actor, bid)
        cand = next(c for c in cands if c["candidate_id"] == cid)
        return candidate_view(env, cand, _batch_open(env, batch))

    return actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "candidate.update",
        {"cid": cid, "patch": patch, "v": expected_version},
        build,
        replay,
    )


def delete_candidate(
    ctx: AppContext, actor: Actor, fid: str, bid: str, cid: str, key: str, expected_version: int
) -> None:
    def build() -> Built[None]:
        env = _Env(ctx, actor, fid)
        batch, cands = _load_batch(env, actor, bid)
        cand = next((c for c in cands if c["candidate_id"] == cid), None)
        if cand is None:
            raise NotFound("候选不存在")
        _require_editable(env, batch, cand, expected_version)
        tx = ctx.store.tx()
        guard_member(tx, actor, env.m)
        tx.update(
            cand["PK"],
            cand["SK"],
            "SET #s = :d, version = version + :one",
            condition="version = :v AND #s = :open",
            names={"#s": "status"},
            values={":d": "deleted", ":one": 1, ":v": expected_version, ":open": "open"},
            on_fail=_stale_cand,
        )
        return Built(tx, None, [{"object_type": "candidate", "object_id": cid}])

    actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "candidate.delete",
        {"cid": cid, "v": expected_version},
        build,
        lambda r: None,
    )


def split_candidate(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    bid: str,
    cid: str,
    key: str,
    parts: list[dict[str, Any]],
    expected_version: int,
) -> dict[str, Any]:
    """把一条候选拆成 2–10 条；原币合计必须等于原金额（ADR-0003）。"""
    if not 2 <= len(parts) <= MAX_CANDIDATES:
        raise ValidationFailed("拆分需为 2–10 份")

    def build() -> Built[dict[str, Any]]:
        env = _Env(ctx, actor, fid)
        batch, cands = _load_batch(env, actor, bid)
        cand = next((c for c in cands if c["candidate_id"] == cid), None)
        if cand is None:
            raise NotFound("候选不存在")
        _require_editable(env, batch, cand, expected_version)
        if not cand.get("amount") or not cand.get("currency"):
            raise ValidationFailed("请先补全金额与币种再拆分")
        meta = env.currencies[cand["currency"]]
        total = parse_amount(cand["amount"], meta, max_major=ctx.limits.max_major)
        minors = [parse_amount(p["amount"], meta, max_major=ctx.limits.max_major) for p in parts]
        if sum(minors) != total:
            raise ValidationFailed(
                f"拆分合计 {format_minor(sum(minors), meta.minor_digits)} 不等于原金额 "
                f"{cand['amount']}"
            )
        alive = [c for c in cands if c["status"] == "open"]
        if len(alive) - 1 + len(parts) > MAX_CANDIDATES:
            raise DomainError("批次候选不能超过 10 条", code="batch_too_large")
        gid = cand.get("receipt_group_id") or ("g" + cid)
        tx = ctx.store.tx()
        guard_member(tx, actor, env.m)
        tx.update(
            cand["PK"],
            cand["SK"],
            "SET #s = :split, version = version + :one",
            condition="version = :v AND #s = :open",
            names={"#s": "status"},
            values={":split": "split", ":one": 1, ":v": expected_version, ":open": "open"},
            on_fail=_stale_cand,
        )
        if ctx.store.get(keys.family(fid), f"RGROUP#{gid}") is None:
            tx.put_new(
                {
                    "PK": keys.family(fid),
                    "SK": f"RGROUP#{gid}",
                    "type": "receipt_group",
                    "receipt_group_id": gid,
                    "currency": cand["currency"],
                    "total_minor": total,
                    "attachment_id": cand.get("attachment_id"),
                    "child_entry_ids": [],
                }
            )
        for i, p in enumerate(parts):
            sources = {**(cand.get("field_sources") or {}), "amount": "user"}
            leaf = str(p.get("leaf_category_id") or cand.get("leaf_category_id") or "")
            if "leaf_category_id" in p:
                sources["category_id"] = "user"
                if cand.get("c_type"):
                    env.catalog.require_selectable(leaf, cand["c_type"])
            tx.put_new(
                {
                    **cand,
                    "SK": f"BATCH#{bid}#CAND#{cid}s{i}",
                    "candidate_id": f"{cid}s{i}",
                    "version": 1,
                    "status": "open",
                    "amount": p["amount"],
                    "leaf_category_id": leaf,
                    "note": p.get("note", cand.get("note", "")),
                    "field_sources": sources,
                    "receipt_group_id": gid,
                    "split_from": cid,
                }
            )
        return Built(tx, {}, [{"object_type": "candidate", "object_id": cid}])

    actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "candidate.split",
        {"cid": cid, "parts": parts, "v": expected_version},
        build,
        lambda r: {},
    )
    return get_batch(ctx, actor, fid, bid)


def cancel_batch(
    ctx: AppContext, actor: Actor, fid: str, bid: str, key: str, expected_version: int
) -> dict[str, Any]:
    def build() -> Built[None]:
        env = _Env(ctx, actor, fid)
        batch, _ = _load_batch(env, actor, bid)
        if int(batch["version"]) != expected_version:
            raise DomainError("批次已变化", code="version_conflict")
        tx = ctx.store.tx()
        guard_member(tx, actor, env.m)
        tx.update(
            batch["PK"],
            batch["SK"],
            "SET #s = :c, version = version + :one",
            condition="version = :v AND #s = :open",
            names={"#s": "status"},
            values={":c": "cancelled", ":one": 1, ":v": expected_version, ":open": "open"},
            on_fail=lambda _: DomainError("批次已关闭", code="candidate_expired"),
        )
        return Built(tx, None, [{"object_type": "batch", "object_id": bid}])

    actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "batch.cancel",
        {"bid": bid, "v": expected_version},
        build,
        lambda r: None,
    )
    return get_batch(ctx, actor, fid, bid)


# ── 确认（T7：≤10 条，同事务全有或全无） ──


def confirm(
    ctx: AppContext, actor: Actor, fid: str, bid: str, key: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    if not 1 <= len(items) <= MAX_CANDIDATES:
        raise DomainError("一次最多确认 10 条", code="batch_too_large")
    if len({i["candidate_id"] for i in items}) != len(items):
        raise ValidationFailed("候选重复")

    def build() -> Built[dict[str, Any]]:
        env = _Env(ctx, actor, fid)
        batch, cands = _load_batch(env, actor, bid)  # AUTH-10：只有发起人
        if not _batch_open(env, batch):
            raise DomainError("候选已过期或批次已关闭", code="candidate_expired")
        by_id = {c["candidate_id"]: c for c in cands}
        stale: list[dict[str, Any]] = []
        plans: list[tuple[dict[str, Any], Any, Any]] = []
        for req in items:
            cand = by_id.get(req["candidate_id"])
            if cand is None:
                raise NotFound("候选不存在")
            view = candidate_view(env, cand, True)
            if (
                view["version"] != req["version"]
                or view["content_digest"] != req["content_digest"]
                or view["status"] != "ready"
            ):
                stale.append(view)
                continue
            f = _fields(cand)
            plan = plan_create(
                EntryInput(
                    business_date=f["business_date"],
                    type=f["type"],
                    amount=f["amount"],
                    currency=f["currency"],
                    leaf_category_id=f["leaf_category_id"],
                    note=f["note"],
                    payment_method=f["payment_method"],
                ),
                catalog=env.catalog,
                currencies=env.currencies,
                today=env.today,
                limits=ctx.limits,
            )
            snap = repo.resolve_snapshot(ctx, fid, plan.business_date, plan.currency)
            plans.append((cand, plan, snap))
        if stale:
            raise DomainError(
                "候选内容已变化（汇率、分类或版本），请核对后再确认",
                code="candidate_stale",
                current={"candidates": stale},
            )

        tx = ctx.store.tx()
        guard_member(tx, actor, env.m)
        tx.check(
            keys.family(fid),
            keys.CONFIG,
            "category_manifest_version = :m",
            values={":m": env.catalog.manifest_version},
            on_fail=lambda _: StaleRead("分类目录已变化"),
        )
        for leaf in sorted({p.leaf_category_id for _, p, _ in plans}):
            tx.check(
                keys.family(fid),
                keys.category(leaf),
                "#s = :active AND attribute_not_exists(redirect_to)",
                names={"#s": "status"},
                values={":active": "active"},
                on_fail=lambda _: StaleRead("分类已变化"),
            )
        remaining = [
            c
            for c in cands
            if c["status"] == "open" and c["candidate_id"] not in {i["candidate_id"] for i in items}
        ]
        tx.update(
            batch["PK"],
            batch["SK"],
            "SET version = version + :one, #s = :st",
            condition="version = :v AND #s = :open AND expires_at > :now",
            names={"#s": "status"},
            values={
                ":one": 1,
                ":v": int(batch["version"]),
                ":open": "open",
                ":now": keys.ts(env.now),
                ":st": "open" if remaining else "closed",
            },
            on_fail=lambda _: StaleRead("批次已变化"),
        )
        results, months = [], set()
        groups: dict[str, list[str]] = {}
        attach_refs: dict[str, int] = {}
        for cand, plan, snap in plans:
            eid = ctx.ids()
            nzd, cny = finalize(plan, snap, env.currencies)
            att = (cand["attachment_id"],) if cand.get("attachment_id") else ()
            e = Entry(
                entry_id=eid,
                family_id=fid,
                business_date=plan.business_date,
                type=plan.type,
                leaf_category_id=plan.leaf_category_id,
                currency=plan.currency,
                amount_minor=plan.amount_minor,
                note=plan.note,
                payment_method=plan.payment_method,
                created_by=actor.user_id,
                created_at=env.now,
                updated_at=env.now,
                source=batch["source"],
                version=1,
                fx_snapshot=snap,
                nzd_minor=nzd,
                cny_minor=cny,
                attachment_ids=att,
                receipt_group_id=cand.get("receipt_group_id"),
            )
            sk = entry_sk(e)
            tx.put_new(entry_item(e, sk))
            tx.put_new(
                {
                    "PK": keys.family(fid),
                    "SK": keys.locator(eid),
                    "type": "locator",
                    "entry_id": eid,
                    "sk": sk,
                    "state": "active",
                }
            )
            tx.update(
                cand["PK"],
                cand["SK"],
                "SET #s = :c, entry_id = :e",
                condition="version = :v AND #s = :open",
                names={"#s": "status"},
                values={":c": "confirmed", ":e": eid, ":v": int(cand["version"]), ":open": "open"},
                on_fail=_stale_cand,
            )
            months.add(e.month)
            results.append({"candidate_id": cand["candidate_id"], "entry_id": eid})
            if e.receipt_group_id:
                groups.setdefault(e.receipt_group_id, []).append(eid)
            for a in att:
                attach_refs[a] = attach_refs.get(a, 0) + 1
        for gid, eids in groups.items():
            existing = ctx.store.get(keys.family(fid), f"RGROUP#{gid}")
            if existing is None:
                first = next(p for c, p, _ in plans if c.get("receipt_group_id") == gid)
                tx.put_new(
                    {
                        "PK": keys.family(fid),
                        "SK": f"RGROUP#{gid}",
                        "type": "receipt_group",
                        "receipt_group_id": gid,
                        "currency": first.currency,
                        "total_minor": first.amount_minor,
                        "child_entry_ids": eids,
                    }
                )
            else:
                tx.update(
                    keys.family(fid),
                    f"RGROUP#{gid}",
                    "SET child_entry_ids = list_append(child_entry_ids, :e)",
                    values={":e": eids},
                )
        for aid, n in attach_refs.items():
            item = ctx.store.get(keys.family(fid), f"ATT#{aid}")
            if item is None or item["status"] != "ready":
                raise DomainError("照片不存在或已失效", code="upload_invalid")
            tx.update(
                keys.family(fid),
                f"ATT#{aid}",
                "SET ref_count = ref_count + :n REMOVE GSI1PK, GSI1SK",
                condition="#s = :ready AND ref_count = :rc",
                names={"#s": "status"},
                values={":n": n, ":ready": "ready", ":rc": int(item["ref_count"])},
                on_fail=lambda _: StaleRead("照片引用已变化"),
            )
        for mo in sorted(months):
            tx.update(
                keys.family(fid), keys.month_version(mo), "ADD version :one", values={":one": 1}
            )
        tx.put_new(
            {
                "PK": keys.family(fid),
                "SK": keys.audit(env.now, ctx.ids()),
                "type": "audit",
                "actor": actor.user_id,
                "action": "batch.confirm",
                "object_id": bid,
                "fields": [r["entry_id"] for r in results],
                "at": keys.ts(env.now),
                "ttl": int((env.now + timedelta(days=180)).timestamp()),
            }
        )
        return Built(
            tx,
            {"action_id": key, "status": "committed", "entries": results},
            [{"object_type": "entry", "object_id": r["entry_id"]} for r in results],
        )

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        env = _Env(ctx, actor, fid)
        _, cands = _load_batch(env, actor, bid)
        by_entry = {c.get("entry_id"): c["candidate_id"] for c in cands if c.get("entry_id")}
        return {
            "action_id": key,
            "status": "committed",
            "entries": [
                {"candidate_id": by_entry.get(x["object_id"], ""), "entry_id": x["object_id"]}
                for x in r["results"]
            ],
        }

    body = {"bid": bid, "items": sorted(items, key=lambda i: i["candidate_id"])}
    return actions.run(
        ctx, actor, Scope.family(fid, actor, key), key, "batch.confirm", body, build, replay
    )

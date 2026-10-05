"""账目用例（需求 §4–5；storage-design §4 T1–T6；ADR-0006／0010）。"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.codec import entry_from, entry_item, entry_sk
from ledger.adapters.dynamo.store import Tx
from ledger.domain import dashboard as dash
from ledger.domain.authz import Actor, Membership, require_can_modify_entry, require_member
from ledger.domain.categories import Catalog
from ledger.domain.entries import (
    Entry,
    EntryInput,
    apply_plan,
    check_refund,
    finalize,
    plan_create,
    plan_update,
    restored,
    trashed,
)
from ledger.domain.errors import DomainError, NotFound, StaleRead, ValidationFailed
from ledger.domain.money import CurrencyMeta

from . import actions, attachments, repo, secondary
from .actions import Built, Scope
from .context import AppContext
from .guard import guard_member, load_membership
from .views import entry_view

PURGE_SHARDS = 4


# ── 事务片段 ──


def _stale(_: dict[str, Any] | None) -> DomainError:
    return StaleRead("账目已被修改")


def _put_same(tx: Tx, e: Entry, sk: str, expected_version: int) -> None:
    tx.put(
        entry_item(e, sk), condition="version = :v", values={":v": expected_version}, on_fail=_stale
    )


def _move(
    tx: Tx,
    old_sk: str,
    new: Entry,
    new_sk: str,
    expected_version: int,
    *,
    state: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """原子迁移排序项（跨月／改日期／入回收站／恢复），同时更新定位器。"""
    pk = keys.family(new.family_id)
    tx.delete(pk, old_sk, condition="version = :v", values={":v": expected_version}, on_fail=_stale)
    tx.put_new({**entry_item(new, new_sk), **(extra or {})})
    tx.update(
        pk,
        keys.locator(new.entry_id),
        "SET sk = :new, #st = :state",
        condition="sk = :old",
        names={"#st": "state"},
        values={":new": new_sk, ":old": old_sk, ":state": state},
        on_fail=_stale,
    )


def _bump(tx: Tx, fid: str, months: set[str]) -> None:
    for m in sorted(months):
        tx.update(keys.family(fid), keys.month_version(m), "ADD version :one", values={":one": 1})


def _check_manifest(tx: Tx, fid: str, catalog: Catalog) -> None:
    tx.check(
        keys.family(fid),
        keys.CONFIG,
        "category_manifest_version = :m",
        values={":m": catalog.manifest_version},
        on_fail=lambda _: StaleRead("分类目录已变化"),
    )


def _check_leaf_active(tx: Tx, fid: str, leaf: str) -> None:
    tx.check(
        keys.family(fid),
        keys.category(leaf),
        "#s = :active AND attribute_not_exists(redirect_to)",
        names={"#s": "status"},
        values={":active": "active"},
        on_fail=lambda _: DomainError("该分类已停用", code="category_inactive"),
    )


def _audit(
    ctx: AppContext,
    tx: Tx,
    fid: str,
    actor: Actor,
    action: str,
    e: Entry,
    fields: list[str] | None = None,
) -> None:
    now = ctx.clock()
    tx.put_new(
        {
            "PK": keys.family(fid),
            "SK": keys.audit(now, ctx.ids()),
            "type": "audit",
            "actor": actor.user_id,
            "action": action,
            "object_id": e.entry_id,
            "version": e.version,
            "fields": sorted(fields or []),  # 只记字段名，不记备注原文
            "amount_minor": e.amount_minor,
            "currency": e.currency,
            "at": keys.ts(now),
            "ttl": int((now + timedelta(days=180)).timestamp()),
        }
    )


def _trash_extra(e: Entry) -> dict[str, Any]:
    assert e.purge_after is not None
    shard = int(hashlib.sha256(e.entry_id.encode()).hexdigest(), 16) % PURGE_SHARDS
    return {
        "GSI1PK": keys.work("trash_purge", shard),
        "GSI1SK": f"{keys.ts(e.purge_after)}#{e.family_id}#{e.entry_id}",
    }


def _refund_target(ctx: AppContext, fid: str, eid: str) -> tuple[Entry, str]:
    try:
        return repo.get_entry(ctx, fid, eid)
    except NotFound:
        raise DomainError("退款关联的原消费不存在", code="refund_target_invalid") from None


class _Env:
    """一次构建中共用的读取结果。"""

    def __init__(self, ctx: AppContext, actor: Actor, fid: str) -> None:
        self.ctx = ctx
        self.m: Membership = require_member(load_membership(ctx, actor, fid))
        self.cfg = repo.family_config(ctx, fid)
        self.now = ctx.clock()
        self.today = repo.family_today(self.cfg, self.now)
        self.catalog = repo.catalog(ctx, fid)
        self.currencies: dict[str, CurrencyMeta] = repo.family_currencies(ctx, fid)
        self.names = repo.display_names(ctx, fid)

    def view(self, e: Entry) -> dict[str, Any]:
        out = entry_view(e, self.catalog, self.currencies, self.names, self.m)
        if e.attachment_ids:
            metas = []
            for aid in e.attachment_ids:
                item = self.ctx.store.get(keys.family(e.family_id), f"ATT#{aid}") or {}
                metas.append({"attachment_id": aid, "content_type": item.get("content_type", "")})
            out["attachments"] = metas
        return out


# ── 查询 ──


def get_entry(ctx: AppContext, actor: Actor, fid: str, eid: str) -> dict[str, Any]:
    env = _Env(ctx, actor, fid)
    e, _ = repo.get_entry(ctx, fid, eid)
    return env.view(e)


def _parse_filters(filters: dict[str, Any] | None) -> dash.Filters:
    f = filters or {}
    return dash.Filters(
        created_by=f.get("created_by"),
        type=f.get("type"),
        category_id=f.get("category_id"),
        payment_method=f.get("payment_method"),
    )


def _check_month(month: str) -> None:
    import re

    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", month or ""):
        raise ValidationFailed("月份格式应为 YYYY-MM")


def _consistent_month(ctx: AppContext, fid: str, month: str) -> tuple[list[Entry], str]:
    """读月版本→全月强一致读取→再读版本；变化则重试一次（ADR-0010）。"""
    for _ in range(2):
        v1 = repo.month_version(ctx, fid, month)
        rows = repo.month_entries(ctx, fid, month)
        v2 = repo.month_version(ctx, fid, month)
        if v1 == v2:
            return rows, f"{month}:{v1}"
    raise DomainError("账目正在变化，请稍后刷新", code="data_changing")


def dashboard(
    ctx: AppContext, actor: Actor, fid: str, month: str, filters: dict[str, Any] | None = None
) -> dict[str, Any]:
    _check_month(month)
    env = _Env(ctx, actor, fid)
    f = _parse_filters(filters)
    rows, version = _consistent_month(ctx, fid, month)
    out = dash.compute(rows, env.catalog, f)
    cfg = repo.family_config(ctx, fid)
    matched = [e for e in rows if e.state == "active" and dash.matches(e, f, env.catalog)]
    # 主币种＝家庭默认币种（净支出、饼图、消费方式）；辅助币种可选（小字）
    main = secondary.primary(cfg)
    prim = secondary.summary_block(
        secondary.CurrencyConverter(ctx, fid, main, matched), matched, env.catalog
    )
    target = secondary.configured(cfg)
    sec = None
    if target:
        sec = secondary.summary_block(
            secondary.CurrencyConverter(ctx, fid, target, matched), matched
        )
    return {
        "family_id": fid,
        "month": month,
        "filters": f.to_dict(),
        "data_version": version,
        "computed_at": keys.ts(env.now),
        **out,
        "primary": prim,
        "secondary": sec,
    }


def _encode_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        out = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        assert isinstance(out, dict)
        return out
    except Exception:
        raise ValidationFailed("分页游标无效") from None


def list_entries(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    month: str,
    filters: dict[str, Any] | None = None,
    page_size: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    _check_month(month)
    if not 1 <= page_size <= 100:
        raise ValidationFailed("page_size 需在 1–100 之间")
    env = _Env(ctx, actor, fid)
    f = _parse_filters(filters)
    sig = hashlib.sha256(json.dumps([month, f.to_dict()], sort_keys=True).encode()).hexdigest()
    rows, version = _consistent_month(ctx, fid, month)
    offset = 0
    if cursor:
        c = _decode_cursor(cursor)
        if c.get("s") != sig:
            raise ValidationFailed("分页游标与查询条件不符")
        if c.get("v") != version:
            raise DomainError("账目已变化，请刷新", code="data_changed")
        offset = int(c.get("o", 0))
    matched = [e for e in rows if dash.matches(e, f, env.catalog)]
    page = matched[offset : offset + page_size]
    cfg = repo.family_config(ctx, fid)
    pconv = secondary.CurrencyConverter(ctx, fid, secondary.primary(cfg), page)
    target = secondary.configured(cfg)
    sconv = secondary.CurrencyConverter(ctx, fid, target, page) if target else None
    items = [
        env.view(e) | {"primary": pconv.amount(e), "secondary": sconv.amount(e) if sconv else None}
        for e in page
    ]
    out: dict[str, Any] = {"items": items, "data_version": version}
    if offset + page_size < len(matched):
        out["next_cursor"] = _encode_cursor({"s": sig, "v": version, "o": offset + page_size})
    return out


def list_trash(ctx: AppContext, actor: Actor, fid: str) -> dict[str, Any]:
    env = _Env(ctx, actor, fid)
    rows = [entry_from(i) for i in ctx.store.query_all(keys.family(fid), "TRASH#")]
    rows.sort(key=lambda e: (e.deleted_at or env.now, e.entry_id), reverse=True)
    return {"items": [env.view(e) for e in rows], "data_version": keys.ts(env.now)}


def _replay_entry(ctx: AppContext, actor: Actor, fid: str):  # type: ignore[no-untyped-def]
    def replay(r: dict[str, Any]) -> dict[str, Any]:
        return get_entry(ctx, actor, fid, r["results"][0]["object_id"])

    return replay


# ── 创建（T1／T4） ──


def create_entry(
    ctx: AppContext, actor: Actor, fid: str, key: str, data: EntryInput, *, source: str = "manual"
) -> dict[str, Any]:
    body = data.__dict__ | {"attachment_ids": list(data.attachment_ids), "source": source}

    def build() -> Built[dict[str, Any]]:
        env = _Env(ctx, actor, fid)
        original: Entry | None = None
        orig_sk = ""
        if data.type == "refund" and data.refund_of:
            original, orig_sk = _refund_target(ctx, fid, data.refund_of)
        plan = plan_create(
            data,
            catalog=env.catalog,
            currencies=env.currencies,
            today=env.today,
            limits=ctx.limits,
            original=original,
        )
        snap = repo.resolve_snapshot(ctx, fid, plan.business_date, plan.currency)
        nzd, cny = finalize(plan, snap, env.currencies)
        eid = ctx.ids()
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
            source=source,
            version=1,
            fx_snapshot=snap,
            nzd_minor=nzd,
            cny_minor=cny,
            attachment_ids=plan.attachment_ids,
            refund_of=plan.refund_of,
        )
        sk = entry_sk(e)
        tx = ctx.store.tx()
        guard_member(tx, actor, env.m)
        _check_manifest(tx, fid, env.catalog)
        months = {e.month}
        if original is None:
            _check_leaf_active(tx, fid, e.leaf_category_id)
        else:
            new_orig = replace(
                original,
                refunded_minor=original.refunded_minor + e.amount_minor,
                refund_ids=(*original.refund_ids, eid),
                version=original.version + 1,
            )
            _put_same(tx, new_orig, orig_sk, original.version)
            months.add(original.month)
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
        attachments.apply_refs(ctx, tx, fid, (), e.attachment_ids, env.now)
        _bump(tx, fid, months)
        _audit(ctx, tx, fid, actor, "entry.create", e)
        return Built(tx, env.view(e), [{"object_type": "entry", "object_id": eid, "version": 1}])

    return actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "entry.create",
        body,
        build,
        _replay_entry(ctx, actor, fid),
    )


def preview_entry(ctx: AppContext, actor: Actor, fid: str, data: EntryInput) -> dict[str, Any]:
    """提交前折算预览，不写入。"""
    env = _Env(ctx, actor, fid)
    original = None
    if data.type == "refund" and data.refund_of:
        original, _ = _refund_target(ctx, fid, data.refund_of)
    plan = plan_create(
        data,
        catalog=env.catalog,
        currencies=env.currencies,
        today=env.today,
        limits=ctx.limits,
        original=original,
    )
    return _preview(ctx, fid, plan, env, snapshot=None)


def preview_update(
    ctx: AppContext, actor: Actor, fid: str, eid: str, patch: dict[str, Any]
) -> dict[str, Any]:
    """修改前折算预览：显示是否更换快照（需求 4.3）。不写入。"""
    env = _Env(ctx, actor, fid)
    old, _ = repo.get_entry(ctx, fid, eid)
    require_can_modify_entry(env.m, old.created_by)
    body = {k: v for k, v in patch.items() if k != "expected_version"}
    original = None
    if old.type == "refund" and old.refund_of:
        original, _ = _refund_target(ctx, fid, old.refund_of)
    elif body.get("type") == "refund" and body.get("refund_of"):
        original, _ = _refund_target(ctx, fid, body["refund_of"])
    plan = plan_update(
        old,
        body,
        catalog=env.catalog,
        currencies=env.currencies,
        today=env.today,
        limits=ctx.limits,
        new_original=original,
    )
    out = _preview(ctx, fid, plan, env, snapshot=plan.keep_snapshot)
    if out["status"] == "ok":
        out["snapshot_changed"] = plan.keep_snapshot is None
    return out


def _preview(ctx: AppContext, fid: str, plan: Any, env: _Env, snapshot: Any) -> dict[str, Any]:
    from ledger.domain.errors import RatePending
    from ledger.domain.money import format_minor

    try:
        snap = snapshot or repo.resolve_snapshot(ctx, fid, plan.business_date, plan.currency)
    except RatePending as e:
        return {"status": "rate_pending", **(e.current or {})}
    nzd, cny = finalize(plan, snap, env.currencies)
    digits = env.currencies[plan.currency].minor_digits
    return {
        "status": "ok",
        "snapshot": snap.to_dict(),
        "display_amounts": {
            "amount": format_minor(plan.amount_minor, digits),
            "nzd": format_minor(nzd, 2),
            "cny": format_minor(cny, 2),
        },
    }


# ── 修改（T2／T3） ──


def update_entry(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    eid: str,
    key: str,
    patch: dict[str, Any],
    expected_version: int,
) -> dict[str, Any]:
    body = {"eid": eid, "patch": patch, "v": expected_version}

    def build() -> Built[dict[str, Any]]:
        env = _Env(ctx, actor, fid)
        old, old_sk = repo.get_entry(ctx, fid, eid)
        require_can_modify_entry(env.m, old.created_by)
        if old.version != expected_version:
            raise DomainError("账目已被他人修改", code="version_conflict", current=env.view(old))
        original: Entry | None = None
        orig_sk = ""
        if old.type == "refund" and old.refund_of:
            original, orig_sk = _refund_target(ctx, fid, old.refund_of)
        elif patch.get("type") == "refund" and patch.get("refund_of"):
            original, orig_sk = _refund_target(ctx, fid, patch["refund_of"])
        plan = plan_update(
            old,
            patch,
            catalog=env.catalog,
            currencies=env.currencies,
            today=env.today,
            limits=ctx.limits,
            new_original=original,
        )
        snap = plan.keep_snapshot or repo.resolve_snapshot(
            ctx, fid, plan.business_date, plan.currency
        )
        nzd, cny = finalize(plan, snap, env.currencies)
        new = apply_plan(old, plan, snap, nzd, cny, env.now)
        new_sk = entry_sk(new)

        tx = ctx.store.tx()
        guard_member(tx, actor, env.m)
        _check_manifest(tx, fid, env.catalog)
        if new.type != "refund" and ("leaf_category_id" in patch or new.type != old.type):
            _check_leaf_active(tx, fid, new.leaf_category_id)
        months = {old.month, new.month}
        if new_sk == old_sk:
            _put_same(tx, new, old_sk, old.version)
        else:
            _move(tx, old_sk, new, new_sk, old.version, state="active")

        # 退款额度联动
        if original is not None:
            delta = new.amount_minor - (old.amount_minor if old.type == "refund" else 0)
            ids = original.refund_ids if old.type == "refund" else (*original.refund_ids, eid)
            new_orig = replace(
                original,
                refunded_minor=original.refunded_minor + delta,
                refund_ids=ids,
                version=original.version + 1,
            )
            _put_same(tx, new_orig, orig_sk, original.version)
            months.add(original.month)
        # 原消费改分类或方式：关联退款随之更新（ADR-0006）
        if (
            old.type == "expense"
            and new.type == "expense"
            and old.refund_ids
            and (
                new.leaf_category_id != old.leaf_category_id
                or new.payment_method != old.payment_method
            )
        ):
            for rid in old.refund_ids:
                r, r_sk = repo.get_entry(ctx, fid, rid)
                r2 = replace(
                    r,
                    leaf_category_id=new.leaf_category_id,
                    payment_method=new.payment_method,
                    version=r.version + 1,
                    updated_at=env.now,
                )
                _put_same(tx, r2, r_sk, r.version)
                months.add(r.month)
        if new.attachment_ids != old.attachment_ids:
            attachments.apply_refs(ctx, tx, fid, old.attachment_ids, new.attachment_ids, env.now)
        _bump(tx, fid, months)
        _audit(ctx, tx, fid, actor, "entry.update", new, sorted(plan.changed_fields))
        return Built(
            tx, env.view(new), [{"object_type": "entry", "object_id": eid, "version": new.version}]
        )

    return actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "entry.update",
        body,
        build,
        _replay_entry(ctx, actor, fid),
    )


# ── 删除（T5） ──


def delete_entry(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    eid: str,
    key: str,
    expected_version: int,
    *,
    with_refunds: bool = False,
) -> dict[str, Any]:
    body = {"eid": eid, "v": expected_version, "with_refunds": with_refunds}

    def build() -> Built[dict[str, Any]]:
        env = _Env(ctx, actor, fid)
        old, old_sk = repo.get_entry(ctx, fid, eid)
        if old.state != "active":
            raise ValidationFailed("该账目已在回收站")
        require_can_modify_entry(env.m, old.created_by)
        if old.version != expected_version:
            raise DomainError("账目已被他人修改", code="version_conflict", current=env.view(old))
        if old.refund_ids and not with_refunds:
            raise DomainError(
                "该消费有关联退款，需一并删除",
                code="has_linked_refunds",
                current={"refund_ids": list(old.refund_ids)},
            )
        tx = ctx.store.tx()
        guard_member(tx, actor, env.m)
        months = {old.month}
        also: list[str] = []
        for rid in old.refund_ids:
            r, r_sk = repo.get_entry(ctx, fid, rid)
            require_can_modify_entry(env.m, r.created_by)
            rt = trashed(r, actor.user_id, env.now)
            _move(tx, r_sk, rt, keys.trash(rid), r.version, state="trashed", extra=_trash_extra(rt))
            months.add(r.month)
            also.append(rid)
        gone = trashed(old, actor.user_id, env.now)
        if old.refund_ids:
            # 退款已一并移入回收站；恢复原消费不自动恢复退款
            gone = replace(gone, refund_ids=(), refunded_minor=0)
        _move(
            tx,
            old_sk,
            gone,
            keys.trash(eid),
            old.version,
            state="trashed",
            extra=_trash_extra(gone),
        )
        if old.type == "refund" and old.refund_of:
            original, orig_sk = _refund_target(ctx, fid, old.refund_of)
            new_orig = replace(
                original,
                refunded_minor=original.refunded_minor - old.amount_minor,
                refund_ids=tuple(i for i in original.refund_ids if i != eid),
                version=original.version + 1,
            )
            _put_same(tx, new_orig, orig_sk, original.version)
            months.add(original.month)
        _bump(tx, fid, months)
        _audit(ctx, tx, fid, actor, "entry.delete", gone)
        assert gone.purge_after is not None
        result = {
            "entry_id": eid,
            "state": "trashed",
            "purge_after": keys.ts(gone.purge_after),
            "version": gone.version,
            "also_trashed": also,
        }
        return Built(
            tx, result, [{"object_type": "entry", "object_id": eid, "version": gone.version}]
        )

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        item, _ = repo.get_entry_item(ctx, fid, eid)
        e = entry_from(item)
        return {
            "entry_id": eid,
            "state": e.state,
            "purge_after": keys.ts(e.purge_after) if e.purge_after else None,
            "version": e.version,
            "also_trashed": [],
        }

    return actions.run(
        ctx, actor, Scope.family(fid, actor, key), key, "entry.delete", body, build, replay
    )


# ── 恢复（T6） ──


def restore_entry(
    ctx: AppContext, actor: Actor, fid: str, eid: str, key: str, expected_version: int
) -> dict[str, Any]:
    body = {"eid": eid, "v": expected_version}

    def build() -> Built[dict[str, Any]]:
        env = _Env(ctx, actor, fid)
        loc = repo.locate(ctx, fid, eid)
        if loc is None:
            raise NotFound("账目不存在")
        if loc.get("state") == "purged":
            raise DomainError("已超过 30 天，无法恢复", code="purged")
        if loc.get("state") != "trashed":
            raise ValidationFailed("该账目不在回收站")
        item = ctx.store.get(keys.family(fid), loc["sk"])
        if item is None:
            raise StaleRead("账目状态变化")
        e = entry_from(item)
        require_can_modify_entry(env.m, e.created_by)
        if e.version != expected_version:
            raise DomainError("账目已被他人修改", code="version_conflict", current=env.view(e))
        back = restored(e, env.now)
        tx = ctx.store.tx()
        guard_member(tx, actor, env.m)
        months = {back.month}
        if back.type == "refund" and back.refund_of:
            original, orig_sk = _refund_target(ctx, fid, back.refund_of)
            check_refund(original, back.amount_minor, back.currency)
            new_orig = replace(
                original,
                refunded_minor=original.refunded_minor + back.amount_minor,
                refund_ids=(*original.refund_ids, eid),
                version=original.version + 1,
            )
            _put_same(tx, new_orig, orig_sk, original.version)
            months.add(original.month)
        _move(tx, loc["sk"], back, entry_sk(back), e.version, state="active")
        _bump(tx, fid, months)
        _audit(ctx, tx, fid, actor, "entry.restore", back)
        return Built(
            tx,
            env.view(back),
            [{"object_type": "entry", "object_id": eid, "version": back.version}],
        )

    return actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "entry.restore",
        body,
        build,
        _replay_entry(ctx, actor, fid),
    )


# ── 动作回执查询 ──


def get_action(ctx: AppContext, actor: Actor, fid: str, action_id: str) -> dict[str, Any]:
    require_member(load_membership(ctx, actor, fid))
    r = actions.get_receipt(ctx, Scope.family(fid, actor, action_id))
    if r is None:
        raise DomainError("该动作尚未提交", code="action_not_found")
    results = []
    for res in r.get("results", []):
        row = dict(res)
        if res.get("object_type") == "entry":
            loc = repo.locate(ctx, fid, res["object_id"])
            if loc is not None and loc.get("state") == "purged":
                row["purged"] = True
        results.append(row)
    return {
        "action_id": action_id,
        "kind": r["kind"],
        "status": r["status"],
        "committed_at": r["committed_at"],
        "results": results,
    }


# ── 维护：回收站到期清理（ACC-16／OPS-11） ──


def purge_due(ctx: AppContext, now: datetime | None = None) -> int:
    """清理到期回收站项：先写删除日志，再删除正文；定位器保留 purged 标记。"""
    now = now or ctx.clock()
    purged = 0
    for shard in range(PURGE_SHARDS):
        r = ctx.store.client.query(
            TableName=ctx.store.table,
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND GSI1SK <= :now",
            ExpressionAttributeValues={
                ":pk": {"S": keys.work("trash_purge", shard)},
                ":now": {"S": keys.ts(now) + "~"},
            },
        )
        for raw in r.get("Items", []):
            pk, sk = raw["PK"]["S"], raw["SK"]["S"]
            item = ctx.store.get(pk, sk)
            if item is None:
                continue
            e = entry_from(item)
            if e.purge_after is None or e.purge_after > now:
                continue
            tx = ctx.store.tx()
            journal = Tx(ctx.store.journal_table)
            journal.put(
                {
                    "PK": pk,
                    "SK": f"PURGED#{e.entry_id}",
                    "type": "deletion",
                    "entry_id": e.entry_id,
                    "purged_at": keys.ts(now),
                    "attachment_ids": list(e.attachment_ids),
                }
            )
            tx.items.extend(journal.items)
            tx.delete(pk, sk, condition="version = :v", values={":v": e.version})
            fid = pk.removeprefix("FAMILY#")
            attachments.apply_refs(
                ctx, tx, fid, e.attachment_ids, (), now, orphan_delay=timedelta(0)
            )
            tx.update(
                pk,
                keys.locator(e.entry_id),
                "SET #st = :p REMOVE sk",
                condition="sk = :sk",
                names={"#st": "state"},
                values={":p": "purged", ":sk": sk},
            )
            try:
                ctx.store.commit(tx)
                purged += 1
            except (DomainError, StaleRead):
                continue  # 期间被恢复或修改：下次再判断
    return purged

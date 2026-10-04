"""币种与汇率用例（需求 §7、ADR-0004）。"""

from __future__ import annotations

from datetime import date
from typing import Any

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.codec import currency_item, provider_set_item
from ledger.domain.authz import Actor, require_family_admin, require_member, require_system_admin
from ledger.domain.errors import DomainError, StaleRead, ValidationFailed
from ledger.domain.fx import (
    ProviderRateSet,
    RatePendingResult,
    decimal_str,
    resolve,
)
from ledger.domain.money import CurrencyMeta, parse_rate

from . import actions, repo
from .actions import Built, Scope
from .context import AppContext
from .guard import guard_member, load_membership, load_profile


def _cur_view(c: CurrencyMeta) -> dict[str, Any]:
    return {
        "code": c.code,
        "name_zh": c.name_zh,
        "minor_digits": c.minor_digits,
        "provider_supported": c.provider_supported,
    }


def list_global_currencies(ctx: AppContext, actor: Actor) -> list[dict[str, Any]]:
    load_profile(ctx, actor)
    return [_cur_view(c) for c in repo.global_currencies(ctx).values()]


def list_family_currencies(ctx: AppContext, actor: Actor, fid: str) -> list[dict[str, Any]]:
    require_member(load_membership(ctx, actor, fid))
    return [_cur_view(c) for c in repo.family_currencies(ctx, fid).values()]


def create_global_currency(
    ctx: AppContext, actor: Actor, key: str, meta: CurrencyMeta
) -> dict[str, Any]:
    require_system_admin(actor)
    if meta.minor_digits not in (0, 2, 3) or len(meta.code) != 3 or not meta.code.isupper():
        raise ValidationFailed("币种元数据无效")

    def build() -> Built[dict[str, Any]]:
        load_profile(ctx, actor)
        tx = ctx.store.tx()
        tx.put_new(
            currency_item(meta),
            on_fail=lambda _: DomainError("币种已存在", code="version_conflict"),
        )
        return Built(tx, _cur_view(meta), [{"object_type": "currency", "object_id": meta.code}])

    return actions.run(
        ctx,
        actor,
        Scope.account(actor, key),
        key,
        "currency.create",
        _cur_view(meta),
        build,
        lambda r: _cur_view(meta),
    )


def enable_family_currency(
    ctx: AppContext, actor: Actor, fid: str, key: str, code: str
) -> dict[str, Any]:
    def build() -> Built[dict[str, Any]]:
        m = require_family_admin(load_membership(ctx, actor, fid))
        meta = repo.global_currencies(ctx).get(code)
        if meta is None:
            raise ValidationFailed("币种不存在，请联系系统管理员添加")
        tx = ctx.store.tx()
        guard_member(tx, actor, m)
        tx.put_new(
            {
                "PK": keys.family(fid),
                "SK": keys.family_currency(code),
                "type": "family_currency",
                "code": code,
                "enabled_at": keys.ts(ctx.clock()),
                "enabled_by": actor.user_id,
            },
            on_fail=lambda _: ValidationFailed("该币种已启用"),
        )
        return Built(tx, _cur_view(meta), [{"object_type": "currency", "object_id": code}])

    return actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "currency.enable",
        {"code": code},
        build,
        lambda r: _cur_view(repo.global_currencies(ctx)[code]),
    )


def resolve_rates(
    ctx: AppContext, actor: Actor, fid: str, d: date, currencies: list[str] | None = None
) -> dict[str, Any]:
    require_member(load_membership(ctx, actor, fid))
    codes = tuple(sorted(set(currencies or repo.family_currencies(ctx, fid)) | {"NZD", "CNY"}))
    provider, manual = repo.rate_inputs(ctx, fid, d)
    out = resolve(d, codes, provider, manual, provider=ctx.provider)
    if isinstance(out, RatePendingResult):
        return {
            "status": "rate_pending",
            "missing_currencies": list(out.missing_currencies),
            "searched_from": out.searched_from.isoformat(),
            "searched_to": out.searched_to.isoformat(),
        }
    snap = out.to_dict()
    return {
        "status": "ok",
        "requested_date": snap["requested_date"],
        "effective_date": snap["effective_date"],
        "rates": snap["rates"],
    }


def put_manual_rate(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    key: str,
    *,
    d: date,
    currency: str,
    usd_value: str,
    reason: str,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """家庭补录／修正：只影响本家庭，且不改写已入账快照（FX-04）。"""
    value = parse_rate(usd_value)
    if currency == "USD":
        raise ValidationFailed("USD 为基准币种，恒为 1")
    if not 1 <= len(reason) <= 200:
        raise ValidationFailed("请填写原因（1–200 字）")
    body = {"d": d.isoformat(), "c": currency, "v": usd_value, "r": reason, "e": expected_revision}

    def build() -> Built[dict[str, Any]]:
        m = require_family_admin(load_membership(ctx, actor, fid))
        if currency not in repo.family_currencies(ctx, fid):
            raise ValidationFailed("币种未在本家庭启用")
        sk = keys.family_rate(d, currency)
        existing = ctx.store.get(keys.family(fid), sk)
        if existing is not None and expected_revision != int(existing["revision"]):
            raise DomainError(
                "该汇率已被修改",
                code="version_conflict",
                current={"revision": int(existing["revision"])},
            )
        if existing is None and expected_revision is not None:
            raise DomainError("该汇率不存在", code="version_conflict")
        revision = 1 if existing is None else int(existing["revision"]) + 1
        now = keys.ts(ctx.clock())
        item = {
            "PK": keys.family(fid),
            "SK": sk,
            "type": "manual_rate",
            "date": d.isoformat(),
            "currency": currency,
            "usd_value": decimal_str(value),
            "revision": revision,
            "entered_by": actor.user_id,
            "reason": reason,
            "entered_at": now,
        }
        tx = ctx.store.tx()
        guard_member(tx, actor, m)
        if existing is None:
            tx.put_new(item, on_fail=lambda _: StaleRead("汇率已变化"))
        else:
            tx.put(
                item,
                condition="revision = :r",
                values={":r": existing["revision"]},
                on_fail=lambda _: StaleRead("汇率已变化"),
            )
        view = {
            k: item[k]
            for k in (
                "date",
                "currency",
                "usd_value",
                "revision",
                "entered_by",
                "reason",
                "entered_at",
            )
        }
        return Built(
            tx, view, [{"object_type": "manual_rate", "object_id": sk, "version": revision}]
        )

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        item = ctx.store.get(keys.family(fid), r["results"][0]["object_id"]) or {}
        return {
            k: item.get(k)
            for k in (
                "date",
                "currency",
                "usd_value",
                "revision",
                "entered_by",
                "reason",
                "entered_at",
            )
        }

    return actions.run(
        ctx, actor, Scope.family(fid, actor, key), key, "rate.manual", body, build, replay
    )


def store_provider_set(ctx: AppContext, rate_set: ProviderRateSet) -> bool:
    """保存供应商汇率组。已存在则不覆盖（历史组不因再次抓取而变化）。返回是否新写入。"""
    from ledger.adapters.dynamo.store import Tx

    tx = Tx(ctx.store.table)
    tx.put_new(provider_set_item(rate_set))
    try:
        ctx.store.commit(tx)
        return True
    except DomainError:
        return False


def sync_provider(ctx: AppContext, client: Any, start: date, end: date) -> int:
    """抓取 [start, end] 的供应商汇率组并保存；已存在的日期不覆盖。返回新写入组数。"""
    codes = [c.code for c in repo.global_currencies(ctx).values() if c.provider_supported]
    return sum(store_provider_set(ctx, s) for s in client.fetch(start, end, codes))


def list_manual_rates(
    ctx: AppContext, actor: Actor, fid: str, start: date, end: date
) -> list[dict[str, Any]]:
    from ledger.adapters.dynamo.store import query_between

    require_member(load_membership(ctx, actor, fid))
    if end < start or (end - start).days > 366:
        raise ValidationFailed("日期范围无效（最长一年）")
    items = query_between(
        ctx.store, keys.family(fid), f"RATE#{start.isoformat()}#", f"RATE#{end.isoformat()}#~"
    )
    return [
        {
            k: i[k]
            for k in (
                "date",
                "currency",
                "usd_value",
                "revision",
                "entered_by",
                "reason",
                "entered_at",
            )
        }
        for i in items
    ]

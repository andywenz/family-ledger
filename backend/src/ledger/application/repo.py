"""读取辅助：所有业务判断前的权威读取均为强一致。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.codec import (
    category_from,
    currency_from,
    entry_from,
    manual_rate_from,
    provider_set_from,
)
from ledger.adapters.dynamo.store import query_between
from ledger.domain.categories import Catalog
from ledger.domain.entries import Entry
from ledger.domain.errors import NotFound, RatePending
from ledger.domain.fx import (
    DEFAULT_LOOKBACK_DAYS,
    FxSnapshot,
    ManualRate,
    ProviderRateSet,
    RatePendingResult,
    required_currencies,
    resolve,
)
from ledger.domain.money import CurrencyMeta

from .context import AppContext


def family_config(ctx: AppContext, fid: str) -> dict[str, Any]:
    cfg = ctx.store.get(keys.family(fid), keys.CONFIG)
    if cfg is None:
        raise NotFound("家庭不存在")
    return cfg


def family_today(cfg: dict[str, Any], now: datetime) -> date:
    return now.astimezone(ZoneInfo(cfg.get("timezone", "Pacific/Auckland"))).date()


def catalog(ctx: AppContext, fid: str) -> Catalog:
    cfg = family_config(ctx, fid)
    items = ctx.store.query_all(keys.family(fid), "CAT#")
    return Catalog((category_from(i) for i in items), int(cfg["category_manifest_version"]))


def global_currencies(ctx: AppContext) -> dict[str, CurrencyMeta]:
    return {c.code: c for c in map(currency_from, ctx.store.query_all(keys.CURRENCY_PK, ""))}


def family_currencies(ctx: AppContext, fid: str) -> dict[str, CurrencyMeta]:
    meta = global_currencies(ctx)
    enabled = [i["code"] for i in ctx.store.query_all(keys.family(fid), "CUR#")]
    return {c: meta[c] for c in enabled if c in meta}


def locate(ctx: AppContext, fid: str, eid: str) -> dict[str, Any] | None:
    return ctx.store.get(keys.family(fid), keys.locator(eid))


def get_entry_item(ctx: AppContext, fid: str, eid: str) -> tuple[dict[str, Any], str]:
    """按定位器读取账目项，返回 (项, SK)。定位与读取之间若被移动则重读一次。"""
    for _ in range(2):
        loc = locate(ctx, fid, eid)
        if loc is None or loc.get("state") == "purged":
            raise NotFound("账目不存在")
        item = ctx.store.get(keys.family(fid), loc["sk"])
        if item is not None:
            return item, loc["sk"]
    raise NotFound("账目不存在")


def get_entry(ctx: AppContext, fid: str, eid: str) -> tuple[Entry, str]:
    item, sk = get_entry_item(ctx, fid, eid)
    return entry_from(item), sk


def month_entries(ctx: AppContext, fid: str, month: str) -> list[Entry]:
    items = ctx.store.query_all(keys.family(fid), keys.entry_prefix(month), descending=True)
    return [entry_from(i) for i in items]


def month_version(ctx: AppContext, fid: str, month: str) -> int:
    item = ctx.store.get(keys.family(fid), keys.month_version(month))
    return int(item["version"]) if item else 0


def rate_inputs(
    ctx: AppContext, fid: str, requested: date, lookback: int = DEFAULT_LOOKBACK_DAYS
) -> tuple[dict[date, ProviderRateSet], dict[tuple[date, str], ManualRate]]:
    start = requested - timedelta(days=lookback)
    provider_items = query_between(
        ctx.store, keys.provider_rates(ctx.provider), start.isoformat(), requested.isoformat()
    )
    provider = {s.effective_date: s for s in map(provider_set_from, provider_items)}
    manual_items = query_between(
        ctx.store,
        keys.family(fid),
        f"RATE#{start.isoformat()}#",
        f"RATE#{requested.isoformat()}#~",
    )
    manual = {(m.effective_date, m.currency): m for m in map(manual_rate_from, manual_items)}
    return provider, manual


def resolve_snapshot(ctx: AppContext, fid: str, requested: date, currency: str) -> FxSnapshot:
    provider, manual = rate_inputs(ctx, fid, requested)
    out = resolve(requested, required_currencies(currency), provider, manual, provider=ctx.provider)
    if isinstance(out, RatePendingResult):
        raise RatePending(
            "缺少该日期的汇率，请管理员补录",
            current={
                "missing_currencies": list(out.missing_currencies),
                "searched_from": out.searched_from.isoformat(),
                "searched_to": out.searched_to.isoformat(),
            },
        )
    return out


def display_names(ctx: AppContext, fid: str) -> dict[str, tuple[str, bool]]:
    """user_id → (显示名, 是否已离开)。离开成员仍显示其显示名（ADR-0006）。"""
    out: dict[str, tuple[str, bool]] = {}
    for m in ctx.store.query_all(keys.family(fid), "MEMBER#"):
        p = ctx.store.get(keys.user(m["user_id"]), keys.PROFILE)
        name = p.get("display_name", "") if p else ""
        out[m["user_id"]] = (name, m.get("status") != "active")
    return out

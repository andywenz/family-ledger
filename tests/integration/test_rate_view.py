"""查看汇率组：个别币种缺汇率（如 ECB 不发布的 MOP）时仍显示其余币种（离线）。"""

from __future__ import annotations

from datetime import date

from ledger.application import rates
from ledger.domain.money import CurrencyMeta

from .conftest import World, new_key
from .helpers import ensure_currency


def _enable_unsupported(w: World) -> None:
    """XTS 没有任何数据源汇率，模拟生产中的 MOP。"""
    if "XTS" not in {c["code"] for c in rates.list_global_currencies(w.ctx, w.admin)}:
        rates.create_global_currency(w.ctx, w.admin, new_key(), CurrencyMeta("XTS", 2, "测试币"))
    ensure_currency(w, "XTS")


def test_family_view_lists_available_rates_and_missing(world: World) -> None:
    w = world
    _enable_unsupported(w)
    d = w.ctx.clock.now.date()
    out = rates.resolve_rates(w.ctx, w.member, w.fid, d)
    assert out["status"] == "ok"
    assert "XTS" in out["missing_currencies"]  # 其他无数据源汇率的币种（如 FJD）也会列出
    assert {"NZD", "CNY", "USD"} <= set(out["rates"]) and "XTS" not in out["rates"]
    # 补录在有数据源发布的日期（10-04 是周日，取 10-02 的组）后整组完整
    rates.put_manual_rate(
        w.ctx,
        w.admin,
        w.fid,
        new_key(),
        d=date(2026, 10, 2),
        currency="XTS",
        usd_value="0.5",
        reason="测试补录",
    )
    out = rates.resolve_rates(w.ctx, w.member, w.fid, d)
    assert "XTS" not in out["missing_currencies"]
    assert out["rates"]["XTS"]["source"] == "family_manual"


def test_explicit_currencies_stay_strict(world: World) -> None:
    """显式指定币种（入账等调用）时保持原语义：缺即 rate_pending。"""
    w = world
    _enable_unsupported(w)
    out = rates.resolve_rates(w.ctx, w.member, w.fid, w.ctx.clock.now.date(), ["XTS"])
    assert out["status"] == "rate_pending" and "XTS" in out["missing_currencies"]


def test_new_family_enables_all_global_currencies(world: World) -> None:
    """新家庭启用全部全局币种（与已有家庭一致）；之后新增的全局币种也会出现在新家庭中。"""
    w = world
    global_codes = {c["code"] for c in rates.list_global_currencies(w.ctx, w.admin)}
    family_codes = {c["code"] for c in rates.list_family_currencies(w.ctx, w.admin, w.fid)}
    assert family_codes == global_codes

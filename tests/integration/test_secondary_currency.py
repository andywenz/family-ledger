"""辅助币种（家庭设置，可选）：月度总览净支出、消费方式、明细的第二币种（离线）。"""

from __future__ import annotations

import pytest

from ledger.application import entries, families, rates
from ledger.domain.errors import ValidationFailed
from ledger.domain.money import CurrencyMeta

from .conftest import World, new_key
from .helpers import expense

MONTH = "2026-10"


def _set(w: World, value: str | None) -> dict:
    cfg = families.get_config(w.ctx, w.admin, w.fid)
    return families.update_config(
        w.ctx, w.admin, w.fid, new_key(), expected_version=cfg["version"], secondary_currency=value
    )


def test_default_is_cny_and_matches_existing_display(world: World) -> None:
    w = world
    expense(w, amount="100")
    assert families.get_config(w.ctx, w.admin, w.fid)["secondary_currency"] == "CNY"
    d = entries.dashboard(w.ctx, w.admin, w.fid, MONTH)
    assert d["secondary"]["currency"] == "CNY"
    assert (
        d["secondary"]["net_expense"] == d["totals"]["net_expense"]["cny"]
    )  # 与原“合 CNY”完全一致
    item = entries.list_entries(w.ctx, w.admin, w.fid, MONTH)["items"][0]
    assert item["secondary"] == {"currency": "CNY", "amount": item["display_amounts"]["cny"]}


def test_other_currency_converted_per_entry(world: World) -> None:
    """NZD 100 按当日汇率（NZD 0.56、AUD 0.65 USD）≈ AUD 86.15；逐笔四舍五入后汇总。"""
    w = world
    expense(w, amount="100", method="credit_card")
    expense(w, amount="10", method="cash")
    assert _set(w, "AUD")["secondary_currency"] == "AUD"
    d = entries.dashboard(w.ctx, w.admin, w.fid, MONTH)
    sec = d["secondary"]
    assert (sec["currency"], sec["net_expense"], sec["missing_count"]) == ("AUD", "94.77", 0)
    by = {m["payment_method"]: m["net"] for m in sec["by_payment_method"]}
    assert by["credit_card"] == "86.15" and by["cash"] == "8.62" and by["debit_card"] == "0.00"
    amounts = sorted(
        i["secondary"]["amount"]
        for i in entries.list_entries(w.ctx, w.admin, w.fid, MONTH)["items"]
    )
    assert amounts == ["8.62", "86.15"]
    # 原币即辅助币种时保持原金额
    expense(w, amount="20", currency="AUD")
    items = entries.list_entries(w.ctx, w.admin, w.fid, MONTH)["items"]
    assert any(i["currency"] == "AUD" and i["secondary"]["amount"] == "20.00" for i in items)


def test_none_hides_secondary(world: World) -> None:
    w = world
    expense(w)
    assert _set(w, None)["secondary_currency"] is None
    assert entries.dashboard(w.ctx, w.admin, w.fid, MONTH)["secondary"] is None
    assert entries.list_entries(w.ctx, w.admin, w.fid, MONTH)["items"][0]["secondary"] is None
    assert _set(w, "CNY")["secondary_currency"] == "CNY"  # 可以再开回来


@pytest.mark.parametrize("code", ["NZD", "JPY"])
def test_rejects_primary_and_not_enabled(world: World, code: str) -> None:
    with pytest.raises(ValidationFailed):
        _set(world, code)


def test_missing_rate_is_counted_not_zero(world: World) -> None:
    """辅助币种当日无汇率：该笔不计入合计并计数，明细显示为空，不按 0 处理。"""
    w = world
    if "XTS" not in {c["code"] for c in rates.list_global_currencies(w.ctx, w.admin)}:
        rates.create_global_currency(w.ctx, w.admin, new_key(), CurrencyMeta("XTS", 2, "测试币"))
    rates.enable_family_currency(w.ctx, w.admin, w.fid, new_key(), "XTS")
    expense(w, amount="100")
    _set(w, "XTS")
    sec = entries.dashboard(w.ctx, w.admin, w.fid, MONTH)["secondary"]
    assert (sec["net_expense"], sec["missing_count"]) == ("0.00", 1)
    assert entries.list_entries(w.ctx, w.admin, w.fid, MONTH)["items"][0]["secondary"] is None


def _cfg(w: World, **kw: object) -> dict:
    cfg = families.get_config(w.ctx, w.admin, w.fid)
    return families.update_config(
        w.ctx, w.admin, w.fid, new_key(), expected_version=cfg["version"], **kw
    )


def test_primary_follows_default_currency(world: World) -> None:
    """主币种＝默认币种：净支出、消费方式、饼图、明细第一个“合 xxx”均按当日汇率折算。"""
    w = world
    expense(w, amount="100", method="credit_card")  # NZD 100 → AUD 86.15
    expense(w, amount="10", method="cash", leaf="expense-03-01")  # → AUD 8.62
    _cfg(w, default_currency="AUD", secondary_currency="NZD")
    d = entries.dashboard(w.ctx, w.admin, w.fid, MONTH)
    p, s = d["primary"], d["secondary"]
    assert (p["currency"], p["net_expense"], p["expense_gross"], p["refund"]) == (
        "AUD",
        "94.77",
        "94.77",
        "0.00",
    )
    assert {m["payment_method"]: m["net"] for m in p["by_payment_method"]}["credit_card"] == "86.15"
    pie = p["category_pie"]
    assert pie["currency"] == "AUD" and pie["denominator"] == "94.77"
    assert [sl["net"] for sl in pie["slices"]] == ["86.15", "8.62"]
    # 辅助币种 NZD：直接用入账快照的合 NZD
    assert (s["currency"], s["net_expense"]) == ("NZD", "110.00")
    item = next(
        i
        for i in entries.list_entries(w.ctx, w.admin, w.fid, MONTH)["items"]
        if i["amount"] == "100.00"
    )
    assert item["primary"] == {"currency": "AUD", "amount": "86.15"}
    assert item["secondary"] == {"currency": "NZD", "amount": "100.00"}


def test_secondary_hidden_when_same_as_default(world: World) -> None:
    """只改默认币种为 CNY（辅助币种仍是旧默认 CNY）：允许，辅助币种暂不显示；明确设成相同则拒绝。"""
    w = world
    expense(w)
    _cfg(w, default_currency="CNY")
    d = entries.dashboard(w.ctx, w.admin, w.fid, MONTH)
    assert d["primary"]["currency"] == "CNY" and d["secondary"] is None
    with pytest.raises(ValidationFailed):
        _cfg(w, secondary_currency="CNY")

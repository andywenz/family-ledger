"""ACC-01／02／04／06／08／10、FX-01／02、OPS-01：创建、统计与幂等（离线，DynamoDB Local）。"""

from __future__ import annotations

import pytest

from ledger.application import entries
from ledger.domain.entries import EntryInput
from ledger.domain.errors import DomainError

from .conftest import World, new_key, raw_items
from .helpers import expense, income, refund


def test_acc01_single_entry_with_receipt_and_locator(world: World) -> None:
    w = world
    key = new_key()
    e = expense(w, amount="45.5", key=key)
    assert e["created_by"] == w.admin.user_id and e["family_id"] == w.fid
    assert e["display_amounts"] == {"amount": "45.50", "nzd": "45.50", "cny": "169.87"}
    # 独立读回：恰好一条账目、一条定位、一条回执
    rows = raw_items(w.ctx, w.fid, "ENTRY#2026-10#")
    assert [r["entry_id"] for r in rows] == [e["entry_id"]]
    assert len(raw_items(w.ctx, w.fid, f"ENTRYLOC#{e['entry_id']}")) == 1
    receipts = raw_items(w.ctx, w.fid, f"ACTION#{w.admin.user_id}#{key}")
    assert receipts[0]["results"][0]["object_id"] == e["entry_id"]
    d = entries.dashboard(w.ctx, w.admin, w.fid, "2026-10")
    assert d["entry_count"] == 1 and d["totals"]["net_expense"]["nzd"] == "45.50"


def test_acc02_example_amounts_persisted(world: World) -> None:
    e = expense(world, amount="5")
    assert (e["nzd_minor"], e["cny_minor"]) == (500, 1867)
    assert e["fx_snapshot"]["rates"]["NZD"]["usd_value"] == "0.56"


def test_fx01_weekend_uses_friday_snapshot(world: World) -> None:
    e = expense(world, date="2026-10-04", currency="AUD", amount="10")
    assert e["fx_snapshot"]["requested_date"] == "2026-10-04"
    assert e["fx_snapshot"]["effective_date"] == "2026-10-02"


def test_fx02_missing_rate_rejects_without_writing(world: World) -> None:
    w = world
    from ledger.application import rates

    rates.enable_family_currency(w.ctx, w.admin, w.fid, new_key(), "FJD")
    with pytest.raises(DomainError) as ex:
        expense(w, currency="FJD", amount="10")
    assert ex.value.code == "rate_pending"
    assert raw_items(w.ctx, w.fid, "ENTRY#") == []
    rates.put_manual_rate(
        w.ctx,
        w.admin,
        w.fid,
        new_key(),
        d=__import__("datetime").date(2026, 10, 2),
        currency="FJD",
        usd_value="0.45",
        reason="供应商不覆盖",
    )
    e = expense(w, currency="FJD", amount="10")
    assert e["fx_snapshot"]["manual"] is True
    assert e["fx_snapshot"]["rates"]["FJD"]["source"] == "family_manual"


def test_acc06_acc08_totals_with_refund_income_and_non_stat(world: World) -> None:
    w = world
    e = expense(w, amount="100")
    refund(w, e["entry_id"], "20")
    income(w, "50")
    entries.create_entry(
        w.ctx,
        w.admin,
        w.fid,
        new_key(),
        EntryInput(
            business_date="2026-10-02",
            type="internal_transfer",
            amount="999",
            currency="NZD",
            leaf_category_id="transfer-01-01",
        ),
    )
    d = entries.dashboard(w.ctx, w.admin, w.fid, "2026-10")
    t = d["totals"]
    assert (t["expense_gross"]["nzd"], t["refund"]["nzd"], t["net_expense"]["nzd"]) == (
        "100.00",
        "20.00",
        "80.00",
    )
    assert (t["income"]["nzd"], t["balance"]["nzd"]) == ("50.00", "-30.00")
    # 逐笔舍入后累加：373.33 − 74.67 = 298.66（不是 80 NZD 重新折算的 298.67，需求 §7）
    assert t["net_expense"]["cny"] == "298.66" and d["entry_count"] == 4
    listed = entries.list_entries(w.ctx, w.admin, w.fid, "2026-10")["items"]
    transfer = next(i for i in listed if i["type"] == "internal_transfer")
    assert (
        transfer["counts_in_stats"] is False and transfer["category"]["parent_name"] == "内部转账"
    )


def test_acc10_refund_in_next_month(world: World) -> None:
    w = world
    e = expense(w, amount="80", date="2026-10-02")
    w.clock.advance(days=30)
    refund(w, e["entry_id"], "80", date="2026-11-02")
    oct_ = entries.dashboard(w.ctx, w.admin, w.fid, "2026-10")["totals"]
    nov = entries.dashboard(w.ctx, w.admin, w.fid, "2026-11")["totals"]
    assert oct_["net_expense"]["nzd"] == "80.00"
    assert nov["net_expense"]["nzd"] == "-80.00"
    orig = entries.get_entry(w.ctx, w.admin, w.fid, e["entry_id"])
    assert orig["refundable_amount"] == "0.00"


def test_acc04_month_by_business_date(world: World) -> None:
    # 创建时刻为 UTC 10 月 4 日；业务日期 9 月 30 日应计入 9 月
    e = expense(world, date="2026-09-30")
    assert entries.dashboard(world.ctx, world.admin, world.fid, "2026-09")["entry_count"] == 1
    assert entries.dashboard(world.ctx, world.admin, world.fid, "2026-10")["entry_count"] == 0
    assert e["business_date"] == "2026-09-30"


class TestIdempotency:
    def test_ops01_same_key_same_body_returns_original(self, world: World) -> None:
        key = new_key()
        a = expense(world, amount="12", key=key)
        b = expense(world, amount="12", key=key)
        assert a["entry_id"] == b["entry_id"]
        assert len(raw_items(world.ctx, world.fid, "ENTRY#")) == 1

    def test_same_key_different_body_conflicts(self, world: World) -> None:
        key = new_key()
        expense(world, amount="12", key=key)
        with pytest.raises(DomainError) as ex:
            expense(world, amount="13", key=key)
        assert ex.value.code == "idempotency_key_reused"
        assert len(raw_items(world.ctx, world.fid, "ENTRY#")) == 1

    def test_keys_scoped_per_actor(self, world: World) -> None:
        key = new_key()
        a = expense(world, amount="12", key=key)
        b = expense(world, world.member, amount="12", key=key)
        assert a["entry_id"] != b["entry_id"]

    def test_get_action_after_lost_response(self, world: World) -> None:
        key = new_key()
        e = expense(world, key=key)
        r = entries.get_action(world.ctx, world.admin, world.fid, key)
        assert r["results"][0]["object_id"] == e["entry_id"]
        with pytest.raises(DomainError) as ex:
            entries.get_action(world.ctx, world.admin, world.fid, new_key())
        assert ex.value.code == "action_not_found"

    def test_invalid_key_rejected(self, world: World) -> None:
        with pytest.raises(DomainError):
            expense(world, key="short")

"""ACC-12／13／14／15／16、FX-04、OPS-11（离线）。"""

from __future__ import annotations

import threading
from datetime import date

import pytest

from ledger.application import entries, rates
from ledger.domain.errors import DomainError

from .conftest import World, new_key, raw_items
from .helpers import expense, refund


def upd(w: World, e: dict, actor=None, **patch):  # noqa: ANN001, ANN003, ANN201
    return entries.update_entry(
        w.ctx, actor or w.admin, w.fid, e["entry_id"], new_key(), patch, e["version"]
    )


class TestUpdate:
    def test_acc12_cross_month_keeps_id_and_moves_atomically(self, world: World) -> None:
        w = world
        e = expense(w, amount="8", date="2026-10-02")
        e2 = upd(w, e, business_date="2026-09-30", amount="18", note="改")
        assert e2["entry_id"] == e["entry_id"] and e2["version"] == 2
        assert [r["entry_id"] for r in raw_items(w.ctx, w.fid, "ENTRY#2026-09#")] == [e["entry_id"]]
        assert raw_items(w.ctx, w.fid, "ENTRY#2026-10#") == []
        loc = raw_items(w.ctx, w.fid, f"ENTRYLOC#{e['entry_id']}")[0]
        assert str(loc["sk"]).startswith("ENTRY#2026-09#2026-09-30#")
        assert (
            entries.dashboard(w.ctx, w.admin, w.fid, "2026-09")["totals"]["net_expense"]["nzd"]
            == "18.00"
        )
        assert entries.dashboard(w.ctx, w.admin, w.fid, "2026-10")["entry_count"] == 0

    def test_fx04_note_edit_keeps_snapshot_after_manual_correction(self, world: World) -> None:
        w = world
        e = expense(w, amount="10", currency="AUD")
        rates.put_manual_rate(
            w.ctx,
            w.admin,
            w.fid,
            new_key(),
            d=date(2026, 10, 2),
            currency="AUD",
            usd_value="0.70",
            reason="修正",
        )
        e2 = upd(w, e, note="只改备注")
        assert e2["fx_snapshot"] == e["fx_snapshot"] and e2["nzd_minor"] == e["nzd_minor"]
        e3 = upd(w, e2, amount="20")  # 改金额：沿用原快照
        # 20 AUD × 0.65 ÷ 0.56 = 23.214… → 23.21（按原快照重新计算，不是 11.61 × 2）
        assert e3["fx_snapshot"] == e["fx_snapshot"] and e3["nzd_minor"] == 2321
        e4 = upd(w, e3, business_date="2026-10-01")  # 改日期：取新快照（含家庭修正）
        assert e4["fx_snapshot"]["effective_date"] == "2026-10-01"
        e5 = upd(w, e4, business_date="2026-10-02")
        assert e5["fx_snapshot"]["rates"]["AUD"]["source"] == "family_manual"

    def test_acc13_concurrent_edits_one_wins(self, world: World) -> None:
        w = world
        e = expense(w, amount="10")
        barrier = threading.Barrier(2)
        results: list[object] = []

        def edit(note: str) -> None:
            barrier.wait()
            try:
                results.append(upd(w, e, note=note))
            except DomainError as ex:
                results.append(ex)

        ts = [threading.Thread(target=edit, args=(n,)) for n in ("甲", "乙")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        ok = [r for r in results if isinstance(r, dict)]
        errs = [r for r in results if isinstance(r, DomainError)]
        assert len(ok) == 1 and len(errs) == 1 and errs[0].code == "version_conflict"
        stored = raw_items(w.ctx, w.fid, "ENTRY#2026-10#")
        assert len(stored) == 1 and stored[0]["version"] == 2 and stored[0]["note"] == ok[0]["note"]

    def test_stale_version_rejected_with_current(self, world: World) -> None:
        w = world
        e = expense(w)
        upd(w, e, note="一")
        with pytest.raises(DomainError) as ex:
            upd(w, e, note="二")
        assert ex.value.code == "version_conflict" and ex.value.current["version"] == 2


class TestRefunds:
    def test_acc15_concurrent_refunds_never_exceed(self, world: World) -> None:
        w = world
        e = expense(w, amount="100")
        barrier = threading.Barrier(4)
        outcomes: list[object] = []

        def go() -> None:
            barrier.wait()
            try:
                outcomes.append(refund(w, e["entry_id"], "40"))
            except DomainError as ex:
                outcomes.append(ex.code)

        ts = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        ok = [o for o in outcomes if isinstance(o, dict)]
        assert len(ok) == 2, outcomes
        assert all(o == "refund_exceeds_remaining" for o in outcomes if not isinstance(o, dict))
        refunds = [r for r in raw_items(w.ctx, w.fid, "ENTRY#") if r["entry_type"] == "refund"]
        orig = next(r for r in raw_items(w.ctx, w.fid, "ENTRY#") if r["entry_id"] == e["entry_id"])
        assert len(refunds) == 2 and orig["refunded_minor"] == 8000
        assert sorted(orig["refund_ids"]) == sorted(r["entry_id"] for r in refunds)

    def test_acc14_illegal_changes_leave_no_partial_state(self, world: World) -> None:
        w = world
        e = expense(w, amount="50")
        refund(w, e["entry_id"], "20")
        e = entries.get_entry(w.ctx, w.admin, w.fid, e["entry_id"])
        before = raw_items(w.ctx, w.fid, "ENTRY#")
        for patch, code in [
            ({"currency": "USD"}, "has_linked_refunds"),
            ({"amount": "19.99"}, "has_linked_refunds"),
        ]:
            with pytest.raises(DomainError) as ex:
                upd(w, e, **patch)
            assert ex.value.code == code
        with pytest.raises(DomainError) as ex:
            entries.delete_entry(w.ctx, w.admin, w.fid, e["entry_id"], new_key(), e["version"])
        assert ex.value.code == "has_linked_refunds"
        assert raw_items(w.ctx, w.fid, "ENTRY#") == before

    def test_delete_with_refunds_moves_both_and_restore_rules(self, world: World) -> None:
        w = world
        e = expense(w, amount="50")
        r = refund(w, e["entry_id"], "20")
        e = entries.get_entry(w.ctx, w.admin, w.fid, e["entry_id"])
        res = entries.delete_entry(
            w.ctx, w.admin, w.fid, e["entry_id"], new_key(), e["version"], with_refunds=True
        )
        assert res["also_trashed"] == [r["entry_id"]]
        assert raw_items(w.ctx, w.fid, "ENTRY#") == []
        assert len(raw_items(w.ctx, w.fid, "TRASH#")) == 2
        # 原消费仍在回收站时，退款不能恢复
        r_tr = next(
            i
            for i in entries.list_trash(w.ctx, w.admin, w.fid)["items"]
            if i["entry_id"] == r["entry_id"]
        )
        with pytest.raises(DomainError) as ex:
            entries.restore_entry(w.ctx, w.admin, w.fid, r["entry_id"], new_key(), r_tr["version"])
        assert ex.value.code == "refund_target_invalid"
        e_tr = next(
            i
            for i in entries.list_trash(w.ctx, w.admin, w.fid)["items"]
            if i["entry_id"] == e["entry_id"]
        )
        back = entries.restore_entry(
            w.ctx, w.admin, w.fid, e["entry_id"], new_key(), e_tr["version"]
        )
        assert back["refundable_amount"] == "50.00"  # 退款未自动恢复
        r_back = entries.restore_entry(
            w.ctx, w.admin, w.fid, r["entry_id"], new_key(), r_tr["version"]
        )
        assert r_back["state"] == "active"
        assert (
            entries.get_entry(w.ctx, w.admin, w.fid, e["entry_id"])["refundable_amount"] == "30.00"
        )

    def test_deleting_refund_releases_amount(self, world: World) -> None:
        w = world
        e = expense(w, amount="50")
        r = refund(w, e["entry_id"], "50")
        entries.delete_entry(w.ctx, w.admin, w.fid, r["entry_id"], new_key(), r["version"])
        assert (
            entries.get_entry(w.ctx, w.admin, w.fid, e["entry_id"])["refundable_amount"] == "50.00"
        )

    def test_original_category_change_propagates_to_refunds(self, world: World) -> None:
        w = world
        e = expense(w, amount="50", method="cash")
        r = refund(w, e["entry_id"], "10")
        e = entries.get_entry(w.ctx, w.admin, w.fid, e["entry_id"])
        upd(w, e, leaf_category_id="expense-03-05", payment_method="debit_card")
        r2 = entries.get_entry(w.ctx, w.admin, w.fid, r["entry_id"])
        assert r2["category"]["leaf_id"] == "expense-03-05" and r2["payment_method"] == "debit_card"

    def test_expense_converted_to_refund(self, world: World) -> None:
        w = world
        orig = expense(w, amount="50", leaf="expense-02-02", method="cash")
        wrong = expense(w, amount="15")
        r = upd(w, wrong, type="refund", refund_of=orig["entry_id"])
        assert r["type"] == "refund" and r["category"]["leaf_id"] == "expense-02-02"
        o = entries.get_entry(w.ctx, w.admin, w.fid, orig["entry_id"])
        assert o["refund_ids"] == [wrong["entry_id"]] and o["refundable_amount"] == "35.00"


class TestTrash:
    def test_acc16_delete_restore_once_and_purge(self, world: World) -> None:
        w = world
        e = expense(w, amount="30")
        d = entries.delete_entry(w.ctx, w.admin, w.fid, e["entry_id"], new_key(), e["version"])
        assert entries.dashboard(w.ctx, w.admin, w.fid, "2026-10")["entry_count"] == 0
        key = new_key()
        back = entries.restore_entry(w.ctx, w.admin, w.fid, e["entry_id"], key, d["version"])
        again = entries.restore_entry(w.ctx, w.admin, w.fid, e["entry_id"], key, d["version"])
        assert back["entry_id"] == again["entry_id"]  # 同 key 重放
        with pytest.raises(DomainError):  # 新 key、旧版本：不再在回收站
            entries.restore_entry(w.ctx, w.admin, w.fid, e["entry_id"], new_key(), d["version"])
        assert len(raw_items(w.ctx, w.fid, "ENTRY#")) == 1
        # 再删除，超过 30 天后清理
        d2 = entries.delete_entry(w.ctx, w.admin, w.fid, e["entry_id"], new_key(), back["version"])
        w.clock.advance(days=29)
        assert entries.purge_due(w.ctx) == 0
        w.clock.advance(days=1, seconds=1)
        with pytest.raises(DomainError) as ex:
            entries.restore_entry(w.ctx, w.admin, w.fid, e["entry_id"], new_key(), d2["version"])
        assert ex.value.code == "purged"
        assert entries.purge_due(w.ctx) >= 1
        assert raw_items(w.ctx, w.fid, "TRASH#") == []
        loc = raw_items(w.ctx, w.fid, f"ENTRYLOC#{e['entry_id']}")[0]
        assert loc["state"] == "purged" and "sk" not in loc
        journal = w.ctx.store.client.get_item(
            TableName=w.ctx.store.journal_table,
            Key={"PK": {"S": f"FAMILY#{w.fid}"}, "SK": {"S": f"PURGED#{e['entry_id']}"}},
        )
        assert "Item" in journal and "note" not in journal["Item"]
        with pytest.raises(DomainError) as ex:
            entries.get_entry(w.ctx, w.admin, w.fid, e["entry_id"])
        assert ex.value.code == "not_found"

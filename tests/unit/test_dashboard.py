"""ACC-04、ACC-06、ACC-07、ACC-08、ACC-10、ACC-11、CAT-01（汇总部分）。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from ledger.domain.categories import Catalog
from ledger.domain.dashboard import Filters, compute

from .factories import catalog, entry

CAT = catalog()


def totals(rows, f=None, cat=CAT):  # noqa: ANN001, ANN201
    return compute(rows, cat, f)


def test_acc06_net_and_balance() -> None:
    rows = [
        entry(amount_minor=10000, nzd=10000, cny=37333),
        entry(type="refund", amount_minor=2000, nzd=2000, cny=7467, refund_of="x"),
        entry(
            type="income", leaf="income-01-01", amount_minor=5000, nzd=5000, cny=18667, method=None
        ),
    ]
    t = totals(rows)["totals"]
    assert t["expense_gross"]["nzd"] == "100.00"
    assert t["refund"]["nzd"] == "20.00"
    assert t["net_expense"]["nzd"] == "80.00"
    assert t["income"]["nzd"] == "50.00"
    assert t["balance"]["nzd"] == "-30.00"
    assert t["net_expense"]["cny_minor"] == 37333 - 7467
    assert t["balance"]["cny_minor"] == 18667 - (37333 - 7467)


def test_acc07_income_not_in_payment_methods() -> None:
    rows = [
        entry(method="cash", amount_minor=800),
        entry(method="debit_card", amount_minor=500),
        entry(type="income", leaf="income-01-01", method="debit_card", amount_minor=99999),
        entry(type="refund", method="cash", amount_minor=300, refund_of="x"),
    ]
    by = {m["payment_method"]: m for m in totals(rows)["by_payment_method"]}
    assert by["debit_card"]["expense"]["nzd_minor"] == 500
    assert by["cash"]["net"]["nzd_minor"] == 500
    assert by["cash"]["refund_count"] == 1
    assert by["credit_card"]["net"]["nzd_minor"] == 0


def test_acc08_non_stat_types_listed_but_not_counted() -> None:
    rows = [
        entry(type="internal_transfer", leaf="transfer-01-01", amount_minor=100000),
        entry(type="exchange", leaf="exchange-01-01", amount_minor=100000),
        entry(type="card_repayment", leaf="repayment-01-01", amount_minor=100000),
        entry(type="receivable", leaf="receivable-01-01", amount_minor=100000),
    ]
    out = totals(rows)
    assert out["entry_count"] == 4
    assert all(v["nzd_minor"] == 0 for v in out["totals"].values())
    assert out["category_pie"]["empty"] is True


def test_trashed_entries_excluded() -> None:
    out = totals([replace(entry(amount_minor=100), state="trashed")])
    assert out["entry_count"] == 0


def test_acc11_pie_negatives_zero_and_denominator() -> None:
    rows = [
        entry(leaf="expense-01-01", amount_minor=6000),
        entry(leaf="expense-03-01", amount_minor=2000),
        entry(type="refund", leaf="expense-05-01", amount_minor=1500, refund_of="x"),  # 仅退款 → 负
        entry(leaf="expense-07-01", amount_minor=1000),
        entry(type="refund", leaf="expense-07-01", amount_minor=1000, refund_of="y"),  # 净零
    ]
    pie = totals(rows)["category_pie"]
    assert [s["category_id"] for s in pie["slices"]] == ["expense-01", "expense-03"]
    assert pie["denominator_minor"] == 8000  # 不同于净支出 6500
    assert [s["share_percent"] for s in pie["slices"]] == ["75.00", "25.00"]
    assert [n["category_id"] for n in pie["negatives"]] == ["expense-05"]
    assert totals(rows)["totals"]["net_expense"]["nzd_minor"] == 6500


def test_acc11_empty_pie_when_only_refunds() -> None:
    pie = totals([entry(type="refund", amount_minor=100, refund_of="x")])["category_pie"]
    assert pie["empty"] is True and pie["slices"] == []


def test_acc10_refund_counts_in_its_own_month() -> None:
    # 汇总按调用方提供的月份分区；退款记录的 month 取自自身业务日期
    r = entry(type="refund", amount_minor=100, refund_of="x", business_date=date(2026, 11, 2))
    assert r.month == "2026-11"


def test_acc04_month_from_business_date_not_created_at() -> None:
    e = entry(business_date=date(2026, 9, 30))  # created_at 为 10 月 4 日 UTC
    assert e.month == "2026-09"


def test_cat01_rename_and_move_reflected_without_touching_entries() -> None:
    rows = [entry(leaf="expense-01-03", amount_minor=500)]
    cat = Catalog(CAT.by_id.values())
    renamed = cat.plan_rename("expense-01", "吃喝")
    moved = cat.plan_move("expense-01-03", "expense-07")
    cat2 = Catalog(
        [
            *(
                c
                for c in cat.by_id.values()
                if c.category_id not in ("expense-01", "expense-01-03")
            ),
            renamed,
            moved,
        ]
    )
    pie = totals(rows, cat=cat2)["category_pie"]
    assert pie["slices"][0]["category_id"] == "expense-07"
    pie_before = totals(
        rows,
        cat=Catalog([*(c for c in cat.by_id.values() if c.category_id != "expense-01"), renamed]),
    )
    assert pie_before["category_pie"]["slices"][0]["name"] == "吃喝"
    assert rows[0].nzd_minor == 500


def test_filters_apply_to_totals_and_pie() -> None:
    rows = [
        entry(created_by="u1", amount_minor=100, method="cash"),
        entry(created_by="u2", leaf="expense-03-01", amount_minor=200),
    ]
    assert totals(rows, Filters(created_by="u2"))["totals"]["net_expense"]["nzd_minor"] == 200
    assert totals(rows, Filters(payment_method="cash"))["entry_count"] == 1
    assert totals(rows, Filters(category_id="expense-03"))["entry_count"] == 1
    assert totals(rows, Filters(category_id="expense-01-01"))["entry_count"] == 1
    assert totals(rows, Filters(type="income"))["entry_count"] == 0

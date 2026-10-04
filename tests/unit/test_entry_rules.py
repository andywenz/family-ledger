"""ACC-09、ACC-12（规则部分）、ACC-14、CAT-04、FX-04（快照决策）：ADR-0002／0006。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ledger.domain.entries import EntryInput, Limits, plan_create, plan_update
from ledger.domain.errors import DomainError, ValidationFailed

from .factories import CURRENCIES, TODAY, catalog, entry

CAT = catalog()
LIM = Limits()


def create(**kw: object):  # noqa: ANN201
    base = {
        "business_date": "2026-10-04",
        "type": "expense",
        "amount": "10",
        "currency": "NZD",
        "leaf_category_id": "expense-01-01",
        "payment_method": "credit_card",
    }
    base.update(kw)
    original = base.pop("original", None)
    return plan_create(
        EntryInput(**base),
        catalog=CAT,
        currencies=CURRENCIES,
        today=TODAY,
        limits=LIM,
        original=original,
    )  # type: ignore[arg-type]


def update(old, original=None, **patch):  # noqa: ANN001, ANN201
    return plan_update(
        old,
        patch,
        catalog=CAT,
        currencies=CURRENCIES,
        today=TODAY,
        limits=LIM,
        new_original=original,
    )


def codes(exc: pytest.ExceptionInfo[DomainError]) -> set[str]:
    return {f.code for f in exc.value.fields} | {exc.value.code}


class TestCreate:
    def test_expense_requires_payment_method(self) -> None:
        with pytest.raises(ValidationFailed) as e:
            create(payment_method=None)
        assert "required" in codes(e)

    def test_income_method_optional(self) -> None:
        p = create(type="income", leaf_category_id="income-02-01", payment_method=None)
        assert p.payment_method is None

    @pytest.mark.parametrize(
        ("type_", "leaf"),
        [
            ("internal_transfer", "transfer-01-01"),
            ("exchange", "exchange-01-01"),
            ("card_repayment", "repayment-01-01"),
            ("receivable", "receivable-01-05"),
        ],
    )
    def test_non_stat_types_need_own_leaf_and_no_method(self, type_: str, leaf: str) -> None:
        p = create(type=type_, leaf_category_id=leaf, payment_method=None)
        assert p.leaf_category_id == leaf
        with pytest.raises(ValidationFailed):
            create(type=type_, leaf_category_id=leaf, payment_method="cash")

    def test_cat04_kind_mismatch_rejected(self) -> None:
        with pytest.raises(DomainError) as e:
            create(type="income", leaf_category_id="expense-01-01", payment_method=None)
        assert e.value.code == "category_kind_mismatch"
        with pytest.raises(DomainError) as e:
            create(type="receivable", leaf_category_id="transfer-01-01", payment_method=None)
        assert e.value.code == "category_kind_mismatch"

    def test_cat04_every_entry_needs_leaf(self) -> None:
        with pytest.raises(ValidationFailed):
            create(type="exchange", leaf_category_id=None, payment_method=None)
        with pytest.raises(ValidationFailed):
            create(leaf_category_id="expense-01")  # 一级分类不可直接引用

    def test_acc09_gift_out_is_expense_gift_in_is_income(self) -> None:
        out = create(leaf_category_id="expense-08-01")
        gift = create(type="income", leaf_category_id="income-02-01", payment_method=None)
        assert (out.type, gift.type) == ("expense", "income")

    def test_currency_not_enabled(self) -> None:
        with pytest.raises(ValidationFailed):
            create(currency="GBP")

    def test_future_and_ancient_dates_rejected(self) -> None:
        create(business_date="2026-10-05")  # 跨时区 +1 天允许
        for d in ("2026-10-06", "1999-12-31", "2026-02-30", "20261004"):
            with pytest.raises(ValidationFailed):
                create(business_date=d)

    def test_note_length(self) -> None:
        create(note="x" * 200)
        with pytest.raises(ValidationFailed):
            create(note="x" * 201)


class TestRefund:
    def test_refund_inherits_original(self) -> None:
        orig = entry(leaf="expense-03-05", method="cash", amount_minor=5000)
        p = create(
            type="refund",
            amount="20",
            original=orig,
            refund_of=orig.entry_id,
            leaf_category_id=None,
            currency=None,
            payment_method=None,
        )
        assert (p.leaf_category_id, p.payment_method, p.currency) == (
            "expense-03-05",
            "cash",
            "NZD",
        )

    def test_refund_cannot_set_category_or_currency(self) -> None:
        orig = entry()
        with pytest.raises(ValidationFailed):
            create(type="refund", original=orig, refund_of=orig.entry_id, payment_method=None)

    def test_refund_limit(self) -> None:
        orig = entry(amount_minor=5000, refunded_minor=3000, refund_ids=("r1",))
        kw = {
            "type": "refund",
            "original": orig,
            "refund_of": orig.entry_id,
            "leaf_category_id": None,
            "currency": None,
            "payment_method": None,
        }
        create(amount="20", **kw)
        with pytest.raises(DomainError) as e:
            create(amount="20.01", **kw)
        assert e.value.code == "refund_exceeds_remaining"

    def test_refund_target_must_be_active_expense(self) -> None:
        kw = {
            "type": "refund",
            "amount": "1",
            "leaf_category_id": None,
            "currency": None,
            "payment_method": None,
            "refund_of": "x",
        }
        for orig in (entry(type="income", leaf="income-01-01"), replace(entry(), state="trashed")):
            with pytest.raises(DomainError) as e:
                create(original=orig, **kw)
            assert e.value.code == "refund_target_invalid"


class TestUpdate:
    def test_fx04_note_category_method_keep_snapshot(self) -> None:
        old = entry()
        p = update(old, note="改备注", leaf_category_id="expense-01-03", payment_method="cash")
        assert p.keep_snapshot is old.fx_snapshot

    def test_amount_change_keeps_snapshot(self) -> None:
        old = entry()
        p = update(old, amount="12.5")
        assert p.keep_snapshot is old.fx_snapshot and p.amount_minor == 1250

    @pytest.mark.parametrize("patch", [{"business_date": "2026-09-30"}, {"currency": "USD"}])
    def test_date_or_currency_change_needs_new_snapshot(self, patch: dict[str, str]) -> None:
        assert update(entry(), **patch).keep_snapshot is None

    def test_type_change_requires_matching_leaf(self) -> None:
        old = entry()
        with pytest.raises(DomainError) as e:
            update(old, type="income", payment_method=None)
        assert e.value.code == "category_kind_mismatch"
        p = update(old, type="income", leaf_category_id="income-01-02", payment_method=None)
        assert p.type == "income"

    def test_type_change_validates_method(self) -> None:
        with pytest.raises(ValidationFailed):
            update(entry(), type="exchange", leaf_category_id="exchange-01-01")

    def test_refund_cannot_change_type(self) -> None:
        r = entry(type="refund", refund_of="e0")
        with pytest.raises(DomainError) as e:
            update(r, type="expense")
        assert e.value.code == "type_change_not_allowed"

    def test_refund_fields_follow_original(self) -> None:
        r = entry(type="refund", refund_of="e0")
        with pytest.raises(ValidationFailed):
            update(r, leaf_category_id="expense-01-02")

    def test_refund_amount_change_checks_remaining(self) -> None:
        orig = entry(entry_id="o1", amount_minor=5000, refunded_minor=2000, refund_ids=("r",))
        r = entry(type="refund", refund_of="o1", amount_minor=2000)
        assert update(r, orig, amount="50").amount_minor == 5000
        with pytest.raises(DomainError) as e:
            update(r, orig, amount="50.01")
        assert e.value.code == "refund_exceeds_remaining"

    def test_acc14_refunded_expense_restrictions(self) -> None:
        old = entry(amount_minor=5000, refunded_minor=2000, refund_ids=("r1",))
        for patch in (
            {"currency": "USD"},
            {"type": "income", "leaf_category_id": "income-01-01", "payment_method": None},
        ):
            with pytest.raises(DomainError) as e:
                update(old, **patch)
            assert e.value.code == "has_linked_refunds"
        with pytest.raises(DomainError) as e:
            update(old, amount="19.99")
        assert e.value.code == "has_linked_refunds"
        assert update(old, amount="20").amount_minor == 2000

    def test_expense_to_refund_requires_link(self) -> None:
        old = entry(amount_minor=1000)
        with pytest.raises(ValidationFailed):
            update(old, type="refund")
        orig = entry(entry_id="o2", amount_minor=5000, leaf="expense-02-02", method="cash")
        p = update(old, orig, type="refund", refund_of="o2")
        assert (p.type, p.leaf_category_id, p.payment_method) == ("refund", "expense-02-02", "cash")
        with pytest.raises(DomainError):
            update(old, old, type="refund", refund_of=old.entry_id)

    def test_trashed_entry_not_editable(self) -> None:
        with pytest.raises(DomainError):
            update(replace(entry(), state="trashed"), note="x")

    def test_unknown_fields_rejected(self) -> None:
        with pytest.raises(ValidationFailed):
            update(entry(), created_by="u2")
        with pytest.raises(ValidationFailed):
            update(entry(), nzd_minor=1)

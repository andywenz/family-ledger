from __future__ import annotations

from typing import Any

from ledger.application import entries
from ledger.domain.authz import Actor
from ledger.domain.entries import EntryInput

from .conftest import World, new_key


def expense(
    w: World,
    actor: Actor | None = None,
    *,
    amount: str = "100",
    date: str = "2026-10-02",
    currency: str = "NZD",
    leaf: str = "expense-01-01",
    method: str = "credit_card",
    note: str = "",
    key: str | None = None,
) -> dict[str, Any]:
    return entries.create_entry(
        w.ctx,
        actor or w.admin,
        w.fid,
        key or new_key(),
        EntryInput(
            business_date=date,
            type="expense",
            amount=amount,
            currency=currency,
            leaf_category_id=leaf,
            payment_method=method,
            note=note,
        ),
    )


def refund(
    w: World,
    original_id: str,
    amount: str,
    *,
    actor: Actor | None = None,
    date: str = "2026-10-02",
    key: str | None = None,
) -> dict[str, Any]:
    return entries.create_entry(
        w.ctx,
        actor or w.admin,
        w.fid,
        key or new_key(),
        EntryInput(business_date=date, type="refund", amount=amount, refund_of=original_id),
    )


def income(w: World, amount: str, *, date: str = "2026-10-02") -> dict[str, Any]:
    return entries.create_entry(
        w.ctx,
        w.admin,
        w.fid,
        new_key(),
        EntryInput(
            business_date=date,
            type="income",
            amount=amount,
            currency="NZD",
            leaf_category_id="income-01-01",
        ),
    )

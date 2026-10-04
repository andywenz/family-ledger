"""单元测试用的合成数据构造器。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from ledger.domain.categories import Catalog, load_template
from ledger.domain.entries import Entry
from ledger.domain.fx import FxSnapshot, RateValue
from ledger.domain.money import CurrencyMeta

NOW = datetime(2026, 10, 4, 1, 0, tzinfo=UTC)
TODAY = date(2026, 10, 4)

CURRENCIES = {
    "NZD": CurrencyMeta("NZD", 2),
    "CNY": CurrencyMeta("CNY", 2),
    "USD": CurrencyMeta("USD", 2),
    "AUD": CurrencyMeta("AUD", 2),
    "EUR": CurrencyMeta("EUR", 2),
    "JPY": CurrencyMeta("JPY", 0, provider_supported=True),
    "KWD": CurrencyMeta("KWD", 3, provider_supported=False),
}

# 需求 §7 示例：r(NZD)=0.56、r(CNY)=0.15
RATES = {
    "USD": Decimal(1),
    "NZD": Decimal("0.56"),
    "CNY": Decimal("0.15"),
    "AUD": Decimal("0.65"),
    "EUR": Decimal("1.08"),
    "JPY": Decimal("0.0067"),
    "KWD": Decimal("3.25"),
}


def snapshot(d: date = TODAY, rates: dict[str, Decimal] | None = None) -> FxSnapshot:
    rates = rates or RATES
    return FxSnapshot(
        requested_date=d,
        effective_date=d,
        provider="ecb",
        rates={c: RateValue(v, "base" if c == "USD" else "provider", 1) for c, v in rates.items()},
        fetched_at=NOW,
        manual=False,
    )


def catalog() -> Catalog:
    return Catalog(load_template())


_seq = 0


def entry(
    *,
    type: str = "expense",
    leaf: str = "expense-01-01",
    currency: str = "NZD",
    amount_minor: int = 10000,
    nzd: int | None = None,
    cny: int | None = None,
    method: str | None = "credit_card",
    created_by: str = "u1",
    business_date: date = TODAY,
    **kw: object,
) -> Entry:
    global _seq
    _seq += 1
    if type not in ("expense", "income", "refund"):
        method = kw.pop("method_override", None)  # type: ignore[assignment]
    return Entry(
        entry_id=kw.pop("entry_id", f"e{_seq}"),  # type: ignore[arg-type]
        family_id="f1",
        business_date=business_date,
        type=type,
        leaf_category_id=leaf,
        currency=currency,
        amount_minor=amount_minor,
        note=str(kw.pop("note", "")),
        payment_method=method,
        created_by=created_by,
        created_at=NOW,
        updated_at=NOW,
        source="manual",
        version=int(kw.pop("version", 1)),  # type: ignore[call-overload]
        fx_snapshot=snapshot(business_date),
        nzd_minor=amount_minor if nzd is None else nzd,
        cny_minor=amount_minor if cny is None else cny,
        **kw,  # type: ignore[arg-type]
    )

"""ACC-02／ACC-03／FX-03：精确金额与折算。"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ledger.domain.errors import ValidationFailed
from ledger.domain.money import (
    CurrencyMeta,
    convert_minor,
    convert_to_target,
    format_minor,
    parse_amount,
)

from .factories import CURRENCIES, RATES

NZD = CURRENCIES["NZD"]
MAX = Decimal("1000000")


def test_acc02_example_from_requirements() -> None:
    minor = parse_amount("5.00", NZD, max_major=MAX)
    assert convert_to_target(minor, NZD, "NZD", RATES) == 500
    assert convert_to_target(minor, NZD, "CNY", RATES) == 1867  # 18.67


def test_acc02_cross_rate_not_truncated_first() -> None:
    # 若先把 NZD→CNY 交叉汇率截断为 3.73，结果会是 18.65；正确结果为 18.67
    truncated = Decimal(500) / 100 * Decimal("3.73")
    assert truncated.quantize(Decimal("0.01")) == Decimal("18.65")
    assert format_minor(convert_minor(500, 2, RATES["NZD"], RATES["CNY"]), 2) == "18.67"


@pytest.mark.parametrize(
    "text",
    ["0", "0.00", "-5", "+5", "5e2", "NaN", "Infinity", "1.234", "05", " 5", "5.", "", "1,000"],
)
def test_invalid_amounts_rejected(text: str) -> None:
    with pytest.raises(ValidationFailed):
        parse_amount(text, NZD, max_major=MAX)


def test_amount_over_limit_rejected() -> None:
    with pytest.raises(ValidationFailed):
        parse_amount("1000000.01", NZD, max_major=MAX)
    assert parse_amount("1000000", NZD, max_major=MAX) == 100000000


def test_non_string_amount_rejected() -> None:
    with pytest.raises(ValidationFailed):
        parse_amount(5.0, NZD, max_major=MAX)  # type: ignore[arg-type]


def test_fx03_zero_and_three_digit_currencies() -> None:
    jpy, kwd = CURRENCIES["JPY"], CURRENCIES["KWD"]
    with pytest.raises(ValidationFailed):
        parse_amount("100.5", jpy, max_major=MAX)
    assert parse_amount("1500", jpy, max_major=MAX) == 1500
    assert parse_amount("1.235", kwd, max_major=MAX) == 1235
    with pytest.raises(ValidationFailed):
        parse_amount("1.2345", kwd, max_major=MAX)
    # 1500 JPY × 0.0067 ÷ 0.56 = 17.946… → 17.95 NZD
    assert convert_to_target(1500, jpy, "NZD", RATES) == 1795
    # 1.235 KWD × 3.25 ÷ 0.15 = 26.7583… → 26.76 CNY
    assert convert_to_target(1235, kwd, "CNY", RATES) == 2676


def test_same_currency_keeps_amount() -> None:
    cny = CURRENCIES["CNY"]
    assert convert_to_target(12345, cny, "CNY", RATES) == 12345


def test_half_up_at_boundary() -> None:
    # 0.005 恰好进位：1 NZD × 0.15 ÷ 0.56 不是边界；构造 r 使结果为 x.xx5
    assert convert_minor(1, 2, Decimal("0.5"), Decimal("1")) == 1  # 0.005 → 0.01
    assert convert_minor(3, 2, Decimal("0.5"), Decimal("1")) == 2  # 0.015 → 0.02


def _exact(amount_minor: int, src: Decimal, dst: Decimal) -> int:
    value = Fraction(amount_minor, 100) * Fraction(src) / Fraction(dst)
    cents = value * 100
    floor = cents.numerator // cents.denominator
    return floor + (1 if cents - floor >= Fraction(1, 2) else 0)


rate_st = st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("100"), places=8)


@given(st.integers(min_value=1, max_value=10**14), rate_st, rate_st)
def test_acc03_conversion_matches_exact_rational(amount: int, src: Decimal, dst: Decimal) -> None:
    assert convert_minor(amount, 2, src, dst) == _exact(amount, src, dst)


@given(st.lists(st.integers(min_value=1, max_value=10**9), min_size=1, max_size=50))
def test_acc03_month_total_equals_sum_of_displayed(amounts: list[int]) -> None:
    usd = CurrencyMeta("USD", 2)
    per_row = [convert_to_target(a, usd, "NZD", RATES) for a in amounts]
    total = sum(per_row)
    shown = sum(Decimal(format_minor(x, 2)) for x in per_row)
    assert Decimal(format_minor(total, 2)) == shown
    assert shown == shown.quantize(Decimal("0.01"), ROUND_HALF_UP)

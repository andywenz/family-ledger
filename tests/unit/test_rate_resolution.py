"""FX-01／FX-02：汇率组解析（ADR-0004）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from ledger.domain.fx import (
    FxSnapshot,
    ManualRate,
    ProviderRateSet,
    RatePendingResult,
    required_currencies,
    resolve,
)

T = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)


def pset(d: date, **rates: str) -> ProviderRateSet:
    return ProviderRateSet("ecb", d, {k: Decimal(v) for k, v in rates.items()}, 1, T)


FRI = date(2026, 10, 2)
SAT = date(2026, 10, 3)
SUN = date(2026, 10, 4)


def test_fx01_weekend_uses_latest_complete_set_and_labels_date() -> None:
    sets = {FRI: pset(FRI, NZD="0.56", CNY="0.15", AUD="0.65")}
    out = resolve(SUN, required_currencies("AUD"), sets, {})
    assert isinstance(out, FxSnapshot)
    assert out.requested_date == SUN
    assert out.effective_date == FRI
    assert out.usd()["AUD"] == Decimal("0.65")
    assert not out.manual


def test_fx01_never_uses_future_dates() -> None:
    mon = date(2026, 10, 5)
    sets = {mon: pset(mon, NZD="0.6", CNY="0.2"), FRI: pset(FRI, NZD="0.56", CNY="0.15")}
    out = resolve(SUN, required_currencies("NZD"), sets, {})
    assert isinstance(out, FxSnapshot) and out.effective_date == FRI


def test_fx02_no_cross_date_splicing() -> None:
    # 周六只有 NZD（不完整），周五完整：全部取周五，不能 NZD 取周六、CNY 取周五
    sets = {
        SAT: pset(SAT, NZD="0.99"),
        FRI: pset(FRI, NZD="0.56", CNY="0.15"),
    }
    out = resolve(SAT, required_currencies("NZD"), sets, {})
    assert isinstance(out, FxSnapshot)
    assert out.effective_date == FRI
    assert out.usd()["NZD"] == Decimal("0.56")


def test_fx02_lookback_exhausted_is_pending_not_zero() -> None:
    old = date(2026, 9, 25)
    sets = {old: pset(old, NZD="0.56", CNY="0.15")}
    out = resolve(date(2026, 10, 3), required_currencies("NZD"), sets, {})
    assert isinstance(out, RatePendingResult)
    assert out.searched_from == date(2026, 9, 26)
    assert set(out.missing_currencies) == {"CNY", "NZD"}


def test_fx02_unsupported_currency_pending_until_manual() -> None:
    sets = {FRI: pset(FRI, NZD="0.56", CNY="0.15")}
    out = resolve(FRI, required_currencies("FJD"), sets, {})
    assert isinstance(out, RatePendingResult) and out.missing_currencies == ("FJD",)
    manual = {(FRI, "FJD"): ManualRate(FRI, "FJD", Decimal("0.45"), 1, T)}
    out2 = resolve(FRI, required_currencies("FJD"), sets, manual)
    assert isinstance(out2, FxSnapshot)
    assert out2.manual
    assert out2.rates["FJD"].source == "family_manual"
    assert out2.rates["NZD"].source == "provider"


def test_family_manual_overrides_provider_same_date() -> None:
    sets = {FRI: pset(FRI, NZD="0.56", CNY="0.15")}
    manual = {(FRI, "NZD"): ManualRate(FRI, "NZD", Decimal("0.57"), 2, T)}
    out = resolve(FRI, required_currencies("NZD"), sets, manual)
    assert isinstance(out, FxSnapshot)
    assert out.usd()["NZD"] == Decimal("0.57")
    assert out.rates["NZD"].revision == 2


def test_usd_is_base_one() -> None:
    sets = {FRI: pset(FRI, NZD="0.56", CNY="0.15")}
    out = resolve(FRI, required_currencies("USD"), sets, {})
    assert isinstance(out, FxSnapshot)
    assert out.rates["USD"].usd_value == 1 and out.rates["USD"].source == "base"


def test_snapshot_roundtrip_keeps_exact_values() -> None:
    sets = {FRI: pset(FRI, NZD="0.5612345678901234567", CNY="0.15")}
    out = resolve(FRI, required_currencies("NZD"), sets, {})
    assert isinstance(out, FxSnapshot)
    again = FxSnapshot.from_dict(out.to_dict())
    assert again == out
    assert out.to_dict()["rates"]["NZD"]["usd_value"] == "0.5612345678901234567"

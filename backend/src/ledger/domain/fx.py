"""汇率解析（ADR-0004）：同一日期的完整组，家庭修正优先于供应商，不跨日期拼接。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

BASE = "USD"
TARGETS = ("NZD", "CNY")
DEFAULT_LOOKBACK_DAYS = 7


@dataclass(frozen=True)
class ProviderRateSet:
    provider: str
    effective_date: date
    rates: Mapping[str, Decimal]  # USD 基准：一单位币种可兑换的 USD
    revision: int
    fetched_at: datetime


@dataclass(frozen=True)
class ManualRate:
    effective_date: date
    currency: str
    usd_value: Decimal
    revision: int
    entered_at: datetime


@dataclass(frozen=True)
class RateValue:
    usd_value: Decimal
    source: str  # provider | family_manual | base
    revision: int


@dataclass(frozen=True)
class FxSnapshot:
    requested_date: date
    effective_date: date
    provider: str
    rates: Mapping[str, RateValue]
    fetched_at: datetime
    manual: bool

    def usd(self) -> dict[str, Decimal]:
        return {c: v.usd_value for c, v in self.rates.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_date": self.requested_date.isoformat(),
            "effective_date": self.effective_date.isoformat(),
            "provider": self.provider,
            "rates": {
                c: {
                    "usd_value": decimal_str(v.usd_value),
                    "source": v.source,
                    "revision": v.revision,
                }
                for c, v in sorted(self.rates.items())
            },
            "fetched_at": iso_utc(self.fetched_at),
            "manual": self.manual,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> FxSnapshot:
        return cls(
            requested_date=date.fromisoformat(d["requested_date"]),
            effective_date=date.fromisoformat(d["effective_date"]),
            provider=d["provider"],
            rates={
                c: RateValue(Decimal(v["usd_value"]), v["source"], int(v["revision"]))
                for c, v in d["rates"].items()
            },
            fetched_at=datetime.fromisoformat(d["fetched_at"]),
            manual=bool(d["manual"]),
        )


@dataclass(frozen=True)
class RatePendingResult:
    missing_currencies: tuple[str, ...]
    searched_from: date
    searched_to: date


def decimal_str(v: Decimal) -> str:
    """不使用指数表示的十进制字符串。"""
    s = format(v, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def iso_utc(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def required_currencies(source_currency: str) -> tuple[str, ...]:
    return tuple(sorted({source_currency, *TARGETS}))


def resolve(
    requested: date,
    currencies: tuple[str, ...],
    provider_sets: Mapping[date, ProviderRateSet],
    family_rates: Mapping[tuple[date, str], ManualRate],
    *,
    provider: str = "ecb",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> FxSnapshot | RatePendingResult:
    """从 requested 向前至多 lookback_days 天，返回第一个完整组。

    调用方负责提供 [requested-lookback, requested] 范围内的数据；本函数不读取未来日期。
    """
    earliest = requested - timedelta(days=lookback_days)
    first_missing: tuple[str, ...] = ()
    for offset in range(lookback_days + 1):
        d = requested - timedelta(days=offset)
        pset = provider_sets.get(d)
        values: dict[str, RateValue] = {}
        stamps: list[datetime] = []
        missing: list[str] = []
        for c in currencies:
            manual = family_rates.get((d, c))
            if c == BASE:
                values[c] = RateValue(Decimal(1), "base", 0)
            elif manual is not None:
                values[c] = RateValue(manual.usd_value, "family_manual", manual.revision)
                stamps.append(manual.entered_at)
            elif pset is not None and c in pset.rates:
                values[c] = RateValue(pset.rates[c], "provider", pset.revision)
                stamps.append(pset.fetched_at)
            else:
                missing.append(c)
        if offset == 0:
            first_missing = tuple(missing)
        if not missing:
            if not stamps and pset is not None:
                stamps.append(pset.fetched_at)
            if not stamps:
                # 只需要 USD（理论上不会发生：目标币种至少含 NZD／CNY）
                continue
            return FxSnapshot(
                requested_date=requested,
                effective_date=d,
                provider=provider,
                rates=values,
                fetched_at=max(stamps),
                manual=any(v.source == "family_manual" for v in values.values()),
            )
    return RatePendingResult(first_missing or currencies, earliest, requested)

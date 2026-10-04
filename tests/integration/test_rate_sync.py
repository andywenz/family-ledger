"""每日汇率同步与新币种回补（离线，供应商替身）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from ledger.adapters.dynamo import keys
from ledger.application import maintenance, rates
from ledger.domain.fx import ProviderRateSet

from .conftest import World


class FakeProvider:
    def __init__(self, rates_by_code: dict[str, str]) -> None:
        self.rates = rates_by_code
        self.calls: list[tuple[date, date]] = []

    def fetch(self, start: date, end: date, currencies: object) -> list[ProviderRateSet]:
        self.calls.append((start, end))
        return [
            ProviderRateSet(
                "ecb", end, {k: Decimal(v) for k, v in self.rates.items()}, 1, datetime.now(UTC)
            )
        ]


def test_existing_rates_never_change_new_codes_are_added(world: World) -> None:
    """已有日期的汇率组：已有币种不改写，只补入新币种（新增全局币种后回补）。"""
    w = world
    day = date(2026, 10, 1)  # conftest 已写入 NZD 0.56 等
    before = w.ctx.store.get(keys.provider_rates("ecb"), day.isoformat())["rates"]
    changed = rates.store_provider_set(
        w.ctx,
        ProviderRateSet(
            "ecb", day, {"NZD": Decimal("9.99"), "HKD": Decimal("0.1286")}, 1, datetime.now(UTC)
        ),
    )
    after = w.ctx.store.get(keys.provider_rates("ecb"), day.isoformat())["rates"]
    assert changed and after["NZD"] == before["NZD"] and after["HKD"] == "0.1286"
    # 再次写入同一新币种：不改、不报错
    assert not rates.store_provider_set(
        w.ctx, ProviderRateSet("ecb", day, {"HKD": Decimal("0.5")}, 1, datetime.now(UTC))
    )
    assert w.ctx.store.get(keys.provider_rates("ecb"), day.isoformat())["rates"]["HKD"] == "0.1286"


def test_daily_sync_pulls_recent_window(world: World) -> None:
    w = world
    fake = FakeProvider({"NZD": "0.57", "USD": "1"})
    maintenance.sync_recent_rates(w.ctx, datetime(2026, 12, 3, 16, 30, tzinfo=UTC), client=fake)
    assert fake.calls == [(date(2026, 11, 23), date(2026, 12, 3))]  # 最近 10 天
    assert w.ctx.store.get(keys.provider_rates("ecb"), "2026-12-03")["rates"]["NZD"] == "0.57"


def test_currency_lists_are_alphabetical(world: World) -> None:
    w = world
    fam = [c["code"] for c in rates.list_family_currencies(w.ctx, w.admin, w.fid)]
    glob = [c["code"] for c in rates.list_global_currencies(w.ctx, w.admin)]
    assert fam == sorted(fam) and glob == sorted(glob)

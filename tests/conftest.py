"""共享夹具：DynamoDB Local（docker compose up -d）。证据层级：离线。"""

from __future__ import annotations

import itertools
import os
import secrets
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ledger.adapters.dynamo.codec import currency_item
from ledger.adapters.dynamo.schema import create_tables
from ledger.adapters.dynamo.store import Store, Tx
from ledger.application import rates
from ledger.application.context import AppContext
from ledger.domain.fx import ProviderRateSet
from ledger.domain.money import CurrencyMeta

os.environ.setdefault("LEDGER_ENV", "local")

ENDPOINT = os.environ.get("DYNAMODB_ENDPOINT", "http://localhost:8000")


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


_counter = itertools.count()


def new_key() -> str:
    return f"k{next(_counter):06d}{secrets.token_hex(8)}"


@pytest.fixture(scope="session")
def store() -> Iterator[Store]:
    suffix = secrets.token_hex(4)
    s = Store.connect(f"ledger-test-{suffix}", f"ledger-test-journal-{suffix}", endpoint=ENDPOINT)
    try:
        s.client.list_tables()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"DynamoDB Local 不可用：{e}")
    create_tables(s.client, s.table, s.journal_table)
    # 全局币种与 2026-09-20 起每日 ECB 汇率组（合成值）
    tx = Tx(s.table)
    for c in [
        CurrencyMeta("NZD", 2, "新西兰元"),
        CurrencyMeta("CNY", 2, "人民币"),
        CurrencyMeta("USD", 2, "美元"),
        CurrencyMeta("AUD", 2, "澳元"),
        CurrencyMeta("EUR", 2, "欧元"),
        CurrencyMeta("JPY", 0, "日元"),
        CurrencyMeta("FJD", 2, "斐济元", provider_supported=False),
    ]:
        tx.put(currency_item(c))
    s.commit(tx)
    seed_rates(AppContext(store=s))
    yield s
    s.client.delete_table(TableName=s.table)
    s.client.delete_table(TableName=s.journal_table)


T0 = datetime(2026, 10, 4, 1, 0, tzinfo=UTC)  # 奥克兰 10 月 4 日 14:00


def seed_rates(ctx: AppContext) -> None:
    d = date(2026, 9, 20)
    while d <= date(2026, 11, 30):
        if d.weekday() < 5:  # ECB 周末不发布
            rates.store_provider_set(
                ctx,
                ProviderRateSet(
                    "ecb",
                    d,
                    {
                        "NZD": Decimal("0.56"),
                        "CNY": Decimal("0.15"),
                        "USD": Decimal(1),
                        "AUD": Decimal("0.65"),
                        "EUR": Decimal("1.08"),
                        "JPY": Decimal("0.0067"),
                    },
                    1,
                    T0,
                ),
            )
        d += timedelta(days=1)

"""FX-06：供应商请求只含日期与币种；EUR→USD 基准换算与严格解析。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from ledger.adapters.frankfurter import FrankfurterClient, ProviderError, parse, to_usd_base
from ledger.domain.fx import required_currencies, resolve
from ledger.domain.money import CurrencyMeta, convert_to_target

T = datetime(2026, 10, 4, tzinfo=UTC)
# 形状来自 2026-10-04 对公开接口的实测；数值为合成
SAMPLE = (
    b'[{"date":"2026-10-01","base":"EUR","quote":"CNY","rate":7.5748},'
    b'{"date":"2026-10-01","base":"EUR","quote":"NZD","rate":2.0121},'
    b'{"date":"2026-10-01","base":"EUR","quote":"USD","rate":1.1298},'
    b'{"date":"2026-10-02","base":"EUR","quote":"CNY","rate":7.5259},'
    b'{"date":"2026-10-02","base":"EUR","quote":"NZD","rate":2.0002},'
    b'{"date":"2026-10-02","base":"EUR","quote":"USD","rate":1.1225}]'
)


def test_fx06_request_contains_only_dates_currencies_and_provider() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=SAMPLE)

    http = httpx.Client(
        base_url="https://api.frankfurter.dev", transport=httpx.MockTransport(handler)
    )
    sets = FrankfurterClient(http, clock=lambda: T).fetch(
        date(2026, 10, 1), date(2026, 10, 4), ["NZD", "CNY", "EUR"]
    )
    assert len(seen) == 1
    req = seen[0]
    assert req.method == "GET" and req.url.path == "/v2/rates" and req.content == b""
    assert dict(req.url.params) == {
        "from": "2026-10-01",
        "to": "2026-10-04",
        "base": "EUR",
        "quotes": "CNY,NZD,USD",
        "providers": "ecb",
    }
    assert "authorization" not in {k.lower() for k in req.headers}
    assert [s.effective_date for s in sets] == [date(2026, 10, 1), date(2026, 10, 2)]


def test_usd_base_conversion_is_exact_enough() -> None:
    usd = to_usd_base(
        {"USD": Decimal("1.1225"), "NZD": Decimal("2.0002"), "CNY": Decimal("7.5259")}
    )
    assert usd["USD"] == 1 and usd["EUR"] == Decimal("1.1225")
    assert usd["NZD"] == Decimal("0.56119388061193880612")
    # 100 NZD → CNY 与直接用 EUR 交叉汇率 7.5259/2.0002 计算的两位结果一致
    sets = parse(SAMPLE, date(2026, 10, 1), date(2026, 10, 2), T)
    snap = resolve(
        date(2026, 10, 4), required_currencies("NZD"), {s.effective_date: s for s in sets}, {}
    )
    cny = convert_to_target(10000, CurrencyMeta("NZD", 2), "CNY", snap.usd())  # type: ignore[union-attr]
    direct = (Decimal(100) * Decimal("7.5259") / Decimal("2.0002")).quantize(Decimal("0.01"))
    assert Decimal(cny) / 100 == direct


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b'{"rates": {}}',
        b'[{"date":"2026-10-02","base":"USD","quote":"NZD","rate":1.7}]',
        b'[{"date":"2026-11-02","base":"EUR","quote":"NZD","rate":2.0}]',
        b'[{"date":"2026-10-02","base":"EUR","quote":"NZD","rate":"2.0"}]',
        b'[{"date":"2026-10-02","base":"EUR","quote":"NZD","rate":-2.0},'
        b'{"date":"2026-10-02","base":"EUR","quote":"USD","rate":1.1}]',
    ],
)
def test_malformed_responses_rejected(payload: bytes) -> None:
    with pytest.raises(ProviderError):
        parse(payload, date(2026, 10, 1), date(2026, 10, 2), T)


def test_day_without_usd_is_skipped_not_spliced() -> None:
    payload = b'[{"date":"2026-10-02","base":"EUR","quote":"NZD","rate":2.0}]'
    assert parse(payload, date(2026, 10, 2), date(2026, 10, 2), T) == []


def test_rows_before_start_are_dropped() -> None:
    """起始日为周六时 Frankfurter 会返回前一个周五的汇率：丢弃，不当作错误。"""
    payload = (
        b'[{"date":"2026-06-05","base":"EUR","quote":"USD","rate":1.1},'
        b'{"date":"2026-06-08","base":"EUR","quote":"USD","rate":1.2}]'
    )
    sets = parse(payload, date(2026, 6, 6), date(2026, 6, 8), T)
    assert [s.effective_date for s in sets] == [date(2026, 6, 8)]

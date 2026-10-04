"""Frankfurter v2 汇率适配器（ECB 数据源）。

请求只包含日期范围、币种与数据源（FX-06）；不发送金额、用户、备注或照片。
响应：[{"date": "YYYY-MM-DD", "base": "EUR", "quote": "NZD", "rate": 2.0002}, …]，周末无数据。
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

import httpx

from ledger.domain.fx import ProviderRateSet

BASE_URL = "https://api.frankfurter.dev"
PROVIDER = "ecb"
# EUR 基准 → USD 基准的除法会产生无限小数；保留 20 位小数（对两位折算结果无影响，ADR-0004）
USD_PLACES = Decimal(1).scaleb(-20)
_CTX = Context(prec=50)


class ProviderError(Exception):
    pass


def to_usd_base(eur_rates: dict[str, Decimal]) -> dict[str, Decimal]:
    """ECB 给出 1 EUR = x_c 单位币种 c。则 1 c = x_USD / x_c USD。"""
    usd = eur_rates.get("USD")
    if usd is None or usd <= 0:
        raise ProviderError("缺少 EUR/USD，无法换算 USD 基准")
    out = {"USD": Decimal(1), "EUR": usd.quantize(USD_PLACES, ROUND_HALF_EVEN)}
    with localcontext(_CTX):
        for code, x in eur_rates.items():
            if code in ("USD", "EUR"):
                continue
            if x <= 0:
                raise ProviderError(f"{code} 汇率非正")
            out[code] = (usd / x).quantize(USD_PLACES, ROUND_HALF_EVEN)
    return out


def parse(payload: bytes, start: date, end: date, fetched_at: datetime) -> list[ProviderRateSet]:
    try:
        rows = json.loads(payload, parse_float=Decimal, parse_int=Decimal)
    except ValueError as e:
        raise ProviderError("响应不是合法 JSON") from e
    if not isinstance(rows, list):
        raise ProviderError("响应格式不符")
    by_date: dict[date, dict[str, Decimal]] = defaultdict(dict)
    for r in rows:
        if not isinstance(r, dict) or r.get("base") != "EUR":
            raise ProviderError("响应基准币种不是 EUR")
        d = date.fromisoformat(str(r["date"]))
        if d < start:
            # 起始日为非工作日时，供应商会附带此前最近一个工作日的汇率（2026-10-04 生产初始化实测）
            continue
        if d > end:
            raise ProviderError("响应日期超出请求范围")
        rate = r["rate"]
        if not isinstance(rate, Decimal):
            raise ProviderError("汇率不是数值")
        by_date[d][str(r["quote"])] = rate
    out = []
    for d in sorted(by_date):
        if "USD" not in by_date[d]:
            continue  # 无法形成同日完整 USD 基准组
        out.append(ProviderRateSet(PROVIDER, d, to_usd_base(by_date[d]), 1, fetched_at))
    return out


class FrankfurterClient:
    def __init__(
        self, http: httpx.Client | None = None, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.http = http or httpx.Client(
            base_url=BASE_URL, timeout=10.0, headers={"User-Agent": "family-ledger"}
        )
        from ledger.application.context import utc_now

        self.clock = clock or utc_now

    def fetch(self, start: date, end: date, currencies: Iterable[str]) -> list[ProviderRateSet]:
        quotes = sorted({c for c in currencies if c != "EUR"} | {"USD"})
        resp = self.http.get(
            "/v2/rates",
            params={
                "from": start.isoformat(),
                "to": end.isoformat(),
                "base": "EUR",
                "quotes": ",".join(quotes),
                "providers": PROVIDER,
            },
        )
        if resp.status_code != 200:
            raise ProviderError(f"供应商返回 {resp.status_code}")
        return parse(resp.content, start, end, self.clock())

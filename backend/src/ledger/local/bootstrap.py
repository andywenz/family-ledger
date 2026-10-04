"""本地初始化：建表，然后走与生产相同的空账本初始化流程（ledger.application.initialize）。

运行：LEDGER_ENV=local uv run python -m ledger.local.bootstrap --admin <登录名> --name <显示名>
  --rates fixture  合成汇率（默认，离线可用，明确标注为合成）
  --rates ecb      从 Frankfurter 抓取真实 ECB 汇率（只发送日期与币种）
临时密码只打印一次到终端，不写入任何文件。
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from ledger.adapters.dynamo.schema import create_tables
from ledger.application.initialize import InitError, initialize
from ledger.domain.fx import ProviderRateSet
from ledger.runtime import build

FIXTURE = {"NZD": "0.58", "CNY": "0.14", "USD": "1", "AUD": "0.66", "EUR": "1.12"}


class FixtureRates:
    """合成汇率（仅本地开发，不是真实汇率）：工作日每天一组。"""

    def fetch(self, start: date, end: date, currencies: Iterable[str]) -> list[ProviderRateSet]:
        out, d = [], start
        while d <= end:
            if d.weekday() < 5:
                rates = {k: Decimal(v) for k, v in FIXTURE.items()}
                out.append(ProviderRateSet("ecb", d, rates, 1, datetime.now(UTC)))
            d += timedelta(days=1)
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--rates", choices=["fixture", "ecb"], default="fixture")
    ap.add_argument("--days", type=int, default=120)
    args = ap.parse_args()

    rt = build()
    if not rt.settings.is_local:
        raise SystemExit("本命令只用于本地；生产请用 python -m ledger.ops.initialize")
    ctx = rt.ctx
    create_tables(ctx.store.client, ctx.store.table, ctx.store.journal_table)
    if args.rates == "ecb":
        from ledger.adapters.frankfurter import FrankfurterClient

        source: object = FrankfurterClient()
    else:
        source = FixtureRates()
        print("使用合成汇率（仅供本地开发，不是真实汇率）")
    try:
        r = initialize(
            ctx,
            rt.auth.idp,
            source,  # type: ignore[arg-type]
            admin_login=args.admin,
            display_name=args.name,
            today=datetime.now(UTC).date(),
            days=args.days,
        )
    except InitError as e:
        raise SystemExit(f"初始化未完成：{e}") from None
    print(
        f"币种新增 {r.currencies_added}，汇率组新增 {r.rate_sets_added}"
        f"（最近 {r.latest_rate_date}）"
    )
    if r.temporary_password:
        print(f"已创建系统管理员 {r.admin_login}。临时密码（仅显示一次）：{r.temporary_password}")
    else:
        print(f"登录名 {r.admin_login} 已存在，跳过创建")


if __name__ == "__main__":
    main()

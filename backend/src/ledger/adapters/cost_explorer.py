"""AWS Cost Explorer：按 Project 费用标签分组的当月至今费用（每次请求 USD 0.01，每日只调一次）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

PROJECT_TAG = "Project"
PROJECT = "family-ledger"


@dataclass(frozen=True)
class MonthCost:
    tagged: Decimal  # 带 Project=family-ledger 标签
    untagged: Decimal  # 未打 Project 标签（本账户专用于本项目，计入账单）
    currency: str


class CostExplorerClient:
    def __init__(self, client: Any = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("ce", region_name="us-east-1")  # Cost Explorer 只有全局端点
        self._c = client

    def month_to_date(self, start: date, end: date) -> MonthCost:
        """[start, end) 的未混合成本；其他项目标签的费用不计入。"""
        r = self._c.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "TAG", "Key": PROJECT_TAG}],
        )
        tagged = untagged = Decimal(0)
        currency = "USD"
        for period in r.get("ResultsByTime", []):
            for g in period.get("Groups", []):
                metric = g["Metrics"]["UnblendedCost"]
                amount, currency = Decimal(metric["Amount"]), metric["Unit"]
                key = g["Keys"][0]
                if key == f"{PROJECT_TAG}${PROJECT}":
                    tagged += amount
                elif key == f"{PROJECT_TAG}$":
                    untagged += amount
        return MonthCost(tagged, untagged, currency)

"""AWS Cost Explorer：按 Project 费用标签分组的当月至今费用（每次请求 USD 0.01，每日只调一次）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

PROJECT_TAG = "Project"
PROJECT = "family-ledger"


CREDIT_TYPES = ("Credit", "Refund")  # 抵扣额度、退款：负数，单独列出


@dataclass(frozen=True)
class MonthCost:
    tagged: Decimal  # 抵扣前用量：带 Project=family-ledger 标签
    untagged: Decimal  # 抵扣前用量：未打 Project 标签（本账户专用于本项目，计入账单）
    credits: Decimal  # 已用抵扣额度与退款（≤ 0）
    currency: str


class CostExplorerClient:
    def __init__(self, client: Any = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("ce", region_name="us-east-1")  # Cost Explorer 只有全局端点
        self._c = client

    def month_to_date(self, start: date, end: date) -> MonthCost:
        """[start, end) 的未混合成本，按项目标签与记录类型分组（仍是一次请求）；
        其他项目标签的费用不计入。"""
        r = self._c.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[
                {"Type": "TAG", "Key": PROJECT_TAG},
                {"Type": "DIMENSION", "Key": "RECORD_TYPE"},
            ],
        )
        tagged = untagged = credits = Decimal(0)
        currency = "USD"
        for period in r.get("ResultsByTime", []):
            for g in period.get("Groups", []):
                metric = g["Metrics"]["UnblendedCost"]
                amount, currency = Decimal(metric["Amount"]), metric["Unit"]
                tag, record_type = g["Keys"][0], (g["Keys"][1:] or ["Usage"])[0]
                if tag not in (f"{PROJECT_TAG}${PROJECT}", f"{PROJECT_TAG}$"):
                    continue
                if record_type in CREDIT_TYPES:
                    credits += amount
                elif tag == f"{PROJECT_TAG}${PROJECT}":
                    tagged += amount
                else:
                    untagged += amount
        return MonthCost(tagged, untagged, credits, currency)

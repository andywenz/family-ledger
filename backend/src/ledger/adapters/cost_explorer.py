"""AWS Cost Explorer：按 Project 费用标签分组的当月至今费用（每次请求 USD 0.01，每日只调一次）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

PROJECT_TAG = "Project"
PROJECT = "family-ledger"


MIN_BREAKDOWN = Decimal("0.01")  # 未打标签用量达到此值才拆分（每次请求 USD 0.01）
CREDIT_TYPES = ("Credit", "Refund")  # 抵扣额度、退款：负数，单独列出


@dataclass(frozen=True)
class MonthCost:
    tagged: Decimal  # 抵扣前用量：带 Project=family-ledger 标签
    untagged: Decimal  # 抵扣前用量：未打 Project 标签（本账户专用于本项目，计入账单）
    credits: Decimal  # 已用抵扣额度与退款（≤ 0）
    currency: str
    untagged_by_service: dict[str, Decimal] = field(default_factory=dict)  # 未打标签用量的服务构成


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
        by_service = self._untagged_by_service(start, end) if untagged >= MIN_BREAKDOWN else {}
        return MonthCost(tagged, untagged, credits, currency, by_service)

    def _untagged_by_service(self, start: date, end: date) -> dict[str, Decimal]:
        """未打标签的用量按服务拆分（再一次请求，仅在有未打标签用量时发出）。

        单次请求最多按两个维度分组，已被“标签＋记录类型”占用，所以另起一次。
        只含 Usage：抵扣额度与退款不是未打标签的用量。"""
        r = self._c.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            Filter={
                "And": [
                    {"Tags": {"Key": PROJECT_TAG, "MatchOptions": ["ABSENT"]}},
                    {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Usage"]}},
                ]
            },
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        out: dict[str, Decimal] = {}
        for period in r.get("ResultsByTime", []):
            for g in period.get("Groups", []):
                amount = Decimal(g["Metrics"]["UnblendedCost"]["Amount"])
                out[g["Keys"][0]] = out.get(g["Keys"][0], Decimal(0)) + amount
        return out

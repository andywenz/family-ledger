"""OPS：每日同步 AWS 账单（Cost Explorer 替身，离线）——换算 NZD、标签覆盖、按账单告警。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from ledger.adapters.cost_explorer import CostExplorerClient
from ledger.application import costs, maintenance

from .conftest import World


class FakeCe:
    """模拟 boto3 Cost Explorer：记录请求区间，返回按 Project 标签分组的费用。"""

    def __init__(self, groups: dict[str, str], services: dict[str, str] | None = None) -> None:
        self.services = services or {}
        # 键为 "标签值|记录类型"，如 "Project$family-ledger|Usage"；省略记录类型即 Usage
        self.groups = groups
        self.calls: list[dict] = []

    def get_cost_and_usage(self, **kw: object) -> dict:
        self.calls.append(kw)
        if kw["GroupBy"] == [{"Type": "DIMENSION", "Key": "SERVICE"}]:  # 未打标签用量按服务拆分
            return {
                "ResultsByTime": [
                    {
                        "Groups": [
                            {
                                "Keys": [k],
                                "Metrics": {"UnblendedCost": {"Amount": v, "Unit": "USD"}},
                            }
                            for k, v in self.services.items()
                        ]
                    }
                ]
            }
        return {
            "ResultsByTime": [
                {
                    "Groups": [
                        {
                            "Keys": [k.split("|")[0], (k.split("|") + ["Usage"])[1]],
                            "Metrics": {"UnblendedCost": {"Amount": v, "Unit": "USD"}},
                        }
                        for k, v in self.groups.items()
                    ]
                }
            ]
        }


def test_sync_converts_to_nzd_and_triggers_billed_alert(world: World) -> None:
    w = world
    ce = FakeCe(
        {
            "Project$family-ledger": "7.00",
            "Project$": "0.28",
            "Project$other-app": "50",
            "Project$|Credit": "-7.28",  # 抵扣额度全额冲抵：告警仍按抵扣前用量
        },
        {"Amazon Bedrock": "0.17", "AWS Cost Explorer": "0.11"},
    )
    # 账单与告警按月全局存放，测试使用独立月份，避免与其他用例共享会话数据表时互相影响
    now = datetime(2026, 12, 10, 16, 0, tzinfo=UTC)
    out = maintenance.run(
        w.ctx,
        blobs=None,
        feishu_api=None,
        tasks=("bill", "budget"),
        now=now,
        cost_client=CostExplorerClient(ce),
    )
    # 当月 1 日至今天（不含），按 Project 标签分组；其他项目的费用不计入
    assert ce.calls[0]["TimePeriod"] == {"Start": "2026-12-01", "End": "2026-12-10"}
    assert ce.calls[0]["GroupBy"] == [
        {"Type": "TAG", "Key": "Project"},
        {"Type": "DIMENSION", "Key": "RECORD_TYPE"},
    ]
    assert out["bill"] == {"month": "2026-12", "usd": "7.28", "coverage": "partial"}
    billed = costs.get_costs(w.ctx, w.admin, "2026-12")["billed"]
    # 7.28 USD ÷ 0.56（最近的 NZD 汇率）= 13.00 NZD
    assert (billed["currency"], billed["amount"], billed["usd_amount"], billed["untagged_usd"]) == (
        "NZD",
        "13.00",
        "7.28",
        "0.28",
    )
    assert billed["tag_coverage"] == "partial" and billed["credits_usd"] == "-7.28"
    assert billed["untagged_by_service"] == {"Amazon Bedrock": "0.17", "AWS Cost Explorer": "0.11"}
    # 拆分请求只看没有 Project 标签的 Usage（抵扣不是未打标签的用量）
    assert len(ce.calls) == 2 and ce.calls[1]["Filter"]["And"][0] == {
        "Tags": {"Key": "Project", "MatchOptions": ["ABSENT"]}
    }
    assert out["budget_alerts"] == [80]  # 13.00 ≥ 12（80%），< 15（100%）
    alerts = costs.list_alerts(w.ctx, w.admin, "2026-12")
    assert alerts[0]["basis"] == "billed"


def test_first_of_month_syncs_previous_month_and_full_coverage(world: World) -> None:
    w = world
    ce = FakeCe({"Project$family-ledger": "2.80"})
    now = datetime(2027, 2, 1, 16, 0, tzinfo=UTC)
    out = costs.sync_bill(w.ctx, CostExplorerClient(ce), now)
    assert ce.calls[0]["TimePeriod"] == {"Start": "2027-01-01", "End": "2027-02-01"}
    assert out == {"month": "2027-01", "usd": "2.80", "coverage": "complete"}
    assert costs.get_costs(w.ctx, w.admin, "2027-01")["billed"]["amount"] == "5.00"
    assert len(ce.calls) == 1  # 没有未打标签用量：不发拆分请求，不多花 USD 0.01
    assert "untagged_by_service" not in costs.get_costs(w.ctx, w.admin, "2027-01")["billed"]


def test_client_sums_multiple_periods() -> None:
    ce = FakeCe({"Project$family-ledger": "1.10", "Project$": "0"})
    cost = CostExplorerClient(ce).month_to_date(date(2026, 10, 1), date(2026, 10, 5))
    assert (cost.tagged, cost.untagged, cost.credits, cost.currency) == (
        Decimal("1.10"),
        Decimal(0),
        Decimal(0),
        "USD",
    )


def test_negative_zero_is_shown_as_zero() -> None:
    assert costs._q2(Decimal("-0.0000000003")) == "0.00"

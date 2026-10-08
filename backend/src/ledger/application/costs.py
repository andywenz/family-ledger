"""本系统费用：应用估算、未知 usage、账单同步值分开展示；80%／100% 去重告警（需求 §9；OPS-05／06）。

应用估算只覆盖已记录 tokens 的模型调用；其余服务以 AWS 账单同步值为准。估算不是账单（ISE-030）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ledger import resources
from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.store import Tx
from ledger.domain.authz import Actor, require_system_admin
from ledger.domain.errors import DomainError

from .context import AppContext
from .guard import load_profile

BUDGET_NZD = Decimal("15")
THRESHOLDS = (80, 100)
PRICES_PATH = resources.path("config/model-prices.json")


def _prices() -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    return data


def _usd_to_nzd(ctx: AppContext, usd: Decimal, on: datetime) -> tuple[Decimal | None, str]:
    """预算换算使用最近的供应商 NZD 汇率（与账目快照分开）。"""
    from ledger.adapters.dynamo.codec import provider_set_from
    from ledger.adapters.dynamo.store import query_between

    items = query_between(
        ctx.store, keys.provider_rates(ctx.provider), "2000-01-01", on.date().isoformat()
    )
    if not items:
        return None, ""
    latest = provider_set_from(items[-1])
    nzd = latest.rates.get("NZD")
    if not nzd:
        return None, ""
    return usd / nzd, latest.effective_date.isoformat()


def month_estimate(ctx: AppContext, month: str) -> dict[str, Any]:
    prices = _prices()
    usd = Decimal(0)
    calls = unknown = 0
    unpriced: set[str] = set()
    for u in ctx.store.query_all(f"COST#{month}", "USAGE#"):
        calls += 1
        if u.get("unknown"):
            unknown += 1  # 未知 usage 单独计数，不按零计
            continue
        p = prices.get(u["model_id"])
        if p is None:
            unpriced.add(u["model_id"])
            continue
        usd += (
            Decimal(int(u["input_tokens"])) * Decimal(p["input_per_m"])
            + Decimal(int(u["output_tokens"])) * Decimal(p["output_per_m"])
        ) / 1_000_000
    return {
        "usd": usd,
        "calls": calls,
        "unknown": unknown,
        "unpriced": sorted(unpriced),
        "price_verified_at": min((p.get("verified_at", "") for p in prices.values()), default=""),
    }


def _latest_bill(ctx: AppContext, month: str) -> dict[str, Any] | None:
    bills = list(ctx.store.query_all(f"COST#{month}", "BILL#"))
    return bills[-1] if bills else None


def _q2(v: Decimal) -> str:
    q = v.quantize(Decimal("0.01"), ROUND_HALF_UP)
    return str(abs(q) if q == 0 else q)  # 不显示“-0.00”


def get_costs(ctx: AppContext, actor: Actor, month: str | None = None) -> dict[str, Any]:
    require_system_admin(actor)
    load_profile(ctx, actor)
    now = ctx.clock()
    month = month or now.strftime("%Y-%m")
    est = month_estimate(ctx, month)
    nzd, _ = _usd_to_nzd(ctx, est["usd"], now)
    bill = _latest_bill(ctx, month)
    out: dict[str, Any] = {
        "month": month,
        "budget_nzd": _q2(BUDGET_NZD),
        "thresholds": [{"percent": t, "amount_nzd": _q2(BUDGET_NZD * t / 100)} for t in THRESHOLDS],
        "estimated": {
            "known_nzd": _q2(nzd) if nzd is not None else "0.00",
            "by_service": {"bedrock_known_usd": _q2(est["usd"])},
            "model_calls": est["calls"],
            "unknown_usage_calls": est["unknown"],
            "as_of": keys.ts(now),
        },
        "billed": None,
    }
    if est["price_verified_at"]:
        out["estimated"]["price_verified_at"] = est["price_verified_at"]
    if bill:
        out["billed"] = {
            "amount": bill["amount"],
            "currency": bill["currency"],
            "as_of": bill["as_of"],
            "tag_coverage": bill.get("tag_coverage", "unknown"),
            **{
                k: bill[k]
                for k in ("usd_amount", "untagged_usd", "credits_usd", "untagged_by_service")
                if bill.get(k)
            },
        }
    return out


def record_bill(
    ctx: AppContext,
    *,
    month: str,
    amount: str,
    currency: str,
    as_of: datetime,
    tag_coverage: str,
    usd_amount: str | None = None,
    untagged_usd: str | None = None,
    credits_usd: str | None = None,
    untagged_by_service: dict[str, str] | None = None,
) -> None:
    """账单同步（每日一次，D5 由 Budgets／Cost Explorer 任务写入）。不高频调用付费 API。"""
    tx = Tx(ctx.store.table)
    tx.put(
        {
            "PK": f"COST#{month}",
            "SK": f"BILL#{keys.ts(as_of)}",
            "type": "bill",
            "amount": amount,
            "currency": currency,
            "as_of": keys.ts(as_of),
            "tag_coverage": tag_coverage,
            **({"usd_amount": usd_amount} if usd_amount is not None else {}),
            **({"untagged_usd": untagged_usd} if untagged_usd is not None else {}),
            **({"credits_usd": credits_usd} if credits_usd is not None else {}),
            **({"untagged_by_service": untagged_by_service} if untagged_by_service else {}),
        }
    )
    ctx.store.commit(tx)


def sync_bill(ctx: AppContext, client: Any, now: datetime | None = None) -> dict[str, Any] | None:
    """每日同步 AWS 账单（Cost Explorer，约 1 天延迟）：当月至今费用换算为 NZD 后保存，
    供费用页显示与按账单触发预算告警。每月 1 日同步上月全月。

    金额与告警按抵扣前用量计算（抵扣额度是临时的，用完或过期后需真实付费）；
    已用抵扣额度单独保存，页面另显示实付。"""
    now = now or ctx.clock()
    end = now.date()
    first = (end - timedelta(days=1)).replace(day=1)
    cost = client.month_to_date(first, end)
    usd = cost.tagged + cost.untagged
    if cost.currency != "USD":
        return None
    nzd, _ = _usd_to_nzd(ctx, usd, now)
    coverage = "complete" if cost.untagged < Decimal("0.01") else "partial"
    month = first.strftime("%Y-%m")
    record_bill(
        ctx,
        month=month,
        amount=_q2(nzd) if nzd is not None else _q2(usd),
        currency="NZD" if nzd is not None else "USD",
        as_of=now,
        tag_coverage=coverage,
        usd_amount=_q2(usd),
        untagged_usd=_q2(cost.untagged),
        credits_usd=_q2(cost.credits),
        untagged_by_service={
            k: _q2(v)
            for k, v in sorted(cost.untagged_by_service.items(), key=lambda kv: -kv[1])
            if v >= Decimal("0.005")
        },
    )
    return {"month": month, "usd": _q2(usd), "coverage": coverage}


def check_budget(ctx: AppContext, now: datetime | None = None) -> list[int]:
    """达到阈值时创建告警（同月同阈值去重），并向已绑定飞书的系统管理员发送。返回新触发的阈值。

    告警只含金额与阈值，不含任何家庭账目内容。超额继续服务，不暂停 AI（用户决定）。
    """
    from . import recognition
    from .feishu import SYSTEM_PARTITION

    now = now or ctx.clock()
    month = now.strftime("%Y-%m")
    est = month_estimate(ctx, month)
    est_nzd, _ = _usd_to_nzd(ctx, est["usd"], now)
    bill = _latest_bill(ctx, month)
    bill_nzd = Decimal(bill["amount"]) if bill and bill["currency"] == "NZD" else None
    triggered: list[int] = []
    for t in THRESHOLDS:
        limit = BUDGET_NZD * t / 100
        basis = None
        if bill_nzd is not None and bill_nzd >= limit:
            basis = "billed"
        elif est_nzd is not None and est_nzd >= limit:
            basis = "estimate"
        if basis is None:
            continue
        admins = _system_admins(ctx)
        alert_id = f"{month}-{t}"
        item = {
            "PK": f"COST#{month}",
            "SK": f"ALERT#{t}",
            "type": "alert",
            "alert_id": alert_id,
            "month": month,
            "threshold_percent": t,
            "basis": basis,
            "triggered_at": keys.ts(now),
            "estimate_nzd": _q2(est_nzd) if est_nzd is not None else None,
            "unknown_usage_calls": est["unknown"],
            "feishu_outbox": [],
        }
        outbox_items = [
            recognition.feishu_outbox_item(
                SYSTEM_PARTITION,
                "feishu_text",
                f"budget-{alert_id}-{uid}",
                open_id,
                now,
                text=_alert_text(basis, t, limit, est["unknown"]),
            )
            for uid, open_id in admins
        ]
        item["feishu_outbox"] = [[ob["PK"], ob["SK"]] for ob in outbox_items]
        tx = Tx(ctx.store.table)
        tx.put(item, condition="attribute_not_exists(PK)")  # 第一项：同月同阈值去重
        for ob in outbox_items:
            tx.put(ob, condition="attribute_not_exists(PK)")
        try:
            ctx.store.commit(tx)
            triggered.append(t)
        except DomainError:
            continue
    return triggered


def _alert_text(basis: str, t: int, limit: Decimal, unknown: int) -> str:
    src = "账单" if basis == "billed" else "应用估算"
    extra = f"另有 {unknown} 次调用用量未知。" if unknown else ""
    return (
        f"小家账本费用提醒：本月{src}已达到预算 {t}%（NZD {_q2(limit)}）。"
        f"服务继续运行，不会自动暂停。{extra}"
    )


def _system_admins(ctx: AppContext) -> list[tuple[str, str]]:
    out = []
    for link in ctx.store.query_all("USERS", ""):
        p = ctx.store.get(keys.user(link["SK"]), keys.PROFILE)
        if not p or not p.get("is_system_admin") or p.get("status") != "active":
            continue
        b = ctx.store.get(keys.user(link["SK"]), "FEISHU")
        if b and b.get("status") == "active":
            out.append((p["user_id"], b["open_id"]))
    return out


def list_alerts(ctx: AppContext, actor: Actor, month: str | None = None) -> list[dict[str, Any]]:
    require_system_admin(actor)
    load_profile(ctx, actor)
    month = month or ctx.clock().strftime("%Y-%m")
    out = []
    for a in ctx.store.query_all(f"COST#{month}", "ALERT#"):
        delivery = [{"channel": "admin_page", "status": "sent", "attempts": 1}]
        refs = a.get("feishu_outbox") or []
        if not refs:
            delivery.append({"channel": "feishu", "status": "failed", "attempts": 0})
        for pk, sk in refs:
            ob = ctx.store.get(pk, sk) or {}
            delivery.append(
                {
                    "channel": "feishu",
                    "status": ob.get("status", "unknown")
                    if ob.get("status") != "pending"
                    else "pending",
                    "attempts": int(ob.get("attempts", 0)),
                }
            )
        out.append(
            {
                "alert_id": a["alert_id"],
                "month": a["month"],
                "threshold_percent": int(a["threshold_percent"]),
                "basis": a["basis"],
                "triggered_at": a["triggered_at"],
                "delivery": delivery,
            }
        )
    return out

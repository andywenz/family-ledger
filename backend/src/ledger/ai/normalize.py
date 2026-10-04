"""语义校验与默认值（ADR-0011 步骤 5–6）。模型输出只是候选，逐字段按当前家庭目录核对。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ledger.domain.categories import KIND_FOR_ENTRY_TYPE, Catalog
from ledger.domain.errors import ValidationFailed
from ledger.domain.money import CurrencyMeta, parse_amount

CANDIDATE_TYPES = (
    "expense",
    "income",
    "internal_transfer",
    "exchange",
    "card_repayment",
    "receivable",
)
FALLBACK_LEAF = {
    "expense": "expense-09-02",
    "income": "income-03-02",
    "internal_transfer": "transfer-01-03",
    "exchange": "exchange-01-01",
    "card_repayment": "repayment-01-01",
    "receivable": "receivable-01-07",
}
REQUIRED = ("type", "business_date", "currency", "amount", "category_id")


@dataclass
class Draft:
    type: str | None
    business_date: str | None
    currency: str | None
    amount: str | None
    category_id: str | None
    payment_method: str | None
    note: str
    field_sources: dict[str, str] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def recompute(self) -> None:
        missing = [f for f in REQUIRED if getattr(self, f) in (None, "")]
        if self.type == "expense" and not self.payment_method:
            missing.append("payment_method")
        self.missing_fields = missing
        self.needs_review = sorted(set(self.needs_review))
        self.problems = sorted(set(self.problems))

    @property
    def status(self) -> str:
        return "ready" if not self.missing_fields and not self.problems else "incomplete"

    def as_dict(self) -> dict[str, Any]:
        return {
            k: getattr(self, k)
            for k in (
                "type",
                "business_date",
                "currency",
                "amount",
                "category_id",
                "payment_method",
                "note",
                "field_sources",
                "missing_fields",
                "needs_review",
                "problems",
            )
        }


def fallback_leaf(catalog: Catalog, kind: str) -> str | None:
    preferred = FALLBACK_LEAF.get(kind)
    c = catalog.by_id.get(preferred or "")
    if c is not None and c.status == "active" and c.redirect_to is None:
        return c.category_id
    active = [
        x
        for x in catalog.by_id.values()
        if x.is_leaf and x.kind == kind and x.status == "active" and x.redirect_to is None
    ]
    return sorted(active, key=lambda x: (x.sort, x.category_id))[-1].category_id if active else None


# 日期不在内：模型把“昨天”等相对日期换算后常标为 inferred，丢弃会出错；推断日期保留并标注待核对
DEFAULTABLE = ("currency", "payment_method")


def normalize(
    raw: dict[str, Any],
    *,
    catalog: Catalog,
    currencies: dict[str, CurrencyMeta],
    received: date,
    default_currency: str,
    default_method: str,
    max_major: Decimal,
) -> tuple[list[Draft], list[str]]:
    drafts: list[Draft] = []
    for c in raw.get("candidates", []):
        sources = dict(c.get("field_sources") or {})
        # 币种、方式有家庭默认值：模型只是“推断”的值不采用，改用带标注的默认值，
        # 避免模型猜的“现金”“人民币”冒充家庭默认（2026-10-04 真实评估发现，ADR-0015）
        for f in DEFAULTABLE:
            if sources.get(f) != "evidence":
                c = {**c, f: None}
        review = set(c.get("needs_review") or [])
        d = Draft(
            None,
            None,
            None,
            None,
            None,
            None,
            (c.get("note") or "").strip()[:200],
            field_sources={},
            needs_review=[],
        )
        # 类型
        if c.get("type") in CANDIDATE_TYPES:
            d.type = c["type"]
            d.field_sources["type"] = sources.get("type", "inferred")
        if "refund_intent" in review:
            d.problems.append("refund_not_supported")
            review.discard("refund_intent")
        # 日期
        bd = c.get("business_date")
        if bd:
            try:
                parsed = date.fromisoformat(bd)
                if parsed > received + timedelta(days=1) or parsed.year < 2000:
                    raise ValueError
                d.business_date = parsed.isoformat()
                d.field_sources["business_date"] = sources.get("business_date", "inferred")
            except ValueError:
                review.add("business_date")
        if d.business_date is None:
            d.business_date = received.isoformat()
            d.field_sources["business_date"] = "request_date"
        # 币种
        cur = c.get("currency")
        if cur in currencies:
            d.currency = cur
            d.field_sources["currency"] = sources.get("currency", "inferred")
        else:
            if cur:
                review.add("currency")
            if default_currency in currencies:
                d.currency = default_currency
                d.field_sources["currency"] = "family_default"
        # 金额：无证据保持为空，绝不补
        amt = c.get("amount")
        if amt is not None and d.currency:
            try:
                parse_amount(amt, currencies[d.currency], max_major=max_major)
                d.amount = amt
                d.field_sources["amount"] = sources.get("amount", "inferred")
            except ValidationFailed:
                review.add("amount")
        # 分类：必须是当前有效叶子且与类型一致
        cid = c.get("category_id")
        if d.type is not None:
            kind = KIND_FOR_ENTRY_TYPE[d.type]
            leaf = catalog.by_id.get(cid or "")
            if (
                leaf is not None
                and leaf.is_leaf
                and leaf.kind == kind
                and leaf.status == "active"
                and leaf.redirect_to is None
            ):
                d.category_id = leaf.category_id
                d.field_sources["category_id"] = sources.get("category_id", "inferred")
            else:
                d.category_id = fallback_leaf(catalog, kind)
                d.field_sources["category_id"] = "inferred"
                review.add("category_id")
        # 方式：支出缺省用家庭默认值；不计收支类型不填
        pm = c.get("payment_method")
        if d.type in ("expense", "income") and pm in ("credit_card", "debit_card", "cash"):
            d.payment_method = pm
            d.field_sources["payment_method"] = sources.get("payment_method", "inferred")
        elif d.type == "expense":
            d.payment_method = default_method
            d.field_sources["payment_method"] = "family_default"
        for f, s in d.field_sources.items():
            if s == "inferred":
                review.add(f)
        d.needs_review = sorted(review)
        d.recompute()
        drafts.append(d)
    return drafts, list(raw.get("input_issues") or [])

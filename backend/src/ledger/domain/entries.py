"""账目模型与规则（需求 §4、ADR-0002／0006）。

纯函数：不读写存储。应用层负责读取当前状态、解析汇率并在事务中提交结果。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .categories import Catalog
from .errors import DomainError, ValidationFailed, Violations
from .fx import FxSnapshot
from .money import CurrencyMeta, convert_to_target, parse_amount

ENTRY_TYPES = (
    "expense",
    "income",
    "refund",
    "internal_transfer",
    "exchange",
    "card_repayment",
    "receivable",
)
STAT_TYPES = frozenset({"expense", "income", "refund"})
PAYMENT_METHODS = ("credit_card", "debit_card", "cash")
NOTE_MAX = 200
MAX_ATTACHMENTS = 3
TRASH_DAYS = 30
MIN_BUSINESS_DATE = date(2000, 1, 1)
# 记录已发生的事项；允许比家庭当天晚 1 天以容纳跨时区录入（ADR-0006 第 9 条）
MAX_FUTURE_DAYS = 1
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Entry:
    entry_id: str
    family_id: str
    business_date: date
    type: str
    leaf_category_id: str
    currency: str
    amount_minor: int
    note: str
    payment_method: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    source: str
    version: int
    fx_snapshot: FxSnapshot
    nzd_minor: int
    cny_minor: int
    attachment_ids: tuple[str, ...] = ()
    refund_of: str | None = None
    refund_ids: tuple[str, ...] = ()
    refunded_minor: int = 0
    receipt_group_id: str | None = None
    state: str = "active"  # active | trashed
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    purge_after: datetime | None = None

    @property
    def month(self) -> str:
        return self.business_date.isoformat()[:7]

    @property
    def counts_in_stats(self) -> bool:
        return self.type in STAT_TYPES

    @property
    def refundable_minor(self) -> int:
        return self.amount_minor - self.refunded_minor if self.type == "expense" else 0


@dataclass(frozen=True)
class EntryInput:
    """已通过 Schema 校验的创建请求（金额仍为字符串）。"""

    business_date: str
    type: str
    amount: str
    leaf_category_id: str | None = None
    currency: str | None = None
    note: str = ""
    payment_method: str | None = None
    attachment_ids: tuple[str, ...] = ()
    refund_of: str | None = None


@dataclass(frozen=True)
class Limits:
    max_major: Decimal = Decimal("1000000")


@dataclass(frozen=True)
class Plan:
    """规则校验后的待提交内容。snapshot 为 None 表示需应用层解析汇率后调用 finalize。"""

    business_date: date
    type: str
    leaf_category_id: str
    currency: str
    amount_minor: int
    note: str
    payment_method: str | None
    attachment_ids: tuple[str, ...]
    refund_of: str | None
    keep_snapshot: FxSnapshot | None = None
    changed_fields: frozenset[str] = field(default_factory=frozenset)


def parse_business_date(text: str, today: date) -> date:
    # fromisoformat 也接受 20261004 等格式；契约只允许 YYYY-MM-DD
    if not isinstance(text, str) or not _DATE_RE.match(text):
        raise ValidationFailed("日期格式无效", fields=[])
    try:
        d = date.fromisoformat(text)
    except (TypeError, ValueError):
        raise ValidationFailed("日期格式无效", fields=[]) from None
    if d < MIN_BUSINESS_DATE or d > today + timedelta(days=MAX_FUTURE_DAYS):
        raise ValidationFailed("日期超出允许范围")
    return d


def check_payment_method(entry_type: str, method: str | None, v: Violations) -> None:
    if method is not None and method not in PAYMENT_METHODS:
        v.add("payment_method", "invalid")
    elif entry_type == "expense" and method is None:
        v.add("payment_method", "required")
    elif entry_type not in ("expense", "income", "refund") and method is not None:
        v.add("payment_method", "not_allowed")


def check_note(note: str, v: Violations) -> None:
    if not isinstance(note, str) or len(note) > NOTE_MAX:
        v.add("note", "too_long")


def plan_create(
    data: EntryInput,
    *,
    catalog: Catalog,
    currencies: Mapping[str, CurrencyMeta],
    today: date,
    limits: Limits,
    original: Entry | None = None,
) -> Plan:
    """创建账目（含退款）的规则校验。退款需传入原消费的当前状态。"""
    v = Violations()
    if data.type not in ENTRY_TYPES:
        raise ValidationFailed("记录类型无效", fields=[])
    bdate = parse_business_date(data.business_date, today)
    check_note(data.note, v)
    if len(data.attachment_ids) > MAX_ATTACHMENTS:
        v.add("attachment_ids", "too_many")

    if data.type == "refund":
        if data.refund_of is None:
            v.add("refund_of", "required")
        for name in ("leaf_category_id", "currency", "payment_method"):
            if getattr(data, name) is not None:
                v.add(name, "not_allowed_for_refund")
        v.raise_if_any()
        assert original is not None, "应用层必须加载原消费"
        currency = currencies.get(original.currency)
        if currency is None:
            raise ValidationFailed("币种未启用")
        amount_minor = parse_amount(data.amount, currency, max_major=limits.max_major)
        check_refund(original, amount_minor, currency.code)
        return Plan(
            business_date=bdate,
            type="refund",
            leaf_category_id=original.leaf_category_id,
            currency=original.currency,
            amount_minor=amount_minor,
            note=data.note,
            payment_method=original.payment_method,
            attachment_ids=data.attachment_ids,
            refund_of=original.entry_id,
        )

    if data.refund_of is not None:
        v.add("refund_of", "only_for_refund")
    if data.leaf_category_id is None:
        v.add("leaf_category_id", "required")
    if data.currency is None:
        v.add("currency", "required")
    check_payment_method(data.type, data.payment_method, v)
    v.raise_if_any()
    assert data.leaf_category_id is not None and data.currency is not None
    currency = currencies.get(data.currency)
    if currency is None:
        raise ValidationFailed("币种未在本家庭启用", fields=[])
    amount_minor = parse_amount(data.amount, currency, max_major=limits.max_major)
    catalog.require_selectable(data.leaf_category_id, data.type)
    return Plan(
        business_date=bdate,
        type=data.type,
        leaf_category_id=data.leaf_category_id,
        currency=currency.code,
        amount_minor=amount_minor,
        note=data.note,
        payment_method=data.payment_method,
        attachment_ids=data.attachment_ids,
        refund_of=None,
    )


def check_refund(
    original: Entry, amount_minor: int, currency: str, *, already_counted: int = 0
) -> None:
    """退款必须关联同家庭有效原消费、同币种、累计不超过原金额（需求 4.3）。

    already_counted：修改既有退款时，该退款原金额已计入 original.refunded_minor 的部分。
    """
    if original.type != "expense" or original.state != "active":
        raise DomainError("退款必须关联有效的支出", code="refund_target_invalid")
    if original.currency != currency:
        raise DomainError("退款必须与原消费同币种", code="refund_currency_mismatch")
    remaining = original.amount_minor - original.refunded_minor + already_counted
    if amount_minor > remaining:
        raise DomainError(
            "退款超过原消费尚可退款金额",
            code="refund_exceeds_remaining",
            current={"refundable_minor": remaining},
        )


def finalize(
    plan: Plan,
    snapshot: FxSnapshot,
    currencies: Mapping[str, CurrencyMeta],
) -> tuple[int, int]:
    """按快照计算合 NZD／合 CNY 分值。"""
    meta = currencies[plan.currency]
    rates = snapshot.usd()
    return (
        convert_to_target(plan.amount_minor, meta, "NZD", rates),
        convert_to_target(plan.amount_minor, meta, "CNY", rates),
    )


PATCHABLE = frozenset(
    {
        "business_date",
        "type",
        "leaf_category_id",
        "currency",
        "amount",
        "note",
        "payment_method",
        "attachment_ids",
        "refund_of",
    }
)


def plan_update(
    old: Entry,
    patch: Mapping[str, Any],
    *,
    catalog: Catalog,
    currencies: Mapping[str, CurrencyMeta],
    today: date,
    limits: Limits,
    new_original: Entry | None = None,
) -> Plan:
    """修改同一账目（ADR-0006）。

    patch 只含变化字段。new_original：改为退款时的目标原消费（当前状态）；
    修改既有退款金额时为其原消费。返回 Plan；keep_snapshot 非空表示沿用原快照。
    """
    unknown = set(patch) - PATCHABLE
    if unknown:
        raise ValidationFailed(f"不可修改的字段：{sorted(unknown)}")
    if old.state != "active":
        raise DomainError("回收站中的账目不能修改", code="validation_failed")

    v = Violations()
    new_type = patch.get("type", old.type)
    if new_type not in ENTRY_TYPES:
        raise ValidationFailed("记录类型无效")
    has_refunds = bool(old.refund_ids) or old.refunded_minor > 0

    # 退款不能改类型，也不能改随原消费的字段
    if old.type == "refund":
        if new_type != "refund":
            raise DomainError("退款不能改成其他类型", code="type_change_not_allowed")
        for name in ("leaf_category_id", "currency", "payment_method", "refund_of"):
            if name in patch:
                v.add(name, "follows_original")
    if has_refunds:
        if new_type != old.type:
            raise DomainError("已有关联退款的消费不能改类型", code="has_linked_refunds")
        if "currency" in patch and patch["currency"] != old.currency:
            raise DomainError("已有关联退款的消费不能改币种", code="has_linked_refunds")
    if "refund_of" in patch and not (old.type == "expense" and new_type == "refund"):
        if old.type != "refund":
            v.add("refund_of", "only_for_refund")
    v.raise_if_any()

    bdate = (
        parse_business_date(patch["business_date"], today)
        if "business_date" in patch
        else old.business_date
    )
    note = patch.get("note", old.note)
    check_note(note, v)
    attachments = tuple(patch.get("attachment_ids", old.attachment_ids))
    if len(attachments) > MAX_ATTACHMENTS:
        v.add("attachment_ids", "too_many")

    # ── 支出 → 退款 ──
    if old.type == "expense" and new_type == "refund":
        if patch.get("refund_of") is None:
            v.add("refund_of", "required")
        v.raise_if_any()
        assert new_original is not None, "应用层必须加载目标原消费"
        if new_original.entry_id == old.entry_id:
            raise DomainError("不能关联自身", code="refund_target_invalid")
        currency = currencies[new_original.currency]
        amount_text = patch.get("amount")
        amount_minor = (
            parse_amount(amount_text, currency, max_major=limits.max_major)
            if amount_text is not None
            else _rescale(old.amount_minor, currencies[old.currency], currency)
        )
        check_refund(new_original, amount_minor, currency.code)
        currency_changed = currency.code != old.currency
        return Plan(
            business_date=bdate,
            type="refund",
            leaf_category_id=new_original.leaf_category_id,
            currency=currency.code,
            amount_minor=amount_minor,
            note=note,
            payment_method=new_original.payment_method,
            attachment_ids=attachments,
            refund_of=new_original.entry_id,
            keep_snapshot=None
            if (currency_changed or "business_date" in patch)
            else old.fx_snapshot,
            changed_fields=frozenset(patch) | {"leaf_category_id", "payment_method", "refund_of"},
        )

    # ── 其他情况 ──
    currency_code = patch.get("currency", old.currency)
    if currency_code not in currencies:
        raise ValidationFailed("币种未在本家庭启用")
    currency = currencies[currency_code]
    if "amount" in patch:
        amount_minor = parse_amount(patch["amount"], currency, max_major=limits.max_major)
    elif currency_code != old.currency:
        amount_minor = _rescale(old.amount_minor, currencies[old.currency], currency)
    else:
        amount_minor = old.amount_minor

    if old.type == "refund":
        assert new_original is not None, "修改退款需加载原消费"
        check_refund(new_original, amount_minor, old.currency, already_counted=old.amount_minor)
        leaf, method = old.leaf_category_id, old.payment_method
    else:
        leaf = patch.get("leaf_category_id", old.leaf_category_id)
        if "leaf_category_id" in patch or new_type != old.type:
            catalog.require_selectable(leaf, new_type)
        method = patch["payment_method"] if "payment_method" in patch else old.payment_method
        check_payment_method(new_type, method, v)
        if has_refunds and amount_minor < old.refunded_minor:
            raise DomainError("金额不能低于已退款累计", code="has_linked_refunds")
    v.raise_if_any()

    snapshot_kept = "business_date" not in patch and currency_code == old.currency
    return Plan(
        business_date=bdate,
        type=new_type,
        leaf_category_id=leaf,
        currency=currency_code,
        amount_minor=amount_minor,
        note=note,
        payment_method=method,
        attachment_ids=attachments,
        refund_of=old.refund_of,
        keep_snapshot=old.fx_snapshot if snapshot_kept else None,
        changed_fields=frozenset(patch),
    )


def _rescale(amount_minor: int, src: CurrencyMeta, dst: CurrencyMeta) -> int:
    """改币种但未改金额时，按数值不变重新表达小数位；精度不足则拒绝。"""
    if src.minor_digits == dst.minor_digits:
        return amount_minor
    if dst.minor_digits > src.minor_digits:
        return amount_minor * int(10 ** (dst.minor_digits - src.minor_digits))
    factor = int(10 ** (src.minor_digits - dst.minor_digits))
    if amount_minor % factor:
        raise ValidationFailed(f"{dst.code} 小数位不足，请同时修改金额")
    return amount_minor // factor


def apply_plan(
    old: Entry,
    plan: Plan,
    snapshot: FxSnapshot,
    nzd_minor: int,
    cny_minor: int,
    now: datetime,
) -> Entry:
    return replace(
        old,
        business_date=plan.business_date,
        type=plan.type,
        leaf_category_id=plan.leaf_category_id,
        currency=plan.currency,
        amount_minor=plan.amount_minor,
        note=plan.note,
        payment_method=plan.payment_method,
        attachment_ids=plan.attachment_ids,
        refund_of=plan.refund_of,
        fx_snapshot=snapshot,
        nzd_minor=nzd_minor,
        cny_minor=cny_minor,
        updated_at=now,
        version=old.version + 1,
    )


def trashed(entry: Entry, by: str, now: datetime) -> Entry:
    return replace(
        entry,
        state="trashed",
        deleted_at=now,
        deleted_by=by,
        purge_after=now + timedelta(days=TRASH_DAYS),
        updated_at=now,
        version=entry.version + 1,
    )


def restored(entry: Entry, now: datetime) -> Entry:
    if entry.state != "trashed":
        raise DomainError("该账目不在回收站", code="validation_failed")
    assert entry.purge_after is not None
    if now >= entry.purge_after:
        raise DomainError("已超过 30 天，无法恢复", code="purged")
    return replace(
        entry,
        state="active",
        deleted_at=None,
        deleted_by=None,
        purge_after=None,
        updated_at=now,
        version=entry.version + 1,
    )

"""实体 ↔ DynamoDB 项。金额为整数分值；汇率快照保存为十进制字符串 map。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from ledger.domain.authz import Membership
from ledger.domain.categories import Category
from ledger.domain.entries import Entry
from ledger.domain.fx import FxSnapshot, ManualRate, ProviderRateSet
from ledger.domain.money import CurrencyMeta, parse_rate

from . import keys

Item = dict[str, Any]


def dt_out(v: datetime | None) -> str | None:
    return keys.ts(v) if v is not None else None


def dt_in(v: str | None) -> datetime | None:
    if v is None:
        return None
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(UTC)


def _dt(v: str) -> datetime:
    out = dt_in(v)
    assert out is not None
    return out


# ── Entry ──


def entry_sk(e: Entry) -> str:
    return keys.entry(e.business_date, e.created_at, e.entry_id)


def entry_item(e: Entry, sk: str | None = None) -> Item:
    item: Item = {
        "PK": keys.family(e.family_id),
        "SK": sk or entry_sk(e),
        "type": "entry",
        "entry_id": e.entry_id,
        "family_id": e.family_id,
        "business_date": e.business_date.isoformat(),
        "entry_type": e.type,
        "leaf_category_id": e.leaf_category_id,
        "currency": e.currency,
        "amount_minor": e.amount_minor,
        "note": e.note,
        "payment_method": e.payment_method,
        "created_by": e.created_by,
        "created_at": dt_out(e.created_at),
        "updated_at": dt_out(e.updated_at),
        "source": e.source,
        "version": e.version,
        "fx_snapshot": e.fx_snapshot.to_dict(),
        "nzd_minor": e.nzd_minor,
        "cny_minor": e.cny_minor,
        "attachment_ids": list(e.attachment_ids),
        "refund_of": e.refund_of,
        "refund_ids": list(e.refund_ids),
        "refunded_minor": e.refunded_minor,
        "receipt_group_id": e.receipt_group_id,
        "state": e.state,
        "deleted_at": dt_out(e.deleted_at),
        "deleted_by": e.deleted_by,
        "purge_after": dt_out(e.purge_after),
    }
    return item


def entry_from(item: Item) -> Entry:
    return Entry(
        entry_id=item["entry_id"],
        family_id=item["family_id"],
        business_date=date.fromisoformat(item["business_date"]),
        type=item["entry_type"],
        leaf_category_id=item["leaf_category_id"],
        currency=item["currency"],
        amount_minor=int(item["amount_minor"]),
        note=item.get("note", ""),
        payment_method=item.get("payment_method"),
        created_by=item["created_by"],
        created_at=_dt(item["created_at"]),
        updated_at=_dt(item["updated_at"]),
        source=item["source"],
        version=int(item["version"]),
        fx_snapshot=FxSnapshot.from_dict(item["fx_snapshot"]),
        nzd_minor=int(item["nzd_minor"]),
        cny_minor=int(item["cny_minor"]),
        attachment_ids=tuple(item.get("attachment_ids") or ()),
        refund_of=item.get("refund_of"),
        refund_ids=tuple(item.get("refund_ids") or ()),
        refunded_minor=int(item.get("refunded_minor", 0)),
        receipt_group_id=item.get("receipt_group_id"),
        state=item.get("state", "active"),
        deleted_at=dt_in(item.get("deleted_at")),
        deleted_by=item.get("deleted_by"),
        purge_after=dt_in(item.get("purge_after")),
    )


# ── Category ──


def category_item(fid: str, c: Category) -> Item:
    return {
        "PK": keys.family(fid),
        "SK": keys.category(c.category_id),
        "type": "category",
        "category_id": c.category_id,
        "kind": c.kind,
        "name": c.name,
        "parent_id": c.parent_id,
        "status": c.status,
        "redirect_to": c.redirect_to,
        "sort": c.sort,
        "version": c.version,
    }


def category_from(item: Item) -> Category:
    return Category(
        category_id=item["category_id"],
        kind=item["kind"],
        name=item["name"],
        parent_id=item.get("parent_id"),
        status=item.get("status", "active"),
        redirect_to=item.get("redirect_to"),
        sort=int(item.get("sort", 0)),
        version=int(item.get("version", 1)),
    )


# ── Membership ──


def membership_from(fid: str, item: Item) -> Membership:
    return Membership(
        family_id=fid,
        user_id=item["user_id"],
        role=item["role"],
        status=item["status"],
        version=int(item["version"]),
    )


# ── 币种与汇率 ──


def currency_item(c: CurrencyMeta) -> Item:
    return {
        "PK": keys.CURRENCY_PK,
        "SK": c.code,
        "type": "currency",
        "code": c.code,
        "name_zh": c.name_zh,
        "minor_digits": c.minor_digits,
        "provider_supported": c.provider_supported,
    }


def currency_from(item: Item) -> CurrencyMeta:
    return CurrencyMeta(
        code=item["code"],
        minor_digits=int(item["minor_digits"]),
        name_zh=item.get("name_zh", ""),
        provider_supported=bool(item.get("provider_supported", False)),
    )


def provider_set_item(s: ProviderRateSet) -> Item:
    from ledger.domain.fx import decimal_str

    return {
        "PK": keys.provider_rates(s.provider),
        "SK": s.effective_date.isoformat(),
        "type": "rateset",
        "provider": s.provider,
        "rates": {c: decimal_str(v) for c, v in s.rates.items()},
        "revision": s.revision,
        "fetched_at": dt_out(s.fetched_at),
    }


def provider_set_from(item: Item) -> ProviderRateSet:
    return ProviderRateSet(
        provider=item["provider"],
        effective_date=date.fromisoformat(item["SK"]),
        rates={c: parse_rate(v) for c, v in item["rates"].items()},
        revision=int(item["revision"]),
        fetched_at=_dt(item["fetched_at"]),
    )


def manual_rate_from(item: Item) -> ManualRate:
    return ManualRate(
        effective_date=date.fromisoformat(item["date"]),
        currency=item["currency"],
        usd_value=parse_rate(item["usd_value"]),
        revision=int(item["revision"]),
        entered_at=_dt(item["entered_at"]),
    )

"""实体 → API 响应（contracts/openapi.yaml）。金额只输出已保存的分值与字符串。"""

from __future__ import annotations

from typing import Any

from ledger.adapters.dynamo import keys
from ledger.domain.authz import Membership, can_modify_entry
from ledger.domain.categories import Catalog
from ledger.domain.entries import Entry
from ledger.domain.money import CurrencyMeta, format_minor


def entry_view(
    e: Entry,
    catalog: Catalog,
    currencies: dict[str, CurrencyMeta],
    names: dict[str, tuple[str, bool]],
    viewer: Membership | None = None,
) -> dict[str, Any]:
    digits = currencies[e.currency].minor_digits if e.currency in currencies else 2
    display, left = names.get(e.created_by, ("", True))
    out: dict[str, Any] = {
        "entry_id": e.entry_id,
        "family_id": e.family_id,
        "business_date": e.business_date.isoformat(),
        "type": e.type,
        "category": catalog.resolve(e.leaf_category_id).to_dict(),
        "currency": e.currency,
        "amount": format_minor(e.amount_minor, digits),
        "payment_method": e.payment_method,
        "note": e.note,
        "created_by": e.created_by,
        "created_by_display": display,
        "created_by_left": left,
        "created_at": keys.ts(e.created_at),
        "updated_at": keys.ts(e.updated_at),
        "source": e.source,
        "version": e.version,
        "state": e.state,
        "fx_snapshot": e.fx_snapshot.to_dict(),
        "nzd_minor": e.nzd_minor,
        "cny_minor": e.cny_minor,
        "display_amounts": {
            "amount": format_minor(e.amount_minor, digits),
            "nzd": format_minor(e.nzd_minor, 2),
            "cny": format_minor(e.cny_minor, 2),
        },
        "counts_in_stats": e.counts_in_stats,
        "attachments": [{"attachment_id": a, "content_type": ""} for a in e.attachment_ids],
    }
    if e.state == "trashed":
        assert e.deleted_at is not None and e.purge_after is not None
        out["deleted_at"] = keys.ts(e.deleted_at)
        out["purge_after"] = keys.ts(e.purge_after)
    if e.refund_of:
        out["refund_of"] = e.refund_of
    if e.type == "expense":
        out["refund_ids"] = list(e.refund_ids)
        out["refunded_amount"] = format_minor(e.refunded_minor, digits)
        out["refundable_amount"] = format_minor(e.refundable_minor, digits)
    if e.receipt_group_id:
        out["receipt_group_id"] = e.receipt_group_id
    if viewer is not None:
        out["can_edit"] = can_modify_entry(viewer, e.created_by)
    return out

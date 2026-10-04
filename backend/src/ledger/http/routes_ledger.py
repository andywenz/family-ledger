"""账目、统计、回收站、分类、币种与汇率路由。"""

from __future__ import annotations

from datetime import date
from typing import Any

from ledger.application import categories, entries, rates
from ledger.domain.entries import EntryInput

from .app import Request, Response, handles

FILTER_KEYS = ("created_by", "type", "category_id", "payment_method")


def _filters(req: Request) -> dict[str, Any]:
    return {k: req.query[k] for k in FILTER_KEYS if k in req.query}


def entry_input(body: dict[str, Any]) -> EntryInput:
    return EntryInput(
        business_date=body["business_date"],
        type=body["type"],
        amount=body["amount"],
        leaf_category_id=body.get("leaf_category_id"),
        currency=body.get("currency"),
        note=body.get("note", ""),
        payment_method=body.get("payment_method"),
        attachment_ids=tuple(body.get("attachment_ids", ())),
        refund_of=body.get("refund_of"),
    )


@handles("getDashboard")
def dashboard(req: Request) -> Response:
    return Response(
        200,
        entries.dashboard(req.ctx, req.actor, req.path["fid"], req.query["month"], _filters(req)),
    )


@handles("listEntries")
def list_entries(req: Request) -> Response:
    return Response(
        200,
        entries.list_entries(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.query["month"],
            _filters(req),
            page_size=req.query.get("page_size", 50),
            cursor=req.query.get("cursor"),
        ),
    )


@handles("createEntry")
def create_entry(req: Request) -> Response:
    return Response(
        201,
        entries.create_entry(req.ctx, req.actor, req.path["fid"], req.key, entry_input(req.body)),
    )


@handles("previewEntry")
def preview_entry(req: Request) -> Response:
    b = req.body
    if "entry_id" in b:
        return Response(
            200,
            entries.preview_update(req.ctx, req.actor, req.path["fid"], b["entry_id"], b["patch"]),
        )
    return Response(200, entries.preview_entry(req.ctx, req.actor, req.path["fid"], entry_input(b)))


@handles("getEntry")
def get_entry(req: Request) -> Response:
    return Response(200, entries.get_entry(req.ctx, req.actor, req.path["fid"], req.path["eid"]))


@handles("updateEntry")
def update_entry(req: Request) -> Response:
    patch = dict(req.body)
    version = patch.pop("expected_version")
    return Response(
        200,
        entries.update_entry(
            req.ctx, req.actor, req.path["fid"], req.path["eid"], req.key, patch, version
        ),
    )


@handles("deleteEntry")
def delete_entry(req: Request) -> Response:
    return Response(
        200,
        entries.delete_entry(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.path["eid"],
            req.key,
            req.query["expected_version"],
            with_refunds=req.query.get("with_refunds", False),
        ),
    )


@handles("restoreEntry")
def restore_entry(req: Request) -> Response:
    return Response(
        200,
        entries.restore_entry(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.path["eid"],
            req.key,
            req.body["expected_version"],
        ),
    )


@handles("listTrash")
def list_trash(req: Request) -> Response:
    return Response(200, entries.list_trash(req.ctx, req.actor, req.path["fid"]))


@handles("getFamilyAction")
def get_action(req: Request) -> Response:
    return Response(
        200, entries.get_action(req.ctx, req.actor, req.path["fid"], req.path["action_id"])
    )


# ── 分类 ──


@handles("listCategories")
def list_categories(req: Request) -> Response:
    return Response(
        200,
        categories.list_categories(
            req.ctx,
            req.actor,
            req.path["fid"],
            include_inactive=req.query.get("include_inactive", False),
        ),
    )


@handles("createCategory")
def create_category(req: Request) -> Response:
    b = req.body
    return Response(
        201,
        categories.create_category(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.key,
            kind=b["kind"],
            name=b["name"],
            parent_id=b.get("parent_id"),
        ),
    )


@handles("updateCategory")
def update_category(req: Request) -> Response:
    b = dict(req.body)
    v = b.pop("expected_version")
    return Response(
        200,
        categories.update_category(
            req.ctx, req.actor, req.path["fid"], req.path["cid"], req.key, expected_version=v, **b
        ),
    )


@handles("deleteCategory")
def delete_category(req: Request) -> Response:
    categories.delete_category(
        req.ctx,
        req.actor,
        req.path["fid"],
        req.path["cid"],
        req.key,
        expected_version=req.query["expected_version"],
    )
    return Response(204)


@handles("mergeCategory")
def merge_category(req: Request) -> Response:
    return Response(
        200,
        categories.merge_category(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.path["cid"],
            req.key,
            target_id=req.body["target_id"],
            expected_version=req.body["expected_version"],
        ),
    )


# ── 币种与汇率 ──


@handles("listGlobalCurrencies")
def global_currencies(req: Request) -> Response:
    return Response(200, {"items": rates.list_global_currencies(req.ctx, req.actor)})


@handles("listFamilyCurrencies")
def family_currencies(req: Request) -> Response:
    return Response(
        200, {"items": rates.list_family_currencies(req.ctx, req.actor, req.path["fid"])}
    )


@handles("enableFamilyCurrency")
def enable_currency(req: Request) -> Response:
    return Response(
        201,
        rates.enable_family_currency(
            req.ctx, req.actor, req.path["fid"], req.key, req.body["code"]
        ),
    )


@handles("resolveRates")
def resolve_rates(req: Request) -> Response:
    codes = req.query.get("currencies")
    return Response(
        200,
        rates.resolve_rates(
            req.ctx,
            req.actor,
            req.path["fid"],
            date.fromisoformat(req.query["date"]),
            codes.split(",") if codes else None,
        ),
    )


@handles("listManualRates")
def list_manual(req: Request) -> Response:
    return Response(
        200,
        {
            "items": rates.list_manual_rates(
                req.ctx,
                req.actor,
                req.path["fid"],
                date.fromisoformat(req.query["from"]),
                date.fromisoformat(req.query["to"]),
            )
        },
    )


@handles("putManualRate")
def put_manual(req: Request) -> Response:
    b = req.body
    return Response(
        201,
        rates.put_manual_rate(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.key,
            d=date.fromisoformat(b["date"]),
            currency=b["currency"],
            usd_value=b["usd_value"],
            reason=b["reason"],
            expected_revision=b.get("expected_revision"),
        ),
    )

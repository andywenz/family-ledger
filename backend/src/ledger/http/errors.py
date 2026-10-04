"""业务错误码 → HTTP 状态码与统一错误体（contracts/openapi.yaml Error）。"""

from __future__ import annotations

from typing import Any

from ledger.domain.errors import DomainError

STATUS: dict[str, int] = {
    "validation_failed": 422,
    "rate_pending": 422,
    "refund_exceeds_remaining": 422,
    "refund_currency_mismatch": 422,
    "refund_target_invalid": 422,
    "type_change_not_allowed": 422,
    "category_kind_mismatch": 422,
    "category_inactive": 422,
    "category_cycle": 422,
    "batch_too_large": 422,
    "upload_invalid": 422,
    "unauthenticated": 401,
    "password_change_required": 401,
    "csrf_failed": 403,
    "forbidden": 403,
    "not_found": 404,
    "action_not_found": 404,
    "version_conflict": 409,
    "idempotency_key_reused": 409,
    "data_changing": 409,
    "data_changed": 409,
    "has_linked_refunds": 409,
    "category_in_use": 409,
    "last_admin": 409,
    "login_name_taken": 409,
    "rename_pending": 409,
    "candidate_stale": 409,
    "candidate_expired": 410,
    "invitation_expired": 410,
    "purged": 410,
    "unsupported_media_type": 415,
    "rate_limited": 429,
    "upstream_unknown": 503,
    "internal": 500,
}


def error_body(
    code: str, message: str, request_id: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details:
        err["details"] = details
    return {"error": err}


def from_domain(e: DomainError, request_id: str) -> tuple[int, dict[str, Any]]:
    details: dict[str, Any] = {}
    if e.fields:
        details["fields"] = [{"field": f.field, "code": f.code} for f in e.fields]
    if e.current is not None:
        details["current"] = e.current
    return STATUS.get(e.code, 500), error_body(e.code, e.message, request_id, details)

"""照片、导出与汇率重算路由。"""

from __future__ import annotations

from datetime import date

from ledger.application import attachments, exports, recompute

from .app import Request, Response, handles


@handles("createUploadIntent")
def upload_intent(req: Request) -> Response:
    b = req.body
    return Response(
        201,
        attachments.create_upload_intent(
            req.ctx,
            req.runtime.services["blobs"],
            req.actor,
            req.path["fid"],
            req.key,
            content_type=b["content_type"],
            size=b["bytes"],
            sha256=b["sha256"],
        ),
    )


@handles("completeUpload")
def complete_upload(req: Request) -> Response:
    return Response(
        200,
        attachments.complete_upload(
            req.ctx,
            req.runtime.services["blobs"],
            req.actor,
            req.path["fid"],
            req.path["aid"],
            req.key,
        ),
    )


@handles("getAttachmentUrl")
def attachment_url(req: Request) -> Response:
    return Response(
        200,
        attachments.download_url(
            req.ctx, req.runtime.services["blobs"], req.actor, req.path["fid"], req.path["aid"]
        ),
    )


@handles("createExport")
def create_export(req: Request) -> Response:
    b = req.body
    return Response(
        202,
        exports.create_export(
            req.ctx,
            req.runtime.services["exports"],
            req.actor,
            req.path["fid"],
            req.key,
            fmt=b["format"],
            month=b["month"],
            filters=b.get("filters"),
        ),
    )


@handles("getExport")
def get_export(req: Request) -> Response:
    return Response(
        200,
        exports.get_export(
            req.ctx, req.runtime.services["exports"], req.actor, req.path["fid"], req.path["jid"]
        ),
    )


@handles("createRecomputePreview")
def recompute_preview(req: Request) -> Response:
    b = req.body
    return Response(
        202,
        recompute.create_preview(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.key,
            date_from=date.fromisoformat(b["date_from"]),
            date_to=date.fromisoformat(b["date_to"]),
            currencies=b.get("currencies"),
        ),
    )


@handles("getRecompute")
def get_recompute(req: Request) -> Response:
    return Response(200, recompute.get_job(req.ctx, req.actor, req.path["fid"], req.path["jid"]))


@handles("confirmRecompute")
def confirm_recompute(req: Request) -> Response:
    return Response(
        202,
        recompute.confirm_action(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.path["jid"],
            req.key,
            preview_digest=req.body["preview_digest"],
        ),
    )

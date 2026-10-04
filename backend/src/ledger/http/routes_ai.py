"""AI 识别与候选路由（需求 §6.2）。"""

from __future__ import annotations

from ledger.application import candidates, recognition

from .app import Request, Response, handles


@handles("createRecognitionJob")
def create_job(req: Request) -> Response:
    return Response(
        202,
        recognition.create_job(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.key,
            text=req.body.get("text"),
            attachment_id=req.body.get("attachment_id"),
        ),
    )


@handles("getRecognitionJob")
def get_job(req: Request) -> Response:
    return Response(200, recognition.get_job(req.ctx, req.actor, req.path["fid"], req.path["jid"]))


@handles("listMyBatches")
def list_batches(req: Request) -> Response:
    return Response(
        200,
        {
            "items": candidates.list_batches(
                req.ctx, req.actor, req.path["fid"], req.query.get("status")
            )
        },
    )


@handles("getBatch")
def get_batch(req: Request) -> Response:
    return Response(200, candidates.get_batch(req.ctx, req.actor, req.path["fid"], req.path["bid"]))


@handles("updateCandidate")
def update_candidate(req: Request) -> Response:
    patch = dict(req.body)
    v = patch.pop("expected_version")
    return Response(
        200,
        candidates.update_candidate(
            req.ctx, req.actor, req.path["fid"], req.path["bid"], req.path["cid"], req.key, patch, v
        ),
    )


@handles("deleteCandidate")
def delete_candidate(req: Request) -> Response:
    candidates.delete_candidate(
        req.ctx,
        req.actor,
        req.path["fid"],
        req.path["bid"],
        req.path["cid"],
        req.key,
        req.query["expected_version"],
    )
    return Response(204)


@handles("splitCandidate")
def split_candidate(req: Request) -> Response:
    return Response(
        200,
        candidates.split_candidate(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.path["bid"],
            req.path["cid"],
            req.key,
            req.body["parts"],
            req.body["expected_version"],
        ),
    )


@handles("confirmCandidates")
def confirm(req: Request) -> Response:
    return Response(
        200,
        candidates.confirm(
            req.ctx, req.actor, req.path["fid"], req.path["bid"], req.key, req.body["items"]
        ),
    )


@handles("cancelBatch")
def cancel(req: Request) -> Response:
    return Response(
        200,
        candidates.cancel_batch(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.path["bid"],
            req.key,
            req.body["expected_version"],
        ),
    )

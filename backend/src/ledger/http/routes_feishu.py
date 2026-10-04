"""飞书绑定（网站侧）与平台回调路由（ADR-0013）。"""

from __future__ import annotations

from ledger.adapters.feishu import FeishuAuthError
from ledger.application import feishu

from .app import Request, Response, handles


@handles("createFeishuBindingCode")
def binding_code(req: Request) -> Response:
    return Response(
        201,
        feishu.create_binding_code(
            req.ctx,
            req.actor,
            req.key,
            default_family_id=req.body["default_family_id"],
            secret=req.runtime.auth.session_secret,
        ),
    )


@handles("updateFeishuBinding")
def update_binding(req: Request) -> Response:
    return Response(
        200,
        feishu.update_binding(
            req.ctx,
            req.actor,
            req.key,
            default_family_id=req.body["default_family_id"],
            expected_version=req.body["expected_version"],
        ),
    )


@handles("deleteFeishuBinding")
def delete_binding(req: Request) -> Response:
    feishu.delete_binding(req.ctx, req.actor, req.key)
    return Response(204)


@handles("feishuEvents")
def events(req: Request) -> Response:
    try:
        out = feishu.handle_event(
            req.ctx, req.runtime.services["feishu_secrets"], req.headers, req.raw_body
        )
    except FeishuAuthError:
        return Response(401)  # 不调用模型、不读取资源，只记录安全错误类别（FS-02）
    return Response(200, out)


@handles("feishuCardActions")
def cards(req: Request) -> Response:
    try:
        out = feishu.handle_card(
            req.ctx, req.runtime.services["feishu_secrets"], req.headers, req.raw_body
        )
    except FeishuAuthError:
        return Response(401)
    return Response(200, out)

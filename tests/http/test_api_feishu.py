"""飞书绑定与回调经 HTTP 全链路（离线：飞书替身）。响应均按 OpenAPI 校验。"""

from __future__ import annotations

import json
import secrets
from typing import Any

from ledger.http.app import Runtime, handle_event
from ledger.local.feishu import message_event, signed_request

from .conftest import CONTRACT_VIOLATIONS


def raw_call(rt: Runtime, path: str, headers: dict[str, str], raw: bytes) -> dict[str, Any]:
    from ledger.http import contract
    from ledger.http.app import match

    resp = handle_event(
        {
            "version": "2.0",
            "rawPath": "/v1" + path,
            "rawQueryString": "",
            "headers": headers,
            "cookies": [],
            "requestContext": {
                "http": {"method": "POST", "sourceIp": "1.2.3.4"},
                "requestId": secrets.token_hex(4),
            },
            "body": raw.decode(),
            "isBase64Encoded": False,
        },
        rt,
    )
    op = match("POST", path)
    assert op is not None
    body = json.loads(resp["body"]) if resp.get("body") else None
    CONTRACT_VIOLATIONS.extend(contract.response_errors(op[0].op_id, resp["statusCode"], body))
    return {"status": resp["statusCode"], "body": body}


def test_binding_via_api_and_signed_event(family: dict[str, Any], runtime: Runtime) -> None:
    fid, m = family["fid"], family["member"]
    status, code = m.post("/me/feishu-binding-code", {"default_family_id": fid})
    assert status == 201 and len(code["code"]) == 8
    open_id = f"ou_http_{fid[-8:]}"
    headers, raw = signed_request(message_event(open_id, text=f"绑定 {code['code']}"))
    r = raw_call(runtime, "/integrations/feishu/events", headers, raw)
    assert r["status"] == 200
    status, me = m.get("/me")
    assert me["feishu"]["status"] == "active" and me["feishu"]["default_family_id"] == fid
    status, b = m.patch(
        "/me/feishu-binding",
        {"default_family_id": fid, "expected_version": me["feishu"]["version"]},
    )
    assert status == 200
    assert m.delete("/me/feishu-binding")[0] == 204
    assert m.get("/me")[1]["feishu"]["status"] == "unbound"


def test_bad_signature_rejected_with_401(runtime: Runtime, family: dict[str, Any]) -> None:
    headers, raw = signed_request(message_event("ou_x", text="超市 45"))
    headers["x-lark-signature"] = "f" * 64
    assert raw_call(runtime, "/integrations/feishu/events", headers, raw)["status"] == 401
    assert raw_call(runtime, "/integrations/feishu/cards", headers, raw)["status"] == 401


def test_binding_code_requires_membership(family: dict[str, Any]) -> None:
    status, err = family["member"].post(
        "/me/feishu-binding-code", {"default_family_id": "not-my-family"}
    )
    assert status == 403


def test_unconfigured_feishu_returns_503_without_processing(runtime: Runtime) -> None:
    """生产 Secret 尚未填写时：飞书入口返回 503，网站其余功能不受影响。"""
    saved = runtime.services["feishu_secrets"]
    runtime.services["feishu_secrets"] = None
    try:
        for path in ("/integrations/feishu/events", "/integrations/feishu/cards"):
            assert (
                raw_call(runtime, path, {"content-type": "application/json"}, b"{}")["status"]
                == 503
            )
    finally:
        runtime.services["feishu_secrets"] = saved


def test_feishu_secret_parsing_tolerates_placeholder() -> None:
    from ledger.runtime import _feishu_secrets

    assert _feishu_secrets("Xk3randomGeneratedPlaceholder") is None  # CDK 初始随机值
    assert _feishu_secrets('{"app_id": "cli_x"}') is None  # 字段不全
    full = {
        k: "v" for k in ("app_id", "app_secret", "verification_token", "encrypt_key", "tenant_key")
    }
    assert _feishu_secrets(json.dumps(full)).app_id == "v"

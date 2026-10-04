"""OpenAPI 驱动的请求分发：解析 API Gateway HTTP API v2 事件 → 校验 → 鉴权 → 用例 → 响应。

Validate → Authorize → Execute → Audit（ISE-013）：契约校验与身份解析在用例之前完成，
业务授权与审计由用例在读取与提交时执行。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

from ledger.application.accounts import AuthDeps, authenticate
from ledger.application.context import AppContext
from ledger.domain.authz import Actor
from ledger.domain.errors import DomainError, Unauthenticated, ValidationFailed
from ledger.settings import Settings

from . import contract
from .errors import error_body, from_domain

log = logging.getLogger("ledger.http")
MAX_BODY = 64 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@dataclass
class Runtime:
    settings: Settings
    ctx: AppContext
    auth: AuthDeps
    services: dict[str, Any] = field(default_factory=dict)


@dataclass
class Request:
    runtime: Runtime
    op: contract.Operation
    path: dict[str, str]
    query: dict[str, Any]
    headers: dict[str, str]
    cookies: dict[str, str]
    body: Any
    source_ip: str
    request_id: str
    raw_body: bytes = b""  # 飞书回调验签需要原始字节
    _actor: Actor | None = None

    @property
    def ctx(self) -> AppContext:
        return self.runtime.ctx

    @property
    def bearer(self) -> str:
        h = self.headers.get("authorization", "")
        if not h.lower().startswith("bearer "):
            raise Unauthenticated("请先登录")
        return h[7:].strip()

    @property
    def actor(self) -> Actor:
        if self._actor is None:
            self._actor = authenticate(self.ctx, self.runtime.auth, self.bearer)
        return self._actor

    @property
    def key(self) -> str:
        return self.headers["idempotency-key"]


@dataclass
class Response:
    status: int
    body: Any = None
    cookies: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)


Handler = Callable[[Request], Response]
HANDLERS: dict[str, Handler] = {}


def handles(op_id: str) -> Callable[[Handler], Handler]:
    if op_id not in contract.operations():
        raise KeyError(f"契约中没有 {op_id}")

    def deco(fn: Handler) -> Handler:
        HANDLERS[op_id] = fn
        return fn

    return deco


@dataclass(frozen=True)
class _Route:
    op: contract.Operation
    regex: re.Pattern[str]
    params: int


def _routes() -> list[_Route]:
    out = []
    for op in contract.operations().values():
        names = re.findall(r"{(\w+)}", op.path)
        pattern = "^" + re.sub(r"{(\w+)}", r"(?P<\1>[^/]+)", op.path) + "$"
        out.append(_Route(op, re.compile(pattern), len(names)))
    return sorted(out, key=lambda r: r.params)  # 静态段优先


_ROUTES: list[_Route] | None = None


def match(method: str, path: str) -> tuple[contract.Operation, dict[str, str]] | None:
    global _ROUTES
    if _ROUTES is None:
        _ROUTES = _routes()
    for r in _ROUTES:
        if r.op.method != method:
            continue
        m = r.regex.match(path)
        if m:
            return r.op, m.groupdict()
    return None


def _coerce_query(op: contract.Operation, raw: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    params = [
        *contract.spec()["paths"][op.path].get("parameters", []),
        *op.spec.get("parameters", []),
    ]
    for p in params:
        if "$ref" in p:
            p = contract.spec()["components"]["parameters"][p["$ref"].rsplit("/", 1)[-1]]
        if p["in"] != "query":
            continue
        name = p["name"]
        if name not in raw:
            if p.get("required"):
                raise ValidationFailed(f"缺少查询参数 {name}")
            continue
        schema = p["schema"]
        if "$ref" in schema:
            schema = contract.spec()["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
        value: Any = raw[name]
        if schema.get("type") == "integer":
            if not re.fullmatch(r"-?\d{1,9}", value):
                raise ValidationFailed(f"参数 {name} 应为整数")
            value = int(value)
        elif schema.get("type") == "boolean":
            if value not in ("true", "false"):
                raise ValidationFailed(f"参数 {name} 应为 true 或 false")
            value = value == "true"
        from jsonschema import Draft202012Validator

        errs = list(Draft202012Validator(schema).iter_errors(value))
        if errs:
            raise ValidationFailed(f"参数 {name} 无效")
        out[name] = value
    return out


def _response(
    status: int,
    body: Any,
    request_id: str,
    cookies: list[str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    h = {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "x-request-id": request_id,
        "x-content-type-options": "nosniff",
        **(headers or {}),
    }
    out: dict[str, Any] = {"statusCode": status, "headers": h}
    if body is not None:
        out["body"] = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if cookies:
        out["cookies"] = cookies
    return out


def handle_event(event: dict[str, Any], runtime: Runtime) -> dict[str, Any]:
    started = time.monotonic()
    rc = event.get("requestContext", {})
    request_id = rc.get("requestId") or secrets.token_hex(8)
    method = rc.get("http", {}).get("method", "GET").upper()
    raw_path = event.get("rawPath", "/")
    path = raw_path[3:] if raw_path.startswith("/v1/") else raw_path
    op_id, status, code, actor_id = "-", 500, "", ""
    try:
        found = match(method, path)
        if found is None:
            status, code = 404, "not_found"
            return _response(404, error_body("not_found", "接口不存在", request_id), request_id)
        op, params = found
        op_id = op.op_id
        handler = HANDLERS.get(op.op_id)
        if handler is None:
            status, code = 501, "internal"
            return _response(501, error_body("internal", "该接口尚未实现", request_id), request_id)
        for v in params.values():
            if not ID_RE.match(v):
                raise ValidationFailed("路径参数无效")
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        if op.spec.get("x-idempotent") and not KEY_RE.match(headers.get("idempotency-key", "")):
            raise ValidationFailed("缺少或无效的 Idempotency-Key")
        body: Any = None
        raw_body = event.get("body")
        data = b""
        if raw_body:
            data = base64.b64decode(raw_body) if event.get("isBase64Encoded") else raw_body.encode()
            if len(data) > MAX_BODY:
                raise ValidationFailed("请求体过大")
            try:
                body = json.loads(data)
            except ValueError:
                raise ValidationFailed("请求体不是合法 JSON") from None
        if body is None and contract.request_validator(op.op_id) is not None:
            if op.spec.get("requestBody", {}).get("required"):
                raise ValidationFailed("缺少请求体")
        if body is not None:
            contract.validate_request(op.op_id, body)
        cookies = dict(c.strip().split("=", 1) for c in event.get("cookies") or [] if "=" in c)
        query = _coerce_query(op, dict(parse_qsl(event.get("rawQueryString", ""))))
        req = Request(
            runtime,
            op,
            params,
            query,
            headers,
            cookies,
            body,
            rc.get("http", {}).get("sourceIp", ""),
            request_id,
            raw_body=data,
        )
        resp = handler(req)
        actor_id = req._actor.user_id if req._actor else ""
        status = resp.status
        return _response(resp.status, resp.body, request_id, resp.cookies, resp.headers)
    except DomainError as e:
        status, payload = from_domain(e, request_id)
        code = e.code
        headers = {}
        if e.code == "rate_limited" and isinstance(e.current, dict) and "retry_after" in e.current:
            headers["retry-after"] = str(e.current["retry_after"])
        return _response(status, payload, request_id, headers=headers)
    except Exception:
        log.exception("unhandled", extra={"request_id": request_id, "op": op_id})
        status, code = 500, "internal"
        return _response(500, error_body("internal", "服务器内部错误", request_id), request_id)
    finally:
        # 只记录 ID、状态与耗时，不记录请求体、备注、密码或令牌（ISE-015）
        log.info(
            json.dumps(
                {
                    "request_id": request_id,
                    "op": op_id,
                    "status": status,
                    "code": code,
                    "actor": actor_id,
                    "ms": int((time.monotonic() - started) * 1000),
                }
            )
        )

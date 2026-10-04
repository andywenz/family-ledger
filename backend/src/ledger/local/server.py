"""本地 API 服务：把 HTTP 请求转换为 API Gateway HTTP API v2 事件，交给同一分发器。

运行：LEDGER_ENV=local uv run python -m ledger.local.server  （端口 8787）
"""

from __future__ import annotations

import base64
import os
import secrets
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ledger.http.app import Runtime, handle_event
from ledger.runtime import build

_runtime: Runtime | None = None
EXTRA_ROUTES: list[Route] = []  # 本地 blob 等替身路由在此登记


def runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = build()
    return _runtime


async def api(request: Request) -> Response:
    body = await request.body()
    event: dict[str, Any] = {
        "version": "2.0",
        "rawPath": request.url.path,
        "rawQueryString": request.url.query,
        "headers": {k: v for k, v in request.headers.items() if k != "cookie"},
        "cookies": [c.strip() for c in request.headers.get("cookie", "").split(";") if c.strip()],
        "requestContext": {
            "http": {
                "method": request.method,
                "sourceIp": request.client.host if request.client else "127.0.0.1",
            },
            "requestId": secrets.token_hex(8),
        },
        "body": base64.b64encode(body).decode() if body else None,
        "isBase64Encoded": bool(body),
    }
    out = handle_event(event, runtime())
    resp = Response(
        content=out.get("body"), status_code=out["statusCode"], headers=out.get("headers")
    )
    for c in out.get("cookies", []):
        resp.raw_headers.append((b"set-cookie", c.encode()))
    return resp


def _background_worker() -> None:
    """本地替代 Streams→SQS→Worker：每秒派发并执行到期识别任务。"""
    import logging
    import threading
    import time

    from ledger.application import recognition

    def loop() -> None:
        rt = runtime()
        from ledger.application import feishu

        deps = recognition.WorkerDeps(
            model=rt.services["model"], blobs=rt.services["blobs"], feishu=rt.services["feishu_api"]
        )
        while True:
            try:
                recognition.process_pending(rt.ctx, deps)
                feishu.deliver_due(rt.ctx, rt.services["feishu_api"])
            except Exception:
                logging.getLogger("ledger.local").exception("background worker")
            time.sleep(1)

    threading.Thread(target=loop, daemon=True, name="local-worker").start()


def create_app() -> Starlette:
    methods = ["GET", "POST", "PATCH", "DELETE", "PUT"]
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    from ledger.local import blobs  # noqa: F401  # 登记本地 blob 路由

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        _background_worker()
        yield

    return Starlette(
        routes=[*EXTRA_ROUTES, Route("/v1/{path:path}", api, methods=methods)],
        lifespan=lifespan,
    )


if __name__ == "__main__":
    import uvicorn

    os.environ.setdefault("LEDGER_ENV", "local")
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("PORT", "8787")))

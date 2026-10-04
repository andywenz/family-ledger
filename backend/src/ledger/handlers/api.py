"""Lambda 入口：用户 HTTP API。"""

from __future__ import annotations

import logging
import time
from typing import Any

from ledger.http.app import Runtime, handle_event

logging.getLogger().setLevel(logging.INFO)
_runtime: Runtime | None = None


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    global _runtime
    if _runtime is None:
        from ledger.runtime import build

        _runtime = build()
    if event.get("warmup"):
        # 定时预热：只确保运行时已构建（飞书 3 秒限制、网站首屏并发请求）。
        # hold_ms 让同一轮的多次预热占用不同实例，从而一次预热多个实例。
        time.sleep(min(int(event.get("hold_ms", 0)), 2000) / 1000)
        return {"warm": True}
    return handle_event(event, _runtime)

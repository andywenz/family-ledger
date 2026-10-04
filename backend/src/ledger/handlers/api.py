"""Lambda 入口：用户 HTTP API。"""

from __future__ import annotations

import logging
from typing import Any

from ledger.http.app import Runtime, handle_event

logging.getLogger().setLevel(logging.INFO)
_runtime: Runtime | None = None


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    global _runtime
    if _runtime is None:
        from ledger.runtime import build

        _runtime = build()
    return handle_event(event, _runtime)

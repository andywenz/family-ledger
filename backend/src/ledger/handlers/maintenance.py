"""Lambda 入口：定时维护（EventBridge Scheduler）。事件 {"tasks": [...]} 选择任务。"""

from __future__ import annotations

import json
import logging
from typing import Any

from ledger.application import maintenance

log = logging.getLogger("ledger.maintenance")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    from ledger.runtime import build

    rt = build()
    tasks = tuple(event.get("tasks") or ("outbox", "trash", "orphans", "batches", "budget"))
    out = maintenance.run(
        rt.ctx, blobs=rt.services["blobs"], feishu_api=rt.services["feishu_api"], tasks=tasks
    )
    log.info(json.dumps(out))  # 只记录计数
    return out

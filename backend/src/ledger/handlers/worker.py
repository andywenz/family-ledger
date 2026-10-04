"""Lambda 入口：识别 Worker（SQS）与 outbox relay（DynamoDB Streams／定时兜底）。"""

from __future__ import annotations

import json
import logging
from typing import Any

from ledger.application import recognition
from ledger.http.app import Runtime

log = logging.getLogger("ledger.worker")
_runtime: Runtime | None = None


def _rt() -> Runtime:
    global _runtime
    if _runtime is None:
        from ledger.runtime import build

        _runtime = build()
    return _runtime


def _deps(rt: Runtime) -> recognition.WorkerDeps:
    return recognition.WorkerDeps(
        model=rt.services["model"], blobs=rt.services["blobs"], feishu=rt.services["feishu_api"]
    )


def worker_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """SQS 批量：每条消息一个 Job。失败的消息返回给 SQS 重投（有限次数后进入 DLQ）。"""
    rt = _rt()
    failures = []
    for record in event.get("Records", []):
        try:
            ref = json.loads(record["body"])
            status = recognition.run_job(rt.ctx, _deps(rt), ref["family_id"], ref["job_id"])
            log.info(json.dumps({"job_id": ref["job_id"], "status": status}))
        except Exception:
            log.exception("worker_failed", extra={"message_id": record.get("messageId")})
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def relay_handler(_event: dict[str, Any], _context: Any) -> dict[str, int]:  # pragma: no cover
    import os

    import boto3

    rt = _rt()
    sqs = boto3.client("sqs")
    queue = os.environ["LEDGER_RECOGNITION_QUEUE_URL"]
    n = recognition.relay_once(
        rt.ctx, lambda ref: sqs.send_message(QueueUrl=queue, MessageBody=json.dumps(ref))
    )
    return {"dispatched": n}

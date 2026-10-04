"""定时维护（storage-design §6 留存；ADR-0008）。每项任务幂等、有界，可重复运行。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.store import Tx
from ledger.domain.errors import DomainError

from . import attachments, costs, entries, feishu, recognition
from .context import AppContext

CANDIDATE_FIELDS_CLEARED = (
    "note",
    "amount",
    "c_type",
    "currency",
    "leaf_category_id",
    "payment_method",
    "business_date",
    "field_sources",
    "needs_review",
)


def expire_batches(ctx: AppContext, now: datetime | None = None) -> int:
    """过期批次：状态置 expired，候选正文清除，只保留 ID、状态、版本与已确认的 entry_id。"""
    now = now or ctx.clock()
    n = 0
    for shard in range(recognition.OUTBOX_SHARDS):
        r = ctx.store.client.query(
            TableName=ctx.store.table,
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND GSI1SK <= :now",
            ExpressionAttributeValues={
                ":pk": {"S": keys.work("candidate_expiry", shard)},
                ":now": {"S": keys.ts(now) + "~"},
            },
        )
        for raw in r.get("Items", []):
            pk, sk = raw["PK"]["S"], raw["SK"]["S"]
            batch = ctx.store.get(pk, sk)
            if batch is None:
                continue
            tx = Tx(ctx.store.table)
            new_status = batch["status"] if batch["status"] != "open" else "expired"
            tx.update(
                pk,
                sk,
                "SET #s = :st, version = version + :one REMOVE GSI1PK, GSI1SK",
                condition="version = :v",
                names={"#s": "status"},
                values={":st": new_status, ":one": 1, ":v": int(batch["version"])},
            )
            for c in ctx.store.query_all(pk, f"{sk}#CAND#"):
                clear = [f for f in CANDIDATE_FIELDS_CLEARED if f in c]
                if not clear:
                    continue
                expr = "REMOVE " + ", ".join(f"#f{i}" for i in range(len(clear)))
                if c["status"] == "open":
                    expr = "SET #s = :expired " + expr
                tx.update(
                    pk,
                    c["SK"],
                    expr,
                    names={
                        **{f"#f{i}": f for i, f in enumerate(clear)},
                        **({"#s": "status"} if c["status"] == "open" else {}),
                    },
                    values={":expired": "expired"} if c["status"] == "open" else None,
                )
            try:
                ctx.store.commit(tx)
                n += 1
            except DomainError:
                continue  # 期间被修改：下次再处理
    return n


def run(
    ctx: AppContext,
    *,
    blobs: Any,
    feishu_api: Any,
    tasks: tuple[str, ...] = ("outbox", "trash", "orphans", "batches", "budget"),
    now: datetime | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "outbox" in tasks:
        out["feishu_delivered"] = feishu.deliver_due(ctx, feishu_api, now)
    if "trash" in tasks:
        out["entries_purged"] = entries.purge_due(ctx, now)
    if "orphans" in tasks:
        out["photos_purged"] = attachments.purge_orphans(ctx, blobs, now)
    if "batches" in tasks:
        out["batches_expired"] = expire_batches(ctx, now)
    if "budget" in tasks:
        out["budget_alerts"] = costs.check_budget(ctx, now)
    return out

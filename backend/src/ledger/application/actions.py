"""幂等动作执行器（ADR-0008，ISE-008／018）。

回执与业务写入在同一事务中提交；同 key 同指纹返回原结果，同 key 不同指纹返回 409。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.store import Tx
from ledger.domain.authz import Actor
from ledger.domain.errors import DomainError, Retryable, StaleRead, ValidationFailed

from .context import AppContext

KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@dataclass
class Built[T]:
    tx: Tx
    response: T
    results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Scope:
    """回执位置：家庭范围或账号范围。"""

    pk: str
    sk: str
    family_id: str | None

    @classmethod
    def family(cls, fid: str, actor: Actor, key: str) -> Scope:
        return cls(keys.family(fid), keys.action(actor.user_id, key), fid)

    @classmethod
    def account(cls, actor: Actor, key: str) -> Scope:
        return cls(keys.user(actor.user_id), keys.user_action(key), None)


class _ReceiptExists(DomainError):
    code = "internal"

    def __init__(self, receipt: dict[str, Any] | None) -> None:
        super().__init__("回执已存在")
        self.receipt = receipt


def fingerprint(kind: str, scope: Scope, actor: Actor, body: Any) -> str:
    canonical = json.dumps(
        {"kind": kind, "scope": [scope.pk, scope.family_id], "actor": actor.user_id, "body": body},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def run[T](
    ctx: AppContext,
    actor: Actor,
    scope: Scope,
    key: str,
    kind: str,
    body: Any,
    build: Callable[[], Built[T]],
    replay: Callable[[dict[str, Any]], T],
) -> T:
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise ValidationFailed("Idempotency-Key 格式无效")
    fp = fingerprint(kind, scope, actor, body)

    def from_receipt(receipt: dict[str, Any]) -> T:
        if receipt.get("fingerprint") != fp:
            raise DomainError("该动作 ID 已用于不同的请求", code="idempotency_key_reused")
        return replay(receipt)

    last: Exception | None = None
    for attempt in range(ctx.max_attempts):
        existing = ctx.store.get(scope.pk, scope.sk)
        if existing is not None:
            return from_receipt(existing)
        built = build()
        now = ctx.clock()
        receipt = {
            "PK": scope.pk,
            "SK": scope.sk,
            "type": "action",
            "action_id": key,
            "actor": actor.user_id,
            "kind": kind,
            "fingerprint": fp,
            "status": "committed",
            "committed_at": keys.ts(now),
            "results": built.results,
        }
        # 回执放在第 0 项：若并发请求已提交同一 key，首先识别为重放
        receipt_tx = Tx(built.tx.table)
        receipt_tx.put(receipt, condition="attribute_not_exists(PK)", on_fail=_ReceiptExists)
        built.tx.items[0:0] = receipt_tx.items
        try:
            ctx.store.commit(built.tx, token_seed=f"{scope.pk}|{scope.sk}|{attempt}")
            return built.response
        except _ReceiptExists as e:
            if e.receipt is None:
                raise DomainError("回执状态异常", code="internal") from e
            return from_receipt(e.receipt)
        except (StaleRead, Retryable) as e:
            last = e
            continue
    raise DomainError("数据持续变化，请稍后重试", code="data_changing") from last


def get_receipt(ctx: AppContext, scope: Scope) -> dict[str, Any] | None:
    return ctx.store.get(scope.pk, scope.sk)

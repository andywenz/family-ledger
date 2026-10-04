"""应用层运行上下文：存储、时钟、ID 生成与限制参数。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ledger.adapters.dynamo.store import Store
from ledger.domain.entries import Limits
from ledger.domain.ids import new_id


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class AppContext:
    store: Store
    clock: Callable[[], datetime] = utc_now
    ids: Callable[[], str] = new_id
    limits: Limits = field(default_factory=Limits)
    provider: str = "ecb"
    max_attempts: int = 4  # StaleRead／并发冲突时重新计算的次数上限

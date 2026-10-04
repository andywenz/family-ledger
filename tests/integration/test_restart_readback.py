"""重启读回（D1 退出条件）：写入 → 重启 DynamoDB Local 容器 → 新连接读回。

默认跳过；运行：LEDGER_RESTART_TEST=1 uv run pytest tests/integration/test_restart_readback.py
证据层级：离线。进程／容器重启不是灾难恢复测试（ISE-024）。
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from ledger.adapters.dynamo.store import Store
from ledger.application import entries
from ledger.application.context import AppContext

from .conftest import ENDPOINT, World
from .helpers import expense, refund

pytestmark = pytest.mark.skipif(
    os.environ.get("LEDGER_RESTART_TEST") != "1", reason="需显式开启（会重启本地容器）"
)


def test_readback_after_container_restart(world: World) -> None:
    w = world
    e = expense(w, amount="88.8", note="重启前")
    r = refund(w, e["entry_id"], "8.8")
    before = entries.dashboard(w.ctx, w.admin, w.fid, "2026-10")

    subprocess.run(
        ["docker", "compose", "restart", "dynamodb"],
        check=True,
        capture_output=True,
    )
    fresh = Store.connect(w.ctx.store.table, w.ctx.store.journal_table, endpoint=ENDPOINT)
    for _ in range(30):
        try:
            fresh.client.list_tables()
            break
        except Exception:
            time.sleep(0.5)
    ctx2 = AppContext(store=fresh, clock=w.clock)
    got = entries.get_entry(ctx2, w.admin, w.fid, e["entry_id"])
    assert got["note"] == "重启前" and got["refund_ids"] == [r["entry_id"]]
    assert got["fx_snapshot"] == e["fx_snapshot"]
    after = entries.dashboard(ctx2, w.admin, w.fid, "2026-10")
    assert after["totals"] == before["totals"] and after["data_version"] == before["data_version"]

"""演示账目生成（离线）：确定性、类型覆盖、写入幂等、退款关联有效。"""

from __future__ import annotations

import datetime as dt

from ledger.application import repo
from ledger.ops import demo_data

from .conftest import World

START, END = dt.date(2026, 7, 6), dt.date(2026, 10, 5)


def test_generation_is_deterministic_and_covers_types() -> None:
    a = demo_data.generate(START, END)
    b = demo_data.generate(START, END)
    assert [(e.day, e.type, e.amount, e.note) for e in a] == [
        (e.day, e.type, e.amount, e.note) for e in b
    ]
    types = {e.type for e in a}
    assert types >= {
        "expense",
        "income",
        "refund",
        "internal_transfer",
        "exchange",
        "card_repayment",
        "receivable",
    }
    assert all(START <= e.day <= END and e.amount > 0 for e in a)
    by_seq = {e.seq: e for e in a}
    for r in (e for e in a if e.type == "refund"):
        orig = by_seq[r.refund_of_seq]
        assert orig.type == "expense" and orig.day <= r.day and r.amount <= orig.amount


def test_apply_writes_once_with_valid_refunds(world: World) -> None:
    """测试库只有 2026-09-20 起的汇率：取其中一段写入，重跑不重复。"""
    w = world
    entries = demo_data.generate(dt.date(2026, 9, 21), dt.date(2026, 10, 5))
    first = demo_data.apply(w.ctx, w.admin, w.fid, entries)
    again = demo_data.apply(w.ctx, w.admin, w.fid, entries)
    written = first.get("新建", 0)
    assert written > 20 and again.get("新建", 0) == 0 and again["重复执行命中"] == written
    rows = repo.month_entries(w.ctx, w.fid, "2026-09") + repo.month_entries(w.ctx, w.fid, "2026-10")
    assert len(rows) == written

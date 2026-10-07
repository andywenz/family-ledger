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


def test_english_demo_has_no_chinese_and_renames_categories(world: World) -> None:
    """--lang en：分类全部改为英文（可重复执行），账目分类与备注不含中文，写入成功。"""
    import json
    import re
    from pathlib import Path

    from ledger.ops import demo_en

    cjk = re.compile(r"[一-鿿]")
    # 初始分类都有英文名，且符合 1–24 字符、同级不重名
    seed = json.loads(
        (Path(__file__).resolve().parents[2] / "seed" / "categories.json").read_text()
    )
    for g in seed["categories"]:
        names = [
            demo_en.LEAF_EN.get((g["name"], c["name"])) or demo_en.CATEGORY_EN[c["name"]]
            for c in g["children"]
        ]
        assert len(set(names)) == len(names) and all(1 <= len(n) <= 24 for n in names)
        assert 1 <= len(demo_en.CATEGORY_EN[g["name"]]) <= 24
    entries = demo_en.localize(demo_data.generate(START, END))
    assert not [e for e in entries if cjk.search(e.note + e.parent + e.leaf)]

    w = world
    assert demo_en.rename_categories(w.ctx, w.admin, w.fid) > 60
    assert demo_en.rename_categories(w.ctx, w.admin, w.fid) == 0  # 重复执行不再改名
    assert not [c for c in repo.catalog(w.ctx, w.fid).by_id.values() if cjk.search(c.name)]
    stats = demo_data.apply(
        w.ctx, w.admin, w.fid, [e for e in entries if e.day >= dt.date(2026, 9, 21)]
    )
    assert stats.get("新建", 0) > 20
    rows = repo.month_entries(w.ctx, w.fid, "2026-10")
    assert rows and not [r for r in rows if cjk.search(r.note)]

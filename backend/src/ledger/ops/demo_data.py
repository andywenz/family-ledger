"""演示账目生成（合成数据，运维命令）。模拟一个新西兰双职工、有学龄孩子的家庭。

  AWS_PROFILE=<目标账户> AWS_DEFAULT_REGION=ap-southeast-2 \\
  LEDGER_TABLE=<TableName> LEDGER_DELETION_TABLE=<JournalTableName> \\
  uv run python -m ledger.ops.demo_data --account <账户 ID> --login <登录名> --family <家庭名> \\
      --start 2026-07-06 --end 2026-10-05 [--lang en] [--dry-run]

- --lang en：先把该家庭的初始分类改为英文，账目备注也用英文（见 demo_en.py）。

- 固定随机种子：同一参数生成相同数据；动作 ID 确定性派生，重复执行不会重复入账。
- 走与网站相同的 create_entry（校验、分类、汇率快照、审计、幂等），不直接写表。
- 只使用家庭当前目录中的分类与已启用币种；退款关联同家庭已生成的原支出。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

SEED = 20261005


@dataclass
class DemoEntry:
    seq: int
    day: dt.date
    type: str
    parent: str
    leaf: str
    amount: Decimal
    currency: str = "NZD"
    method: str | None = None
    note: str = ""
    refund_of_seq: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _m(x: float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def generate(start: dt.date, end: dt.date, seed: int = SEED) -> list[DemoEntry]:
    """按日期生成演示账目（纯函数，便于离线测试）。"""
    rnd = random.Random(seed)  # noqa: S311 - 演示数据，需要可复现而非密码学随机
    out: list[DemoEntry] = []

    def add(
        day: dt.date, etype: str, parent: str, leaf: str, amount: float, **kw: Any
    ) -> DemoEntry:
        e = DemoEntry(len(out) + 1, day, etype, parent, leaf, _m(amount), **kw)
        out.append(e)
        return e

    def pick(*opts: str) -> str:
        return rnd.choice(opts)

    card_spend: dict[str, Decimal] = defaultdict(Decimal)
    day = start
    while day <= end:
        wd = day.weekday()  # 0=周一
        month = day.strftime("%Y-%m")

        def spend(
            parent: str, leaf: str, amount: float, method: str = "credit_card", **kw: Any
        ) -> DemoEntry:
            e = add(day, "expense", parent, leaf, amount, method=method, **kw)  # noqa: B023 - 当次循环内调用
            if method == "credit_card" and e.currency == "NZD":
                card_spend[month] += e.amount  # noqa: B023
            return e

        # ── 日常 ──
        if wd in (5, 1):  # 周六大采购、周二补货
            shop = pick("Countdown", "Pak'nSave", "New World")
            amt = rnd.uniform(120, 240) if wd == 5 else rnd.uniform(35, 90)
            spend(
                "食品与餐饮",
                "超市综合购物",
                amt,
                pick("credit_card", "debit_card"),
                note=f"{shop} 采购",
            )
        if wd == 3 and rnd.random() < 0.7:
            spend(
                "食品与餐饮",
                "超市食品",
                rnd.uniform(18, 65),
                "debit_card",
                note=pick("亚洲超市", "Tai Ping 超市", "菜市场"),
            )
        if wd < 5 and rnd.random() < 0.45:
            spend(
                "食品与餐饮",
                "外出用餐",
                rnd.uniform(4.8, 7.5),
                note=pick("咖啡", "Flat white", "早餐咖啡"),
            )
        if wd in (4, 6) and rnd.random() < 0.75:
            spend(
                "食品与餐饮",
                "外出用餐",
                rnd.uniform(28, 110),
                note=pick("周末家庭晚餐", "火锅", "日料", "披萨外卖", "越南粉", "早午餐"),
            )
        if (
            wd == 0
            and day.month in (7, 8, 9, 10)
            and not (day.month == 7 and day.day < 20)
            and not (dt.date(2026, 9, 26) <= day <= dt.date(2026, 10, 12))
        ):
            spend("食品与餐饮", "学校餐食", 30, "debit_card", note="学校午餐卡充值")
        if wd == 2:
            spend(
                "交通出行",
                "燃油",
                rnd.uniform(72, 118),
                note=pick("Z Energy", "BP", "Mobil", "Gull"),
            )
        if wd < 5 and rnd.random() < 0.18:
            spend("交通出行", "停车与路桥", rnd.uniform(4, 14), note="市区停车")
        if day.day in (3, 17):
            spend("交通出行", "公交", 40, note="HOP 卡充值")
        if rnd.random() < 0.05:
            spend("交通出行", "打车", rnd.uniform(16, 48), note="Uber")
        if wd == 6 and rnd.random() < 0.5:
            spend(
                "居家生活",
                "日用杂物",
                rnd.uniform(15, 75),
                pick("credit_card", "debit_card"),
                note=pick("Kmart", "The Warehouse", "Mitre 10"),
            )
        if rnd.random() < 0.06:
            spend(
                "医疗与个护",
                pick("药品", "个人护理"),
                rnd.uniform(9, 42),
                note=pick("Chemist Warehouse", "Unichem", "药房"),
            )
        if wd == 5 and rnd.random() < 0.3:
            spend(
                "娱乐休闲",
                "其他娱乐",
                rnd.uniform(18, 65),
                pick("credit_card", "cash"),
                note=pick("游泳馆", "博物馆", "动物园", "保龄球", "农夫市集"),
            )

        # ── 每月固定 ──
        if day.day == 1:
            spend("交通出行", "车辆保险", 68.50, note="车险月付")
            add(day, "internal_transfer", "内部转账", "账户间转账", 1000, note="每月转入储蓄账户")
        if day.day == 5:
            spend("通信与订阅", "手机与话费", 45, note="手机月费（One NZ）")
            spend("通信与订阅", "手机与话费", 30, note="手机月费（2degrees）")
        if day.day == 8:
            spend("通信与订阅", "软件订阅", 18.49, note="Netflix")
            spend("通信与订阅", "软件订阅", 4.99, note="iCloud 储存")
        if day.day == 12:
            spend("通信与订阅", "手机与话费", 38, currency="CNY", note="国内手机号月租")
        if day.day == 15:
            spend(
                "人情与往来",
                "给他人转账",
                1500,
                "debit_card",
                currency="CNY",
                note="给父母的生活费",
            )
        if day.day == 20 and day.month != start.month:
            prev = (day.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
            total = card_spend.get(prev, Decimal(0))
            if total > 0:
                add(
                    day,
                    "card_repayment",
                    "信用卡还款",
                    "信用卡还款",
                    float(total),
                    note=f"还清 {prev} 信用卡账单",
                )
        if day.day == 22:
            spend("通信与订阅", "软件订阅", 19.99, note="Spotify 家庭版")

        # ── 收入 ──
        if wd == 3 and (day - dt.date(2026, 7, 9)).days % 14 == 0:
            add(day, "income", "工作收入", "工资", 3920.45, note="工资（双周）")
        if wd == 3 and (day - dt.date(2026, 7, 16)).days % 14 == 0:
            add(day, "income", "工作收入", "工资", 2480.20, note="兼职工资（双周）")
        day += dt.timedelta(days=1)

    # ── 一次性事件（在区间内才生成） ──
    def once(d: dt.date, *a: Any, **kw: Any) -> DemoEntry | None:
        return add(d, *a, **kw) if start <= d <= end else None

    once(
        dt.date(2026, 7, 11),
        "expense",
        "交通出行",
        "道路救援会员",
        139,
        method="credit_card",
        note="AA 年费",
    )
    once(
        dt.date(2026, 7, 14),
        "expense",
        "交通出行",
        "校巴",
        120,
        method="debit_card",
        note="第三学期校巴月票",
    )
    once(
        dt.date(2026, 7, 18),
        "expense",
        "教育与职业",
        "学习用品",
        46.80,
        method="credit_card",
        note="Warehouse Stationery 开学文具",
    )
    sofa = once(
        dt.date(2026, 7, 25),
        "expense",
        "居家生活",
        "家具",
        899,
        method="credit_card",
        note="客厅沙发",
    )
    once(
        dt.date(2026, 8, 2),
        "expense",
        "娱乐休闲",
        "电影",
        37,
        method="credit_card",
        note="Event Cinemas 两张电影票",
    )
    once(dt.date(2026, 8, 6), "receivable", "往来款", "代垫", 64, note="替朋友垫付聚餐")
    once(dt.date(2026, 8, 9), "receivable", "往来款", "收回代垫", 64, note="朋友还聚餐垫付")
    once(
        dt.date(2026, 8, 13),
        "expense",
        "居家生活",
        "维修",
        185,
        method="debit_card",
        note="水管维修",
    )
    kettle = once(
        dt.date(2026, 8, 16),
        "expense",
        "居家生活",
        "家电与耗材",
        129,
        method="credit_card",
        note="Briscoes 电水壶",
    )
    once(dt.date(2026, 8, 21), "income", "工作收入", "奖金", 600, note="季度奖金")
    once(
        dt.date(2026, 8, 28),
        "expense",
        "人情与往来",
        "礼金与礼物",
        55,
        method="credit_card",
        note="同学生日礼物",
    )
    once(dt.date(2026, 9, 1), "exchange", "换汇", "货币兑换", 2000, note="纽币换人民币（Wise）")
    once(
        dt.date(2026, 9, 4),
        "income",
        "他人赠予",
        "亲友赠予",
        800,
        currency="CNY",
        note="长辈给孩子的红包",
    )
    once(
        dt.date(2026, 9, 6), "expense", "其他支出", "邮寄", 8.50, method="cash", note="NZ Post 寄信"
    )
    once(dt.date(2026, 9, 12), "income", "其他收入", "其他收入", 45, note="Trade Me 卖二手童车")
    once(dt.date(2026, 9, 19), "expense", "人情与往来", "捐赠", 20, method="cash", note="学校募捐")
    once(
        dt.date(2026, 9, 27),
        "expense",
        "娱乐休闲",
        "其他娱乐",
        168,
        method="credit_card",
        note="学校假期活动营",
    )
    once(
        dt.date(2026, 10, 3),
        "expense",
        "医疗与个护",
        "药妆综合购物",
        76.40,
        method="credit_card",
        note="药妆店补货",
    )
    if sofa:
        once(
            dt.date(2026, 8, 3),
            "refund",
            "",
            "",
            120,
            refund_of_seq=sofa.seq,
            note="沙发价保差价退款",
        )
    if kettle:
        once(
            dt.date(2026, 8, 24), "refund", "", "", 129, refund_of_seq=kettle.seq, note="电水壶退货"
        )
    out.sort(key=lambda e: (e.day, e.seq))
    return out


def summary(entries: list[DemoEntry]) -> dict[str, Any]:
    c: Counter[str] = Counter()
    nzd: dict[str, Decimal] = defaultdict(Decimal)
    for e in entries:
        c[f"{e.day:%Y-%m} {e.type}"] += 1
        if e.currency == "NZD" and e.type in ("expense", "refund"):
            nzd[f"{e.day:%Y-%m}"] += e.amount if e.type == "expense" else -e.amount
    return {
        "笔数": dict(sorted(c.items())),
        "NZD 净支出（不含人民币）": {k: str(v) for k, v in sorted(nzd.items())},
    }


def apply(ctx: Any, actor: Any, fid: str, entries: list[DemoEntry]) -> dict[str, int]:
    from ledger.application import entries as entry_app
    from ledger.application import repo
    from ledger.application.actions import Scope, get_receipt
    from ledger.domain.categories import KIND_FOR_ENTRY_TYPE
    from ledger.domain.entries import EntryInput

    cat = repo.catalog(ctx, fid)
    names = {c.category_id: c.name for c in cat.by_id.values()}
    index = {
        (c.kind, names.get(c.parent_id or "", ""), c.name): c.category_id
        for c in cat.by_id.values()
        if c.is_leaf and c.status == "active" and c.redirect_to is None
    }
    enabled = set(repo.family_currencies(ctx, fid))
    tag = hashlib.sha256(fid.encode()).hexdigest()[:8]
    ids: dict[int, str] = {}
    stats: Counter[str] = Counter()
    for e in entries:
        key = f"demo-{tag}-{e.seq:04d}"
        if e.currency not in enabled:
            stats["跳过（币种未启用）"] += 1
            continue
        if e.type == "refund":
            if e.refund_of_seq not in ids:
                stats["跳过（缺原支出）"] += 1
                continue
            data = EntryInput(
                e.day.isoformat(),
                "refund",
                str(e.amount),
                note=e.note,
                refund_of=ids[e.refund_of_seq],
            )
        else:
            leaf = index.get((KIND_FOR_ENTRY_TYPE[e.type], e.parent, e.leaf))
            if leaf is None:
                raise SystemExit(f"家庭目录中找不到分类：{e.parent}／{e.leaf}")
            data = EntryInput(
                e.day.isoformat(), e.type, str(e.amount), leaf, e.currency, e.note, e.method
            )
        existed = get_receipt(ctx, Scope.family(fid, actor, key)) is not None
        view = entry_app.create_entry(ctx, actor, fid, key, data)
        ids[e.seq] = view["entry_id"]
        stats["重复执行命中" if existed else "新建"] += 1
    return dict(stats)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="生成演示账目")
    ap.add_argument("--login", required=True)
    ap.add_argument("--family", required=True)
    ap.add_argument("--start", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--end", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--account", help="生产：凭证账户必须等于此 ID")
    ap.add_argument("--lang", choices=("zh", "en"), default="zh", help="en：分类与备注用英文")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    entries = generate(a.start, a.end)
    if a.lang == "en":
        from ledger.ops.demo_en import localize

        entries = localize(entries)
    print(json.dumps(summary(entries), ensure_ascii=False, indent=1))
    if a.dry_run:
        print(f"共 {len(entries)} 笔；演算模式未写入。")
        return
    from ledger.ops.history_import import _actor_and_family, _store_ctx

    local = os.environ.get("LEDGER_ENV") == "local"
    if not local:
        import boto3

        if not a.account or boto3.client("sts").get_caller_identity()["Account"] != a.account:
            raise SystemExit("生产环境必须提供与当前凭证一致的 --account")
    ctx = _store_ctx(local)
    actor, fid = _actor_and_family(ctx, a.login, a.family)
    if a.lang == "en":
        from ledger.ops.demo_en import rename_categories

        print(f"分类改为英文：{rename_categories(ctx, actor, fid)} 个")
    print(apply(ctx, actor, fid, entries))


if __name__ == "__main__":
    main()

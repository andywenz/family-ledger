"""月度汇总（需求 §5、ADR-0010）。只累加各笔已保存的分值，不重新折算。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .categories import Catalog
from .entries import PAYMENT_METHODS, Entry
from .money import format_minor


@dataclass(frozen=True)
class Filters:
    created_by: str | None = None
    type: str | None = None
    category_id: str | None = None  # 一级或叶子，按当前目录解析
    payment_method: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def matches(entry: Entry, f: Filters, catalog: Catalog) -> bool:
    if f.created_by is not None and entry.created_by != f.created_by:
        return False
    if f.type is not None and entry.type != f.type:
        return False
    if f.payment_method is not None and entry.payment_method != f.payment_method:
        return False
    if f.category_id is not None:
        r = catalog.resolve(entry.leaf_category_id)
        if f.category_id not in (r.leaf_id, r.parent_id, r.stored_leaf_id):
            return False
    return True


class _Pair:
    __slots__ = ("nzd", "cny")

    def __init__(self) -> None:
        self.nzd = 0
        self.cny = 0

    def add(self, e: Entry, sign: int = 1) -> None:
        self.nzd += sign * e.nzd_minor
        self.cny += sign * e.cny_minor

    def to_dict(self) -> dict[str, Any]:
        return {
            "nzd": format_minor(self.nzd, 2),
            "cny": format_minor(self.cny, 2),
            "nzd_minor": self.nzd,
            "cny_minor": self.cny,
        }


class _Method:
    __slots__ = ("expense", "refund", "expense_count", "refund_count")

    def __init__(self) -> None:
        self.expense, self.refund = _Pair(), _Pair()
        self.expense_count = self.refund_count = 0


def compute(entries: Iterable[Entry], catalog: Catalog, f: Filters | None = None) -> dict[str, Any]:
    f = f or Filters()
    income, gross, refund = _Pair(), _Pair(), _Pair()
    methods = {m: _Method() for m in PAYMENT_METHODS}
    pie: dict[str, int] = {}
    pie_names: dict[str, str] = {}
    count = 0
    for e in entries:
        if e.state != "active" or not matches(e, f, catalog):
            continue
        count += 1
        if e.type == "income":
            income.add(e)
        elif e.type in ("expense", "refund"):
            sign = 1 if e.type == "expense" else -1
            (gross if sign > 0 else refund).add(e)
            if e.payment_method is not None:
                bucket = methods[e.payment_method]
                if sign > 0:
                    bucket.expense.add(e)
                    bucket.expense_count += 1
                else:
                    bucket.refund.add(e)
                    bucket.refund_count += 1
            r = catalog.resolve(e.leaf_category_id)
            pie[r.parent_id] = pie.get(r.parent_id, 0) + sign * e.nzd_minor
            pie_names[r.parent_id] = r.parent_name
        # 内部转账、换汇、信用卡还款、往来款：只计条数，不进入任何金额汇总

    net = _Pair()
    net.nzd, net.cny = gross.nzd - refund.nzd, gross.cny - refund.cny
    balance = _Pair()
    balance.nzd, balance.cny = income.nzd - net.nzd, income.cny - net.cny

    positives = sorted(((cid, v) for cid, v in pie.items() if v > 0), key=lambda x: (-x[1], x[0]))
    negatives = sorted(((cid, v) for cid, v in pie.items() if v < 0), key=lambda x: (x[1], x[0]))
    denominator = sum(v for _, v in positives)

    def share(v: int) -> str:
        pct = (Decimal(v) * 100 / Decimal(denominator)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        return f"{pct:.2f}"

    by_method = []
    for m in PAYMENT_METHODS:
        b = methods[m]
        n = _Pair()
        n.nzd, n.cny = b.expense.nzd - b.refund.nzd, b.expense.cny - b.refund.cny
        by_method.append(
            {
                "payment_method": m,
                "expense": b.expense.to_dict(),
                "refund": b.refund.to_dict(),
                "net": n.to_dict(),
                "expense_count": b.expense_count,
                "refund_count": b.refund_count,
            }
        )

    return {
        "entry_count": count,
        "totals": {
            "income": income.to_dict(),
            "expense_gross": gross.to_dict(),
            "refund": refund.to_dict(),
            "net_expense": net.to_dict(),
            "balance": balance.to_dict(),
        },
        "by_payment_method": by_method,
        "category_pie": {
            "currency": "NZD",
            "denominator_minor": denominator,
            "denominator": format_minor(denominator, 2),
            "slices": [
                {
                    "category_id": cid,
                    "name": pie_names[cid],
                    "net_minor": v,
                    "net": format_minor(v, 2),
                    "share_percent": share(v),
                }
                for cid, v in positives
            ],
            "negatives": [
                {
                    "category_id": cid,
                    "name": pie_names[cid],
                    "net_minor": v,
                    "net": format_minor(v, 2),
                }
                for cid, v in negatives
            ],
            "empty": not positives,
        },
    }

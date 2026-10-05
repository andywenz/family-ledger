"""月度总览的显示币种（2026-10-05 用户要求）。

- 主币种 ＝ 家庭默认币种：本月净支出、钱花在了哪里、消费方式、明细列表第一个“合 xxx”。
- 辅助币种（可选）：本月净支出与消费方式的小字、明细列表第二个“合 xxx”；不设置则不显示。
- 换算：NZD／CNY 直接使用入账快照里保存的“合 NZD／合 CNY”；其他币种按每笔快照的生效日汇率
  即时折算（该币种当日无汇率时向前最多回看 7 天），逐笔四舍五入后汇总。
  缺汇率的账目不计入合计并计数返回，绝不按 0 处理。
- 只影响显示：不改写账目，不影响导出（导出仍为合 NZD／合 CNY）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ledger.domain.categories import Catalog
from ledger.domain.entries import PAYMENT_METHODS, Entry
from ledger.domain.fx import DEFAULT_LOOKBACK_DAYS, RatePendingResult, resolve
from ledger.domain.money import convert_to_target, format_minor

from . import repo
from .context import AppContext

DEFAULT_SECONDARY = "CNY"  # 旧家庭未设置时保持原来的人民币显示
STORED: dict[str, Callable[[Entry], int]] = {
    "NZD": lambda e: e.nzd_minor,
    "CNY": lambda e: e.cny_minor,
}


def primary(cfg: dict[str, Any]) -> str:
    """主显示币种：家庭默认币种。"""
    return str(cfg["default_currency"])


def configured(cfg: dict[str, Any]) -> str | None:
    """辅助币种；空字符串表示“不显示”；与主币种相同时也不显示。"""
    value = cfg.get("secondary_currency", DEFAULT_SECONDARY)
    if not value or value == primary(cfg):
        return None
    return str(value)


class CurrencyConverter:
    def __init__(self, ctx: AppContext, fid: str, target: str, entries: Iterable[Entry]) -> None:
        self.target = target
        self.currencies = {**repo.global_currencies(ctx), **repo.family_currencies(ctx, fid)}
        self._target_usd: dict[Any, Decimal | None] = {}
        self._provider: dict[Any, Any] = {}
        self._manual: dict[Any, Any] = {}
        if target not in STORED:
            days = [e.fx_snapshot.effective_date for e in entries]
            if days:
                hi, lo = max(days), min(days)
                # 一次读取整个区间（含回看）的汇率，而不是逐笔查询
                self._provider, self._manual = repo.rate_inputs(
                    ctx, fid, hi, lookback=(hi - lo).days + DEFAULT_LOOKBACK_DAYS
                )

    def _usd_on(self, day: Any) -> Decimal | None:
        if day not in self._target_usd:
            out = resolve(day, (self.target,), self._provider, self._manual)
            usd = None if isinstance(out, RatePendingResult) else out.usd()[self.target]
            self._target_usd[day] = usd
        return self._target_usd[day]

    def minor(self, e: Entry) -> int | None:
        """账目折算到目标币种的分值（两位小数）；缺汇率返回 None。"""
        if self.target in STORED:
            return STORED[self.target](e)
        meta = self.currencies.get(e.currency)
        if meta is None:
            return None
        if e.currency == self.target:
            return convert_to_target(e.amount_minor, meta, self.target, {})
        snap = e.fx_snapshot.usd()
        src = snap.get(e.currency)
        tgt = snap.get(self.target) or self._usd_on(e.fx_snapshot.effective_date)
        if src is None or tgt is None:
            return None
        rates = {e.currency: src, self.target: tgt}
        return convert_to_target(e.amount_minor, meta, self.target, rates)

    def amount(self, e: Entry) -> dict[str, str] | None:
        v = self.minor(e)
        return None if v is None else {"currency": self.target, "amount": format_minor(v, 2)}


# 旧名称保留，便于阅读既有调用
SecondaryConverter = CurrencyConverter


def summary_block(
    conv: CurrencyConverter, entries: Iterable[Entry], catalog: Catalog | None = None
) -> dict[str, Any]:
    """净支出（消费－退款）、各消费方式净额；传入 catalog 时另算一级分类饼图。
    entries 须已按筛选条件过滤；只统计支出与退款（与原月度汇总口径一致）。"""
    gross = refund = 0
    methods = {m: [0, 0, 0] for m in PAYMENT_METHODS}  # 净额、消费笔数、退款笔数
    pie: dict[str, int] = {}
    names: dict[str, str] = {}
    missing = 0
    for e in entries:
        if e.state != "active" or e.type not in ("expense", "refund"):
            continue
        v = conv.minor(e)
        if v is None:
            missing += 1
            continue
        sign = 1 if e.type == "expense" else -1
        if sign > 0:
            gross += v
        else:
            refund += v
        if e.payment_method in methods:
            b = methods[e.payment_method]
            b[0] += sign * v
            b[1 if sign > 0 else 2] += 1
        if catalog is not None:
            r = catalog.resolve(e.leaf_category_id)
            pie[r.parent_id] = pie.get(r.parent_id, 0) + sign * v
            names[r.parent_id] = r.parent_name
    net = gross - refund
    out: dict[str, Any] = {
        "currency": conv.target,
        "net_expense": format_minor(net, 2),
        "net_expense_minor": net,
        "expense_gross": format_minor(gross, 2),
        "refund": format_minor(refund, 2),
        "by_payment_method": [
            {
                "payment_method": m,
                "net": format_minor(b[0], 2),
                "net_minor": b[0],
                "expense_count": b[1],
                "refund_count": b[2],
            }
            for m, b in methods.items()
        ],
        "missing_count": missing,
    }
    if catalog is not None:
        out["category_pie"] = _pie(conv.target, pie, names)
    return out


def _pie(currency: str, pie: dict[str, int], names: dict[str, str]) -> dict[str, Any]:
    positives = sorted(((c, v) for c, v in pie.items() if v > 0), key=lambda x: (-x[1], x[0]))
    negatives = sorted(((c, v) for c, v in pie.items() if v < 0), key=lambda x: (x[1], x[0]))
    denominator = sum(v for _, v in positives)

    def share(v: int) -> str:
        pct = (Decimal(v) * 100 / Decimal(denominator)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        return f"{pct:.2f}"

    return {
        "currency": currency,
        "denominator_minor": denominator,
        "denominator": format_minor(denominator, 2),
        "slices": [
            {
                "category_id": c,
                "name": names[c],
                "net_minor": v,
                "net": format_minor(v, 2),
                "share_percent": share(v),
            }
            for c, v in positives
        ],
        "negatives": [
            {"category_id": c, "name": names[c], "net_minor": v, "net": format_minor(v, 2)}
            for c, v in negatives
        ],
        "empty": not positives,
    }


def dashboard_block(conv: CurrencyConverter, entries: Iterable[Entry]) -> dict[str, Any]:
    """辅助币种块（不含饼图）。"""
    return summary_block(conv, entries)

"""辅助币种显示（家庭设置，可选；2026-10-05 用户要求）。

- 只影响月度总览的显示：本月净支出的“合 xxx”、消费方式小字金额、明细列表的第二个“合 xxx”。
- CNY：直接使用入账快照里的“合 CNY”，与原显示完全一致。
- 其他币种：按每笔账目快照的生效日汇率即时折算（目标币种当日无汇率时向前最多回看 7 天），
  逐笔四舍五入后汇总。缺汇率的账目不计入合计并计数返回，绝不按 0 处理。
- 不改写账目、不影响统计口径（NZD 仍是主币种），也不影响导出。
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from ledger.domain.entries import Entry
from ledger.domain.fx import DEFAULT_LOOKBACK_DAYS, RatePendingResult, resolve
from ledger.domain.money import convert_to_target, format_minor

from . import repo
from .context import AppContext

DEFAULT_SECONDARY = "CNY"  # 旧家庭未设置时保持原来的人民币显示


def configured(cfg: dict[str, Any]) -> str | None:
    """家庭设置中的辅助币种；空字符串表示用户选择了“不显示”。"""
    value = cfg.get("secondary_currency", DEFAULT_SECONDARY)
    return str(value) if value else None


class SecondaryConverter:
    def __init__(self, ctx: AppContext, fid: str, target: str, entries: Iterable[Entry]) -> None:
        self.target = target
        self.currencies = {**repo.global_currencies(ctx), **repo.family_currencies(ctx, fid)}
        self._target_usd: dict[Any, Decimal | None] = {}
        self._provider: dict[Any, Any] = {}
        self._manual: dict[Any, Any] = {}
        if target != "CNY":
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
            self._target_usd[day] = (
                None if isinstance(out, RatePendingResult) else out.usd()[self.target]
            )
        return self._target_usd[day]

    def minor(self, e: Entry) -> int | None:
        """账目折算到辅助币种的分值（两位小数）；缺汇率返回 None。"""
        if self.target == "CNY":
            return e.cny_minor
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
        return convert_to_target(
            e.amount_minor, meta, self.target, {e.currency: src, self.target: tgt}
        )

    def amount(self, e: Entry) -> dict[str, str] | None:
        v = self.minor(e)
        return None if v is None else {"currency": self.target, "amount": format_minor(v, 2)}


def dashboard_block(conv: SecondaryConverter, entries: Iterable[Entry]) -> dict[str, Any]:
    """净支出与各消费方式净额（支出－退款）的辅助币种合计；entries 为已按筛选条件过滤的账目。"""
    from ledger.domain.entries import PAYMENT_METHODS

    net = 0
    methods = dict.fromkeys(PAYMENT_METHODS, 0)
    missing = 0
    for e in entries:
        if e.type not in ("expense", "refund"):
            continue
        v = conv.minor(e)
        if v is None:
            missing += 1
            continue
        sign = 1 if e.type == "expense" else -1
        net += sign * v
        if e.payment_method in methods:
            methods[e.payment_method] += sign * v
    return {
        "currency": conv.target,
        "net_expense": format_minor(net, 2),
        "net_expense_minor": net,
        "by_payment_method": [
            {"payment_method": m, "net": format_minor(v, 2), "net_minor": v}
            for m, v in methods.items()
        ],
        "missing_count": missing,
    }

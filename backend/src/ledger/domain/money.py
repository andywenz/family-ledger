"""金额与折算：全部使用 Decimal／整数分值，禁止浮点（需求 §7，ACC-02／03）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext

from .errors import ValidationFailed

# 足够容纳 12 位整数金额 × 20 位小数汇率的中间结果，汇率不提前截断。
_CTX = Context(prec=60, rounding=ROUND_HALF_UP)
_AMOUNT_RE = re.compile(r"^(0|[1-9][0-9]{0,11})(?:\.([0-9]{1,3}))?$")
_RATE_RE = re.compile(r"^(0|[1-9][0-9]{0,9})(?:\.([0-9]{1,20}))?$")

TARGET_DIGITS = 2  # 合 NZD／合 CNY 固定两位


@dataclass(frozen=True)
class CurrencyMeta:
    code: str
    minor_digits: int
    name_zh: str = ""
    provider_supported: bool = True


def parse_amount(text: str, currency: CurrencyMeta, *, max_major: Decimal) -> int:
    """把正十进制字符串解析为原币最小单位整数。

    拒绝零、负数、指数、NaN／Infinity、超精度与超过配置上限的金额。
    """
    if not isinstance(text, str):
        raise ValidationFailed("金额必须是十进制字符串", code="validation_failed")
    m = _AMOUNT_RE.match(text)
    if not m:
        raise ValidationFailed(f"金额格式无效：{text!r}")
    frac = m.group(2) or ""
    if len(frac) > currency.minor_digits:
        raise ValidationFailed(f"{currency.code} 最多 {currency.minor_digits} 位小数")
    value = Decimal(text)
    if value <= 0:
        raise ValidationFailed("金额必须大于 0")
    if value > max_major:
        raise ValidationFailed("金额超过上限")
    return int(value.scaleb(currency.minor_digits))


def parse_rate(text: str) -> Decimal:
    """解析 USD 基准汇率（一单位币种可兑换的 USD），必须为正。"""
    if not isinstance(text, str) or not _RATE_RE.match(text):
        raise ValidationFailed(f"汇率格式无效：{text!r}")
    value = Decimal(text)
    if value <= 0:
        raise ValidationFailed("汇率必须大于 0")
    return value


def format_minor(minor: int, digits: int) -> str:
    """整数分值 → 十进制字符串（保留币种位数，负数带符号）。"""
    q = Decimal(minor).scaleb(-digits)
    return f"{q:.{digits}f}"


def convert_minor(
    amount_minor: int,
    source_digits: int,
    source_usd: Decimal,
    target_usd: Decimal,
) -> int:
    """原币分值折算为目标币分值（两位），ROUND_HALF_UP。

    目标 = 原币金额 × r(原币) ÷ r(目标)；中间不截断。
    """
    with localcontext(_CTX):
        amount = Decimal(amount_minor).scaleb(-source_digits)
        target = amount * source_usd / target_usd
        rounded = target.quantize(Decimal(1).scaleb(-TARGET_DIGITS), rounding=ROUND_HALF_UP)
        return int(rounded.scaleb(TARGET_DIGITS))


def convert_to_target(
    amount_minor: int,
    source: CurrencyMeta,
    target_code: str,
    rates: dict[str, Decimal],
) -> int:
    """折算到 NZD 或 CNY。原币即目标币时数值保持原金额（需求 §7）。"""
    if source.code == target_code:
        if source.minor_digits == TARGET_DIGITS:
            return amount_minor
        with localcontext(_CTX):
            v = Decimal(amount_minor).scaleb(-source.minor_digits)
            q = v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return int(q.scaleb(TARGET_DIGITS))
    return convert_minor(amount_minor, source.minor_digits, rates[source.code], rates[target_code])

"""严格解析模型输出（ADR-0011 步骤 2–4）。"""

from __future__ import annotations

import json
import re
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator

from ledger import resources

from .ports import ModelError, ModelResponse

SCHEMA_PATH = resources.path("contracts/model.schema.json")
_FENCE = re.compile(r"\A```(?:json)?[ \t]*\n(.*)\n```\s*\Z", re.S)
MAX_RAW = 64 * 1024


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"重复键 {k}")
        out[k] = v
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError(f"非法数值 {name}")


def parse(resp: ModelResponse) -> dict[str, Any]:
    """返回通过 JSON Schema 的输出；协议或结构问题抛 ModelError。"""
    # end_turn：文本模式正常结束；tool_use：结构化模式下唯一指定工具调用完成
    if resp.stop_reason not in ("end_turn", "tool_use"):
        raise ModelError("protocol_error", f"非正常结束：{resp.stop_reason}", usage_known=True)
    if resp.content_blocks != 1:
        raise ModelError(
            "protocol_error", "响应必须只有一个文本块或一个指定工具调用", usage_known=True
        )
    text = resp.raw_text.strip()
    if len(text) > MAX_RAW:
        raise ModelError("protocol_error", "响应过长", usage_known=True)
    m = _FENCE.match(text)
    if m:
        text = m.group(1).strip()  # 只接受整段被单个代码围栏包裹
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as e:
        raise ModelError("protocol_error", f"不是完整合法的 JSON：{e}", usage_known=True) from None
    errors = list(_validator().iter_errors(data))
    if errors:
        first = errors[0]
        raise ModelError(
            "schema_invalid", f"{list(first.absolute_path)}: {first.message}", usage_known=True
        )
    assert isinstance(data, dict)
    return data

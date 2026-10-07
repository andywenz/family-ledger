"""英文界面的错误信息：后端所有返回给用户的固定中文信息与带参数模板都必须有英文版。

新增 ValidationFailed("……") 等中文信息时，须同时在 ledger/http/messages_en.py 补充英文。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from ledger.http.messages_en import EN, PATTERNS, to_english

SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "ledger"
CJK = re.compile(r"[一-鿿]")
EXCEPTIONS = {
    "DomainError",
    "ValidationFailed",
    "Forbidden",
    "NotFound",
    "VersionConflict",
    "RatePending",
    "UpstreamUnknown",
    "StaleRead",
    "Unauthenticated",
}


def _messages() -> tuple[set[str], set[str]]:
    """异常构造的第一个参数（或 message=）与 error_body 的 message：固定文案与 f-string 模板。"""
    static: set[str] = set()
    templates: set[str] = set()
    for f in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            )
            args: list[ast.expr] = []
            if name in EXCEPTIONS and node.args:
                args.append(node.args[0])
            if name == "error_body" and len(node.args) >= 2:
                args.append(node.args[1])
            args += [k.value for k in node.keywords if name in EXCEPTIONS and k.arg == "message"]
            for a in args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) and CJK.search(a.value):
                    static.add(a.value)
                elif isinstance(a, ast.JoinedStr):
                    t = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in a.values)
                    if CJK.search(t):
                        templates.add(t)
    return static, templates


def test_every_backend_message_has_english() -> None:
    static, templates = _messages()
    assert len(static) > 150  # 防止提取逻辑失效而“空跑通过”
    assert sorted(static - EN.keys()) == []
    assert sorted(templates - {zh for zh, _ in PATTERNS}) == []


def test_english_text_has_no_chinese() -> None:
    assert [en for en in EN.values() if CJK.search(en)] == []
    assert [en for _, en in PATTERNS if CJK.search(en)] == []


def test_templates_fill_arguments_and_unknown_passes_through() -> None:
    assert (
        to_english("拆分合计 30.00 不等于原金额 40.00")
        == "The parts add up to 30.00, not the original 40.00"
    )
    assert to_english("缺少查询参数 month") == "Missing query parameter month"
    assert to_english("未收录的信息") == "未收录的信息"

"""离线替身模型（仅测试与 LEDGER_ENV=local）。不是真实识别能力，结果不得作为质量证据。"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .ports import ModelError, ModelRequest, ModelResponse, Usage

_CUR = [
    ("纽币", "NZD"),
    ("新西兰元", "NZD"),
    ("NZD", "NZD"),
    ("人民币", "CNY"),
    ("CNY", "CNY"),
    ("美元", "USD"),
    ("USD", "USD"),
    ("澳元", "AUD"),
    ("AUD", "AUD"),
    ("欧元", "EUR"),
    ("EUR", "EUR"),
]
_CAT = [
    ("超市", "expense", "expense-01-01"),
    ("停车", "expense", "expense-03-05"),
    ("加油", "expense", "expense-03-04"),
    ("打车", "expense", "expense-03-03"),
    ("午餐", "expense", "expense-01-03"),
    ("晚餐", "expense", "expense-01-03"),
    ("咖啡", "expense", "expense-01-03"),
    ("公交", "expense", "expense-03-01"),
    ("工资", "income", "income-01-01"),
    ("红包", "income", "income-02-01"),
    ("还信用卡", "card_repayment", "repayment-01-01"),
]
_PAY = [("现金", "cash"), ("信用卡", "credit_card"), ("借记卡", "debit_card")]
_AMT = re.compile(r"(\d+(?:\.\d{1,2})?)")


class FakeModel:
    """按关键词切分与匹配的确定性替身。照片：读取 PNG tEXt 块 total／currency（合成小票）。"""

    model_id = "fake.rules-v1"

    def invoke(self, request: ModelRequest, *, timeout_s: float) -> ModelResponse:
        cands = []
        issues: list[str] = []
        text = request.text or ""
        if re.search(r"忽略|ignore|system|确认入账|其他家庭", text, re.I):
            issues.append("embedded_instructions")
        for seg in [s for s in re.split(r"[，,；;。\n]|和|还有", text) if s.strip()]:
            if "忽略" in seg or "ignore" in seg.lower():
                continue
            m = _AMT.search(seg)
            cur = next((c for k, c in _CUR if k in seg), None)
            cat = next(((t, leaf) for k, t, leaf in _CAT if k in seg), None)
            if not m and not cat:
                continue
            t, leaf = cat if cat else ("expense", None)
            if "退" in seg:
                t = None  # type: ignore[assignment]
            pay = next((p for k, p in _PAY if k in seg), None)
            fs = {}
            if m:
                fs["amount"] = "evidence"
            if cur:
                fs["currency"] = "evidence"
            if leaf:
                fs["category_id"] = "inferred"
            if t:
                fs["type"] = "evidence" if cat else "inferred"
            if pay:
                fs["payment_method"] = "evidence"
            cands.append(
                {
                    "type": t,
                    "business_date": None,
                    "currency": cur,
                    "amount": m.group(1) if m else None,
                    "category_id": leaf,
                    "payment_method": pay,
                    "note": seg.strip()[:40],
                    "field_sources": fs,
                    "missing_fields": [
                        k
                        for k, v in (
                            ("amount", m),
                            ("currency", cur),
                            ("payment_method", pay),
                            ("business_date", None),
                        )
                        if not v
                    ],
                    "needs_review": (["refund_intent"] if "退" in seg else [])
                    + (["category_id"] if leaf else []),
                }
            )
        if request.image is not None:
            meta = _png_text(request.image)
            if "total" in meta:
                cands.append(
                    {
                        "type": "expense",
                        "business_date": meta.get("date"),
                        "currency": meta.get("currency"),
                        "amount": meta["total"],
                        "category_id": meta.get("category", "expense-01-01"),
                        "payment_method": None,
                        "note": meta.get("merchant", "小票"),
                        "field_sources": {"amount": "evidence", "type": "inferred"},
                        "missing_fields": ["payment_method"],
                        "needs_review": ["type"],
                    }
                )
            else:
                issues.append("total_ambiguous")
        out = {"schema_version": 1, "candidates": cands[:10]}
        if issues:
            out["input_issues"] = sorted(set(issues))
        raw = json.dumps(out, ensure_ascii=False)
        return ModelResponse(raw, "end_turn", Usage(len(text) + 200, len(raw) // 2), 5)


def _png_text(data: bytes) -> dict[str, str]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            return {k: str(v) for k, v in (getattr(img, "text", {}) or {}).items()}
    except Exception:
        return {}


@dataclass
class ScriptedModel:
    """按顺序返回预设响应或抛出预设错误（协议／故障测试）。"""

    script: list[ModelResponse | ModelError | Callable[[], ModelResponse]]
    model_id: str = "fake.scripted"
    calls: list[ModelRequest] = field(default_factory=list)

    def invoke(self, request: ModelRequest, *, timeout_s: float) -> ModelResponse:
        self.calls.append(request)
        step = self.script.pop(0)
        if isinstance(step, ModelError):
            raise step
        if callable(step):
            return step()
        return step

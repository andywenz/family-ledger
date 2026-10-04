"""AI-02／03／05／07（规则部分）：严格解析与语义校验（离线；不代表真实模型质量）。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from ledger.ai.normalize import normalize
from ledger.ai.parser import parse
from ledger.ai.ports import ModelError, ModelResponse, Usage

from .factories import CURRENCIES, catalog

GOOD = {
    "schema_version": 1,
    "candidates": [
        {
            "type": "expense",
            "business_date": None,
            "currency": "NZD",
            "amount": "45",
            "category_id": "expense-01-01",
            "payment_method": None,
            "note": "超市",
            "field_sources": {"amount": "evidence", "currency": "evidence"},
            "missing_fields": ["business_date", "payment_method"],
            "needs_review": [],
        }
    ],
}


def resp(text: str, stop: str = "end_turn", blocks: int = 1) -> ModelResponse:
    return ModelResponse(text, stop, Usage(10, 10), 1, content_blocks=blocks)


class TestParse:
    def test_valid_and_single_fence_accepted(self) -> None:
        assert parse(resp(json.dumps(GOOD)))["candidates"][0]["amount"] == "45"
        assert parse(resp("```json\n" + json.dumps(GOOD) + "\n```"))["schema_version"] == 1

    @pytest.mark.parametrize(
        "text,cls",
        [
            ('{"schema_version":1,"candidates":[],"candidates":[]}', "protocol_error"),  # 重复键
            ('{"schema_version":1,"candidates":[', "protocol_error"),  # 截断
            (
                '好的，以下是结果：{"schema_version":1,"candidates":[]}',
                "protocol_error",
            ),  # 混合文本
            ('{"schema_version":1,"candidates":[]} 额外', "protocol_error"),
            ('```\n{"a":1}\n```\n```\n{"b":2}\n```', "protocol_error"),  # 多个围栏
            ('{"schema_version":1,"candidates":[],"user_id":"u2"}', "schema_invalid"),  # 未知字段
            ('{"schema_version":1,"candidates":[{"amount":NaN}]}', "protocol_error"),
            ("[]", "schema_invalid"),
        ],
    )
    def test_ai05_rejections(self, text: str, cls: str) -> None:
        with pytest.raises(ModelError) as e:
            parse(resp(text))
        assert e.value.error_class == cls and e.value.usage_known

    def test_truncated_by_max_tokens_not_parsed(self) -> None:
        with pytest.raises(ModelError) as e:
            parse(resp(json.dumps(GOOD), stop="max_tokens"))
        assert e.value.error_class == "protocol_error"

    def test_multiple_content_blocks_rejected(self) -> None:
        with pytest.raises(ModelError):
            parse(resp(json.dumps(GOOD), blocks=2))


def run(raw: dict, **kw):  # noqa: ANN003, ANN201
    return normalize(
        raw,
        catalog=catalog(),
        currencies=CURRENCIES,
        received=date(2026, 10, 4),
        default_currency=kw.get("cur", "NZD"),
        default_method="credit_card",
        max_major=Decimal("1000000"),
    )


class TestNormalize:
    def test_ai02_defaults_are_marked(self) -> None:
        (d,), _ = run(GOOD)
        assert (
            d.business_date == "2026-10-04" and d.field_sources["business_date"] == "request_date"
        )
        assert d.payment_method == "credit_card"
        assert d.field_sources["payment_method"] == "family_default"
        assert d.status == "ready"

    def test_ai02_missing_amount_stays_empty(self) -> None:
        raw = json.loads(json.dumps(GOOD))
        raw["candidates"][0]["amount"] = None
        (d,), _ = run(raw)
        assert d.amount is None and "amount" in d.missing_fields and d.status == "incomplete"

    def test_category_must_be_current_and_match_kind(self) -> None:
        raw = json.loads(json.dumps(GOOD))
        raw["candidates"][0]["category_id"] = "income-01-01"  # 收入分类塞进支出
        (d,), _ = run(raw)
        assert d.category_id == "expense-09-02" and "category_id" in d.needs_review
        raw["candidates"][0]["category_id"] = "made-up"
        (d,), _ = run(raw)
        assert d.category_id == "expense-09-02"

    def test_invalid_values_nulled_not_trusted(self) -> None:
        raw = json.loads(json.dumps(GOOD))
        c = raw["candidates"][0]
        c.update(currency="GBP", business_date="2027-01-01", amount="1.234")
        c["field_sources"].update(business_date="evidence")
        (d,), _ = run(raw)
        assert d.currency == "NZD" and d.field_sources["currency"] == "family_default"
        assert d.business_date == "2026-10-04" and "business_date" in d.needs_review
        assert d.amount is None and "amount" in d.needs_review

    def test_inferred_defaultable_fields_use_marked_defaults(self) -> None:
        """模型推断的币种／方式不采用，改用带标注的家庭默认值；推断日期保留并待核对。"""
        raw = json.loads(json.dumps(GOOD))
        c = raw["candidates"][0]
        c.update(currency="CNY", business_date="2026-10-01", payment_method="cash")
        c["field_sources"] = {
            "amount": "evidence",
            "currency": "inferred",
            "business_date": "inferred",
            "payment_method": "inferred",
        }
        (d,), _ = run(raw)
        assert (d.currency, d.field_sources["currency"]) == ("NZD", "family_default")
        assert d.business_date == "2026-10-01" and "business_date" in d.needs_review
        assert (d.payment_method, d.field_sources["payment_method"]) == (
            "credit_card",
            "family_default",
        )
        # 有输入证据的值保留
        c["field_sources"].update(currency="evidence", payment_method="evidence")
        (d,), _ = run(raw)
        assert (d.currency, d.payment_method) == ("CNY", "cash")

    def test_refund_intent_becomes_problem(self) -> None:
        raw = json.loads(json.dumps(GOOD))
        raw["candidates"][0].update(type=None, needs_review=["refund_intent"])
        (d,), _ = run(raw)
        assert "refund_not_supported" in d.problems and d.status == "incomplete"

    def test_non_stat_types_drop_payment_method(self) -> None:
        raw = json.loads(json.dumps(GOOD))
        raw["candidates"][0].update(
            type="card_repayment", category_id="repayment-01-01", payment_method="cash"
        )
        (d,), _ = run(raw)
        assert d.payment_method is None and d.status == "ready"

    def test_input_issues_passed_through(self) -> None:
        _, issues = run(
            {"schema_version": 1, "candidates": [], "input_issues": ["embedded_instructions"]}
        )
        assert issues == ["embedded_instructions"]

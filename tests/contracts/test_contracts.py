"""D0 契约测试：结构一致性与模型输出 Schema 边界。"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_contracts  # noqa: E402

MODEL = Draft202012Validator(
    json.loads((ROOT / "contracts" / "model.schema.json").read_text(encoding="utf-8"))
)

VALID_OUTPUT = {
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
            "field_sources": {
                "amount": "evidence",
                "currency": "evidence",
                "category_id": "inferred",
            },
            "missing_fields": ["business_date", "payment_method"],
            "needs_review": ["category_id"],
        },
        {
            "type": "expense",
            "business_date": None,
            "currency": "NZD",
            "amount": "8",
            "category_id": "expense-03-05",
            "payment_method": None,
            "note": "停车",
            "field_sources": {"amount": "evidence"},
            "missing_fields": ["business_date", "payment_method"],
            "needs_review": [],
        },
    ],
}


def test_all_contract_checks_pass() -> None:
    assert check_contracts.run_all() == []


def test_valid_model_output_accepted() -> None:
    assert list(MODEL.iter_errors(VALID_OUTPUT)) == []


def test_missing_amount_is_null_not_invented() -> None:
    out = copy.deepcopy(VALID_OUTPUT)
    out["candidates"][0]["amount"] = None
    out["candidates"][0]["missing_fields"].append("amount")
    assert list(MODEL.iter_errors(out)) == []


def _mutations():
    def unknown_top(o):
        o["user_id"] = "u1"

    def unknown_candidate_field(o):
        o["candidates"][0]["nzd"] = "45.00"

    def family_injection(o):
        o["candidates"][0]["family_id"] = "f2"

    def too_many(o):
        o["candidates"] = o["candidates"] * 6

    def refund_type(o):
        o["candidates"][0]["type"] = "refund"

    def negative_amount(o):
        o["candidates"][0]["amount"] = "-45"

    def float_amount(o):
        o["candidates"][0]["amount"] = 45.0

    def exponent_amount(o):
        o["candidates"][0]["amount"] = "4.5e1"

    def bad_currency(o):
        o["candidates"][0]["currency"] = "nzd"

    def bad_method(o):
        o["candidates"][0]["payment_method"] = "alipay"

    def long_note(o):
        o["candidates"][0]["note"] = "x" * 201

    def wrong_version(o):
        o["schema_version"] = 2

    def missing_required(o):
        del o["candidates"][0]["missing_fields"]

    def bad_source(o):
        o["candidates"][0]["field_sources"]["amount"] = "family_default"

    return [
        unknown_top,
        unknown_candidate_field,
        family_injection,
        too_many,
        refund_type,
        negative_amount,
        float_amount,
        exponent_amount,
        bad_currency,
        bad_method,
        long_note,
        wrong_version,
        missing_required,
        bad_source,
    ]


@pytest.mark.parametrize("mutate", _mutations(), ids=lambda f: f.__name__)
def test_invalid_model_output_rejected(mutate) -> None:
    out = copy.deepcopy(VALID_OUTPUT)
    mutate(out)
    assert list(MODEL.iter_errors(out)), f"{mutate.__name__} 应被拒绝"

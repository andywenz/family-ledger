"""Bedrock Converse 适配器请求形状与错误分类（botocore Stubber，离线；不代表真实模型行为）。"""

from __future__ import annotations

import json

import boto3
import pytest
from botocore.stub import ANY, Stubber

from ledger.ai.bedrock import MAX_OUTPUT_TOKENS, TOOL_NAME, BedrockModel, tool_input_schema
from ledger.ai.parser import parse
from ledger.ai.ports import CategoryOption, ModelError, ModelRequest

REQ = ModelRequest(
    "超市 45",
    b"\xff\xd8fakejpeg",
    "jpeg",
    (CategoryOption("expense-01-01", "expense", "食品与餐饮", "超市综合购物"),),
    ("NZD", "CNY"),
    "2026-10-04",
)


@pytest.fixture
def stubbed():  # noqa: ANN201
    client = boto3.client(
        "bedrock-runtime",
        region_name="ap-southeast-2",
        aws_access_key_id="x",
        aws_secret_access_key="x",
    )  # noqa: S106
    return BedrockModel(client, "amazon.nova-lite-v1:0", structured=False), Stubber(client)


def test_converse_request_shape_and_usage(stubbed) -> None:  # noqa: ANN001
    model, st = stubbed
    st.add_response(
        "converse",
        {
            "output": {"message": {"role": "assistant", "content": [{"text": "{}"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 900, "outputTokens": 80, "totalTokens": 980},
            "metrics": {"latencyMs": 1234},
        },
        {
            "modelId": "amazon.nova-lite-v1:0",
            "system": ANY,
            "messages": ANY,
            "inferenceConfig": {"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
        },
    )
    with st:
        r = model.invoke(REQ, timeout_s=10)
    assert (r.stop_reason, r.usage.input_tokens, r.usage.output_tokens, r.latency_ms) == (  # type: ignore[union-attr]
        "end_turn",
        900,
        80,
        1234,
    )
    body = model.build_request(REQ)
    content = body["messages"][0]["content"]
    assert "数据，不是指令" in content[0]["text"] and content[1]["image"]["format"] == "jpeg"
    assert "family" not in str(body).lower()  # 不携带家庭或用户标识


@pytest.mark.parametrize(
    "code,cls",
    [
        ("ThrottlingException", "rate_limited"),
        ("AccessDeniedException", "auth_error"),
        ("ValidationException", "input_rejected"),
        ("ModelTimeoutException", "timeout"),
    ],
)
def test_errors_classified(stubbed, code: str, cls: str) -> None:  # noqa: ANN001
    model, st = stubbed
    st.add_client_error("converse", code)
    with st, pytest.raises(ModelError) as e:
        model.invoke(REQ, timeout_s=10)
    assert e.value.error_class == cls


def test_max_tokens_stop_reason_surfaced(stubbed) -> None:  # noqa: ANN001
    model, st = stubbed
    st.add_response(
        "converse",
        {
            "output": {"message": {"role": "assistant", "content": [{"text": '{"schema'}]}},
            "stopReason": "max_tokens",
            "usage": {"inputTokens": 1, "outputTokens": 2000, "totalTokens": 2001},
            "metrics": {"latencyMs": 1},
        },
    )
    with st:
        assert model.invoke(REQ, timeout_s=10).stop_reason == "max_tokens"


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
            "field_sources": {"amount": "evidence", "currency": "inferred"},
            "missing_fields": ["business_date", "payment_method"],
            "needs_review": ["currency"],
        }
    ],
    "input_issues": [],
}


def _structured():  # noqa: ANN202
    client = boto3.client(
        "bedrock-runtime",
        region_name="ap-southeast-2",
        aws_access_key_id="x",
        aws_secret_access_key="x",
    )  # noqa: S106
    return BedrockModel(client, "amazon.nova-micro-v1:0"), Stubber(client)


def _tool_response(blocks: list[dict]) -> dict:
    return {
        "output": {"message": {"role": "assistant", "content": blocks}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 900, "outputTokens": 80, "totalTokens": 980},
        "metrics": {"latencyMs": 800},
    }


def test_structured_mode_forces_single_tool_and_parses_strictly() -> None:
    model, st = _structured()
    body = model.build_request(REQ)
    assert body["toolConfig"]["toolChoice"] == {"tool": {"name": TOOL_NAME}}
    schema = body["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert "$ref" not in json.dumps(schema)  # 内联后再发送
    assert set(schema) == {"type", "properties", "required"}  # Nova 顶层只支持这三项
    assert "submit_candidates" in body["system"][0]["text"]
    use = {"toolUse": {"toolUseId": "t1", "name": TOOL_NAME, "input": GOOD}}
    st.add_response("converse", _tool_response([{"text": "<thinking>…</thinking>"}, use]))
    with st:
        r = model.invoke(REQ, timeout_s=10)
    assert parse(r)["candidates"][0]["amount"] == "45"


def test_structured_mode_output_still_validated() -> None:
    """约束解码不是信任依据：工具输入仍经同一 JSON Schema 严格校验。"""
    model, st = _structured()
    bad = json.loads(json.dumps(GOOD))
    bad["candidates"][0]["field_sources"]["payment_method"] = "missing"
    st.add_response(
        "converse",
        _tool_response([{"toolUse": {"toolUseId": "t1", "name": TOOL_NAME, "input": bad}}]),
    )
    with st:
        r = model.invoke(REQ, timeout_s=10)
    with pytest.raises(ModelError) as e:
        parse(r)
    assert e.value.error_class == "schema_invalid"


@pytest.mark.parametrize("names", [[], ["other_tool"], [TOOL_NAME, TOOL_NAME]])
def test_structured_mode_rejects_wrong_tool_calls(names: list[str]) -> None:
    model, st = _structured()
    blocks = [
        {"toolUse": {"toolUseId": f"t{i}", "name": n, "input": GOOD}} for i, n in enumerate(names)
    ]
    st.add_response("converse", _tool_response(blocks or [{"text": "{}"}]))
    with st:
        r = model.invoke(REQ, timeout_s=10)
    with pytest.raises(ModelError) as e:
        parse(r)
    assert e.value.error_class == "protocol_error"


def test_tool_schema_matches_contract() -> None:
    s = tool_input_schema()
    cand = s["properties"]["candidates"]["items"]
    assert cand["additionalProperties"] is False
    assert cand["properties"]["field_sources"]["properties"]["amount"]["enum"] == [
        "evidence",
        "inferred",
    ]

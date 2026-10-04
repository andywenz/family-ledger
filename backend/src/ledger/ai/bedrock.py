"""Amazon Bedrock Converse 适配器。SDK 自动重试关闭，尝试次数由应用计数（ADR-0011）。"""

from __future__ import annotations

import json
import time
from functools import cache
from typing import Any

from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from .parser import SCHEMA_PATH
from .ports import ModelError, ModelRequest, ModelResponse, Usage
from .prompt import system_prompt, user_message

MAX_OUTPUT_TOKENS = 2000
TOOL_NAME = "submit_candidates"


@cache
def tool_input_schema() -> dict[str, Any]:
    """把输出契约内联为工具 inputSchema（展开 $ref）。

    Nova 工具 inputSchema 只支持 JsonSchema 子集，顶层对象仅允许 type／properties／required
    （Nova 用户指南“Defining a tool”，2026-10-04 核实）。完整契约仍由 parser 严格校验。
    """
    raw = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = raw.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return inline(defs[ref.removeprefix("#/$defs/")])
            out = {k: inline(v) for k, v in node.items()}
            if "enum" in out and "type" not in out:
                # 只有 enum 的节点补显式 type（Nova 子集需要），取值不变
                kinds = {"string" if isinstance(v, str) else "null" for v in out["enum"]}
                out["type"] = sorted(kinds) if len(kinds) > 1 else kinds.pop()
            if "const" in out and "type" not in out:
                out["type"] = "integer" if isinstance(out["const"], int) else "string"
            return out
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    full = inline(raw)
    return {"type": "object", "properties": full["properties"], "required": full["required"]}


_ERRORS = {
    "ThrottlingException": "rate_limited",
    "ServiceQuotaExceededException": "rate_limited",
    "ModelTimeoutException": "timeout",
    "AccessDeniedException": "auth_error",
    "UnrecognizedClientException": "auth_error",
    "ValidationException": "input_rejected",
    "ModelErrorException": "transport_error",
    "ModelNotReadyException": "transport_error",
    "ServiceUnavailableException": "transport_error",
    "InternalServerException": "transport_error",
}


def client_config(timeout_s: float) -> Config:
    return Config(
        retries={"max_attempts": 1, "mode": "standard"}, connect_timeout=5, read_timeout=timeout_s
    )


class BedrockModel:
    def __init__(
        self, client: Any, model_id: str, *, structured: bool = True, invoke_id: str | None = None
    ) -> None:
        self.client = client
        self.model_id = model_id  # 基础模型 ID：计价、用量记录
        self.invoke_id = invoke_id or model_id  # 实际调用 ID：可为带标签的应用推理配置文件 ARN
        self.structured = structured  # True：工具调用＋约束解码（默认，见 ADR-0015）

    def build_request(self, request: ModelRequest) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"text": user_message(request)}]
        if request.image is not None:
            content.append(
                {
                    "image": {
                        "format": request.image_format or "jpeg",
                        "source": {"bytes": request.image},
                    }
                }
            )
        body: dict[str, Any] = {
            "modelId": self.invoke_id,
            "system": [{"text": system_prompt(self.structured)}],
            "messages": [{"role": "user", "content": content}],
            "inferenceConfig": {"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
        }
        if self.structured:
            body["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": TOOL_NAME,
                            "description": "提交从输入中识别出的记账候选（只是候选，需用户确认）",
                            "inputSchema": {"json": tool_input_schema()},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": TOOL_NAME}},
            }
        return body

    def invoke(self, request: ModelRequest, *, timeout_s: float) -> ModelResponse:
        started = time.monotonic()
        try:
            out = self.client.converse(**self.build_request(request))
        except (ReadTimeoutError, ConnectionClosedError) as e:
            # 请求可能已在远端执行并计费：usage 未知，不补零
            raise ModelError("timeout", str(e), usage_known=False) from e
        except EndpointConnectionError as e:
            raise ModelError("transport_error", str(e), usage_known=True) from e
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            raise ModelError(_ERRORS.get(code, "internal"), code, usage_known=True) from e
        blocks = out.get("output", {}).get("message", {}).get("content", [])
        if self.structured:
            # 只认唯一一个指定工具调用；其输入重新序列化后仍走严格解析
            calls = [b["toolUse"] for b in blocks if "toolUse" in b]
            valid = len(calls) == 1 and calls[0].get("name") == TOOL_NAME
            texts = [json.dumps(calls[0].get("input"), ensure_ascii=False)] if valid else []
            n_blocks = len(calls)
        else:
            texts = [b["text"] for b in blocks if "text" in b]
            n_blocks = len(blocks)
        u = out.get("usage") or {}
        usage = (
            Usage(int(u["inputTokens"]), int(u["outputTokens"]))
            if "inputTokens" in u and "outputTokens" in u
            else None
        )
        return ModelResponse(
            raw_text=texts[0] if len(texts) == 1 else "",
            stop_reason=str(out.get("stopReason", "other")),
            usage=usage,
            latency_ms=int(
                out.get("metrics", {}).get("latencyMs", (time.monotonic() - started) * 1000)
            ),
            content_blocks=n_blocks,
        )

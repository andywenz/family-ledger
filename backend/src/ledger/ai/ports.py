"""模型适配器接口与错误分类（ADR-0011）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

RETRYABLE = frozenset({"rate_limited", "transport_error", "timeout"})


class ModelError(Exception):
    """模型调用失败。error_class 取 ADR-0011 分类；usage_known=False 表示可能已计费但未知。"""

    def __init__(self, error_class: str, message: str = "", *, usage_known: bool = False) -> None:
        super().__init__(message or error_class)
        self.error_class = error_class
        self.usage_known = usage_known

    @property
    def retryable(self) -> bool:
        return self.error_class in RETRYABLE


@dataclass(frozen=True)
class CategoryOption:
    leaf_id: str
    kind: str
    parent_name: str
    leaf_name: str


@dataclass(frozen=True)
class ModelRequest:
    text: str | None
    image: bytes | None
    image_format: str | None  # jpeg | png | webp
    categories: tuple[CategoryOption, ...]
    currencies: tuple[str, ...]
    received_date: str  # 家庭时区的请求接收日（YYYY-MM-DD）


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ModelResponse:
    raw_text: str
    stop_reason: str  # end_turn | max_tokens | stop_sequence | content_filtered | tool_use | other
    usage: Usage | None
    latency_ms: int
    content_blocks: int = 1
    extra: dict[str, str] = field(default_factory=dict)


class RecognitionModel(Protocol):
    model_id: str

    def invoke(self, request: ModelRequest, *, timeout_s: float) -> ModelResponse: ...

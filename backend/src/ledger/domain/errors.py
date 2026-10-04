"""领域错误：code 与 contracts/openapi.yaml 中 Error.code 枚举一致。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldError:
    field: str
    code: str


class DomainError(Exception):
    """业务规则拒绝。HTTP 层按 code 映射状态码。"""

    code = "validation_failed"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        fields: list[FieldError] | None = None,
        current: Any = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.message = message
        self.fields = fields or []
        self.current = current


class ValidationFailed(DomainError):
    code = "validation_failed"


class Forbidden(DomainError):
    code = "forbidden"


class NotFound(DomainError):
    code = "not_found"


class VersionConflict(DomainError):
    code = "version_conflict"


class RatePending(DomainError):
    code = "rate_pending"


@dataclass
class Violations:
    """收集多个字段错误后一次抛出。"""

    items: list[FieldError] = field(default_factory=list)

    def add(self, field_name: str, code: str) -> None:
        self.items.append(FieldError(field_name, code))

    def raise_if_any(self, message: str = "字段校验失败") -> None:
        if self.items:
            raise ValidationFailed(message, fields=list(self.items))


class UpstreamUnknown(DomainError):
    """写请求已发出但结果未知（ISE-018）。调用方必须按同一动作 ID 查询，不得换 ID 重试。"""

    code = "upstream_unknown"


class Retryable(Exception):
    """并发事务冲突等可安全重新计算后重试的情况（内部使用）。"""


class StaleRead(DomainError):
    """提交时发现读取的版本已变化，但权限仍可能有效：执行器重新读取后重算。"""

    code = "version_conflict"


class Unauthenticated(DomainError):
    code = "unauthenticated"

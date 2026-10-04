"""以 contracts/openapi.yaml 为唯一来源校验请求与响应。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from ledger import resources
from ledger.domain.errors import FieldError, ValidationFailed

SPEC_URI = "urn:family-ledger:openapi"
DEFAULT_SPEC = resources.path("contracts/openapi.yaml")


@dataclass(frozen=True)
class Operation:
    op_id: str
    method: str
    path: str  # OpenAPI 形式，如 /families/{fid}/entries
    pointer: str  # JSON Pointer 指向该操作
    spec: dict[str, Any]


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


@cache
def spec() -> dict[str, Any]:
    path = Path(os.environ.get("LEDGER_OPENAPI", DEFAULT_SPEC))
    compiled = path.with_suffix(".json")
    if compiled.exists():
        # Lambda 制品中由 build_lambda 从 YAML 预先转换；JSON 解析比 YAML 快数十倍（冷启动）
        data: dict[str, Any] = json.loads(compiled.read_text(encoding="utf-8"))
        return data
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


@cache
def registry() -> Registry[Any]:
    resource: Resource[Any] = DRAFT202012.create_resource(spec())
    reg: Registry[Any] = Registry().with_resource(SPEC_URI, resource)
    return reg


@cache
def operations() -> dict[str, Operation]:
    out = {}
    for path, item in spec()["paths"].items():
        for method, op in item.items():
            if method in ("get", "post", "patch", "delete", "put"):
                out[op["operationId"]] = Operation(
                    op["operationId"], method.upper(), path, f"/paths/{_escape(path)}/{method}", op
                )
    return out


@cache
def _validator(pointer: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"{SPEC_URI}#{pointer}"}, registry=registry())


def request_validator(op_id: str) -> Draft202012Validator | None:
    op = operations()[op_id]
    if "application/json" not in op.spec.get("requestBody", {}).get("content", {}):
        return None
    return _validator(op.pointer + "/requestBody/content/application~1json/schema")


def validate_request(op_id: str, body: Any) -> None:
    v = request_validator(op_id)
    if v is None:
        return
    errors = sorted(v.iter_errors(body), key=lambda e: list(e.absolute_path))
    if errors:
        fields = [
            FieldError(".".join(str(p) for p in e.absolute_path) or "(body)", str(e.validator))
            for e in errors[:10]
        ]
        raise ValidationFailed("请求内容不符合格式要求", fields=fields)


def response_errors(op_id: str, status: int, body: Any) -> list[str]:
    """测试用：返回响应与契约不符之处。未声明的状态码本身就是错误。"""
    op = operations()[op_id]
    responses = op.spec.get("responses", {})
    key = str(status)
    if key not in responses:
        # 成功状态必须显式声明；错误状态可落到 default（必须符合 Error 结构）
        if status < 400 or "default" not in responses:
            return [f"{op_id}: 未声明的状态码 {status}"]
        key = "default"
    resp = responses[key]
    pointer = op.pointer + f"/responses/{key}"
    if "$ref" in resp:
        pointer = resp["$ref"].lstrip("#")
        resp = spec()["components"]["responses"][resp["$ref"].rsplit("/", 1)[-1]]
    content = resp.get("content", {}).get("application/json")
    if content is None:
        return [] if body in (None, b"", "") else [f"{op_id}: {status} 不应有响应体"]
    v = _validator(pointer + "/content/application~1json/schema")
    return [f"{op_id} {status} {list(e.absolute_path)}: {e.message}" for e in v.iter_errors(body)]

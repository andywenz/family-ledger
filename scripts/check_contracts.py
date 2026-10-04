"""D0 契约一致性检查：OpenAPI、模型 Schema、种子数据与验收映射。

用法：uv run python scripts/check_contracts.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi

ROOT = Path(__file__).resolve().parent.parent
OPENAPI = ROOT / "contracts" / "openapi.yaml"
MODEL_SCHEMA = ROOT / "contracts" / "model.schema.json"
CATEGORIES = ROOT / "seed" / "categories.json"
CURRENCIES = ROOT / "seed" / "currencies.json"
MAPPING = ROOT / "verification" / "验收映射.md"
ACCEPTANCE_IDS = ROOT / "verification" / "acceptance_ids.txt"

AUTHZ_VALUES = {
    "public",
    "authenticated",
    "pre_auth",
    "self",
    "member",
    "family_admin",
    "owner_or_admin",
    "candidate_owner",
    "system_admin",
    "feishu_signature",
}
ENTRY_TYPES = {
    "expense",
    "income",
    "refund",
    "internal_transfer",
    "exchange",
    "card_repayment",
    "receivable",
}
CATEGORY_KINDS = ENTRY_TYPES - {"refund"}
WRITE_METHODS = {"post", "patch", "delete", "put"}
HTTP_METHODS = WRITE_METHODS | {"get"}


def load_openapi() -> dict[str, Any]:
    with OPENAPI.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref")
    if not ref:
        return node
    target: Any = spec
    for part in ref.removeprefix("#/").split("/"):
        target = target[part]
    return target


def iter_operations(spec: dict[str, Any]):
    for path, item in spec["paths"].items():
        shared = item.get("parameters", [])
        for method, op in item.items():
            if method in HTTP_METHODS:
                yield path, method, op, shared


def check_openapi(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    validate_openapi(spec)
    seen: set[str] = set()
    for path, method, op, shared in iter_operations(spec):
        where = f"{method.upper()} {path}"
        op_id = op.get("operationId")
        if not op_id:
            errors.append(f"{where}: 缺少 operationId")
            continue
        if op_id in seen:
            errors.append(f"{where}: operationId 重复 {op_id}")
        seen.add(op_id)
        authz = op.get("x-authz")
        if authz not in AUTHZ_VALUES:
            errors.append(f"{where}: x-authz 非法 {authz!r}")
        if "x-idempotent" not in op:
            errors.append(f"{where}: 缺少 x-idempotent")
        params = [_resolve(spec, p) for p in [*shared, *op.get("parameters", [])]]
        names = {(p["in"], p["name"]) for p in params}
        if op.get("x-idempotent") and ("header", "Idempotency-Key") not in names:
            errors.append(f"{where}: 声明幂等但未要求 Idempotency-Key")
        if (
            method in WRITE_METHODS
            and authz not in {"pre_auth", "feishu_signature"}
            and op_id != "previewEntry"
            and not op.get("x-idempotent")
        ):
            errors.append(f"{where}: 写操作未声明幂等")
        if op.get("x-versioned"):
            body_schema: dict[str, Any] = {}
            content = op.get("requestBody", {}).get("content", {}).get("application/json")
            if content:
                body_schema = _resolve(spec, content["schema"])
            in_body = "expected_version" in body_schema.get("required", [])
            item_schema = body_schema.get("properties", {}).get("items", {}).get("items", {})
            in_items = "version" in item_schema.get("required", [])
            in_query = ("query", "expected_version") in names
            if not (in_body or in_query or in_items):
                errors.append(f"{where}: 声明版本控制但缺少 expected_version")
        if authz in {"pre_auth", "feishu_signature", "public"} and op.get("security") != []:
            errors.append(f"{where}: {authz} 接口应声明 security: []")
    entry_types = set(spec["components"]["schemas"]["EntryType"]["enum"])
    if entry_types != ENTRY_TYPES:
        errors.append(f"EntryType 枚举不一致：{sorted(entry_types)}")
    kinds = set(spec["components"]["schemas"]["CategoryKind"]["enum"])
    if kinds != CATEGORY_KINDS:
        errors.append(f"CategoryKind 枚举不一致：{sorted(kinds)}")
    return errors


def check_model_schema() -> list[str]:
    schema = json.loads(MODEL_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return []


def check_categories() -> list[str]:
    errors: list[str] = []
    data = json.loads(CATEGORIES.read_text(encoding="utf-8"))
    ids: set[str] = set()
    leaf_kind: dict[str, str] = {}
    kinds_present: set[str] = set()
    for group in data["categories"]:
        kind = group["kind"]
        if kind not in CATEGORY_KINDS:
            errors.append(f"{group['id']}: kind 非法 {kind}")
        kinds_present.add(kind)
        for node in [group, *group["children"]]:
            if node["id"] in ids:
                errors.append(f"分类 ID 重复：{node['id']}")
            ids.add(node["id"])
            if not 1 <= len(node["name"]) <= 24:
                errors.append(f"{node['id']}: 名称长度不符")
            if node["status"] != "active":
                errors.append(f"{node['id']}: 初始状态应为 active")
        if not group["children"]:
            errors.append(f"{group['id']}: 一级分类没有叶子，无法被账目引用（ADR-0002）")
        for child in group["children"]:
            leaf_kind[child["id"]] = kind
    missing = CATEGORY_KINDS - kinds_present
    if missing:
        errors.append(f"缺少分类目录：{sorted(missing)}")
    mapping = data["kind_for_entry_type"]
    if set(mapping) != ENTRY_TYPES:
        errors.append("kind_for_entry_type 未覆盖全部记录类型")
    for kind, leaf in data["uncategorized_leaf"].items():
        if leaf_kind.get(leaf) != kind:
            errors.append(f"待分类叶子 {leaf} 不属于 {kind}")
    return errors


def check_currencies(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schema = dict(spec["components"]["schemas"]["Currency"])
    schema["properties"] = dict(schema["properties"])
    schema["properties"]["code"] = {"type": "string", "pattern": "^[A-Z]{3}$"}
    schema["additionalProperties"] = True
    validator = Draft202012Validator(schema)
    data = json.loads(CURRENCIES.read_text(encoding="utf-8"))
    codes = [c["code"] for c in data["currencies"]]
    for c in data["currencies"]:
        for err in validator.iter_errors(c):
            errors.append(f"币种 {c.get('code')}: {err.message}")
    initial = [c["code"] for c in data["currencies"] if c.get("initial")]
    if sorted(initial) != sorted({"NZD", "CNY", "USD", "AUD", "EUR"}):
        errors.append(f"初始币种应为五币：{initial}")
    if codes != sorted(codes):
        errors.append("币种应按代码字母顺序排列")
    if len(set(codes)) != len(codes):
        errors.append("币种代码重复")
    return errors


def check_mapping(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    text = MAPPING.read_text(encoding="utf-8")
    ids = {line.strip() for line in ACCEPTANCE_IDS.read_text().splitlines() if line.strip()}
    for acc in sorted(ids):
        if not re.search(rf"^\| {re.escape(acc)}\b", text, flags=re.MULTILINE):
            errors.append(f"验收映射缺少 {acc} 的追踪行")
    for path, method, op, _ in iter_operations(spec):
        op_id = op.get("operationId", "")
        if not re.search(rf"\b{re.escape(op_id)}\b", text):
            errors.append(f"验收映射未引用接口 {op_id}（{method.upper()} {path}）")
        for acc in op.get("x-acceptance", []):
            if acc not in ids:
                errors.append(f"{op_id}: x-acceptance 引用未知 ID {acc}")
    allowed = {"未实现", "已实现待验证", "离线通过", "真实通过", "受阻"}
    for row in re.findall(r"^\| (?:[A-Z]+-\d{2}).*$", text, flags=re.MULTILINE):
        cells = [c.strip() for c in row.strip("|").split("|")]
        if not any(c in allowed for c in cells):
            errors.append(f"追踪行缺少合法状态：{cells[0]}")
    return errors


def run_all() -> list[str]:
    spec = load_openapi()
    errors: list[str] = []
    errors += check_openapi(spec)
    errors += check_model_schema()
    errors += check_categories()
    errors += check_currencies(spec)
    errors += check_mapping(spec)
    return errors


def main() -> int:
    errors = run_all()
    spec = load_openapi()
    op_count = sum(1 for _ in iter_operations(spec))
    if errors:
        print(f"契约检查失败（{len(errors)} 项）：")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"契约检查通过：{op_count} 个接口，验收 ID 与分类／币种种子一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

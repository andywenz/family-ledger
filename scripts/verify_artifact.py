"""发布门禁：校验制品与清单一致、不含本地替身、资源齐全（ISE-027；OPS-08）。

  uv run python scripts/verify_artifact.py dist/lambda.zip dist/manifest.json [--expect-commit SHA]
任一检查失败即以非零状态退出。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

REQUIRED = [
    "ledger/handlers/api.py",
    "ledger/handlers/worker.py",
    "ledger/handlers/maintenance.py",
    "resources/contracts/openapi.yaml",
    "resources/contracts/openapi.json",
    "resources/contracts/model.schema.json",
    "resources/seed/categories.json",
    "resources/config/model-prices.json",
]
FORBIDDEN_PREFIXES = ("ledger/local/", "tests/", "frontend/")


def verify(zip_path: Path, manifest_path: Path, expect_commit: str | None) -> list[str]:
    errors = []
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if digest != m["sha256"]:
        errors.append(f"制品摘要不符：{digest} ≠ 清单 {m['sha256']}")
    if m.get("dirty_worktree"):
        errors.append("制品来自有未提交修改的工作区")
    if expect_commit and m.get("commit") != expect_commit:
        errors.append(f"制品 commit {m.get('commit')} ≠ 预期 {expect_commit}")
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
    for r in REQUIRED:
        if r not in names:
            errors.append(f"缺少 {r}")
    bad = sorted(n for n in names if n.startswith(FORBIDDEN_PREFIXES))
    if bad:
        errors.append(f"包含禁止的路径：{bad[:5]}")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("manifest")
    ap.add_argument("--expect-commit")
    ap.add_argument("--allow-dirty", action="store_true", help="仅限本地检查")
    a = ap.parse_args()
    errors = verify(Path(a.zip), Path(a.manifest), a.expect_commit)
    if a.allow_dirty:
        errors = [e for e in errors if "未提交" not in e]
    if errors:
        print("制品校验失败：\n  " + "\n  ".join(errors))
        sys.exit(1)
    print("制品校验通过")


if __name__ == "__main__":
    main()

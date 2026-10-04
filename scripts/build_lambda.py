"""构建 Lambda 制品（arm64，Python 3.12）并写出绑定清单（ISE-027）。

  uv run python scripts/build_lambda.py            → dist/lambda.zip + dist/manifest.json
  uv run python scripts/build_lambda.py --no-deps  → 只打代码与资源（用于快速检查）

生产包排除 ledger/local（ADR-0007）、测试与开发依赖；zip 内顺序与时间戳固定，同一源码字节一致。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build" / "lambda"
RESOURCES = [
    "contracts/openapi.yaml",
    "contracts/model.schema.json",
    "seed/categories.json",
    "seed/currencies.json",
    "config/model-prices.json",
]
# Lambda python3.12 运行时为 Amazon Linux 2023（glibc 2.34），兼容 manylinux_2_28
PLATFORM = "aarch64-manylinux_2_28"
FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def _packaged(rel: Path) -> bool:
    """排除与构建路径相关、运行时不需要的文件，保证不同目录构建出相同摘要。

    bin/ 下的命令行脚本 shebang 含构建机解释器绝对路径；RECORD 记录这些脚本的哈希。
    """
    if "__pycache__" in rel.parts or rel.parts[0] == "bin":
        return False
    return not (rel.name == "RECORD" and rel.parent.name.endswith(".dist-info"))


def compile_contract(src: Path, dst: Path) -> None:
    """YAML 契约预转为 JSON（确定性输出），运行时冷启动免去 YAML 解析；转换前后内容必须相同。"""
    import yaml

    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if json.loads(text) != data:
        raise SystemExit("契约 YAML 含 JSON 无法表示的值")
    dst.write_text(text, encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def build(with_deps: bool) -> Path:
    for d in (BUILD, DIST):  # 清除旧制品，避免构建失败时校验到旧文件
        if d.exists():
            shutil.rmtree(d)
    BUILD.mkdir(parents=True)
    if with_deps:
        req = BUILD.parent / "requirements.txt"
        req.write_text(
            subprocess.run(
                ["uv", "export", "--no-dev", "--no-hashes", "--no-emit-project", "--frozen"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--target",
                str(BUILD),
                "--python-version",
                "3.12",
                "--python-platform",
                PLATFORM,
                "--only-binary",
                ":all:",
                "-r",
                str(req),
                "--quiet",
            ],
            cwd=ROOT,
            check=True,
        )
    shutil.copytree(
        ROOT / "backend" / "src" / "ledger",
        BUILD / "ledger",
        ignore=shutil.ignore_patterns("local", "__pycache__", "*.pyc"),
    )
    for rel in RESOURCES:
        dst = BUILD / "resources" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    compile_contract(
        ROOT / "contracts" / "openapi.yaml", BUILD / "resources" / "contracts" / "openapi.json"
    )
    DIST.mkdir(exist_ok=True)
    out = DIST / "lambda.zip"
    files = sorted(p for p in BUILD.rglob("*") if p.is_file() and _packaged(p.relative_to(BUILD)))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            info = zipfile.ZipInfo(f.relative_to(BUILD).as_posix(), FIXED_TIME)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, f.read_bytes())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deps", action="store_true")
    a = ap.parse_args()
    out = build(not a.no_deps)
    dirty = bool(git("status", "--porcelain"))
    manifest = {
        "artifact": out.name,
        "sha256": sha256(out),
        "bytes": out.stat().st_size,
        "commit": git("rev-parse", "HEAD"),
        "dirty_worktree": dirty,
        "uv_lock_sha256": sha256(ROOT / "uv.lock"),
        "pnpm_lock_sha256": sha256(ROOT / "pnpm-lock.yaml"),
        "python": "3.12",
        "platform": PLATFORM if not a.no_deps else "code-only",
        "resources": [*RESOURCES, "contracts/openapi.json（由 openapi.yaml 生成）"],
        "env": {"LEDGER_RESOURCE_DIR": "/var/task/resources"},
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (DIST / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("sha256", "bytes", "commit", "dirty_worktree")}))
    if dirty:
        print("警告：工作区有未提交修改，制品不能用于发布", file=sys.stderr)


if __name__ == "__main__":
    main()

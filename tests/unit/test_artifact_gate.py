"""OPS-08（门禁部分）：制品被改、含本地替身、缺资源或 commit 不符时拒绝发布（离线）。"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_artifact  # noqa: E402

GOOD = [*verify_artifact.REQUIRED, "ledger/__init__.py"]


def make(
    tmp: Path, names: list[str], commit: str = "abc", dirty: bool = False
) -> tuple[Path, Path]:
    z = tmp / "lambda.zip"
    with zipfile.ZipFile(z, "w") as f:
        for n in names:
            f.writestr(n, "x")
    m = tmp / "manifest.json"
    m.write_text(
        json.dumps(
            {
                "sha256": hashlib.sha256(z.read_bytes()).hexdigest(),
                "commit": commit,
                "dirty_worktree": dirty,
            }
        )
    )
    return z, m


def test_good_artifact_passes(tmp_path: Path) -> None:
    assert verify_artifact.verify(*make(tmp_path, GOOD), "abc") == []


def test_tampered_artifact_rejected(tmp_path: Path) -> None:
    z, m = make(tmp_path, GOOD)
    with zipfile.ZipFile(z, "a") as f:
        f.writestr("ledger/evil.py", "x")
    assert any("摘要不符" in e for e in verify_artifact.verify(z, m, None))


def test_local_substitutes_and_missing_resources_rejected(tmp_path: Path) -> None:
    errs = verify_artifact.verify(*make(tmp_path, [*GOOD[1:], "ledger/local/identity.py"]), None)
    assert any("禁止" in e for e in errs) and any("缺少" in e for e in errs)


def test_commit_mismatch_and_dirty_rejected(tmp_path: Path) -> None:
    errs = verify_artifact.verify(*make(tmp_path, GOOD, commit="other", dirty=True), "abc")
    assert any("预期" in e for e in errs) and any("未提交" in e for e in errs)


def test_build_excludes_path_dependent_files() -> None:
    """不同目录构建须得到相同摘要：命令行脚本（shebang 含绝对路径）与 RECORD 不打包。"""
    from build_lambda import _packaged

    assert not _packaged(Path("bin/httpx"))
    assert not _packaged(Path("httpx-0.28.1.dist-info/RECORD"))
    assert not _packaged(Path("ledger/__pycache__/x.pyc"))
    assert _packaged(Path("httpx-0.28.1.dist-info/METADATA"))
    assert _packaged(Path("ledger/runtime.py"))


def test_compiled_contract_matches_yaml(tmp_path: Path) -> None:
    """制品里的 openapi.json 与 YAML 内容完全相同；运行时优先读取 JSON。"""
    import yaml

    from build_lambda import compile_contract

    src = ROOT / "contracts" / "openapi.yaml"
    out = tmp_path / "openapi.json"
    compile_contract(src, out)
    assert json.loads(out.read_text("utf-8")) == yaml.safe_load(src.read_text("utf-8"))
    first = out.read_bytes()
    compile_contract(src, out)
    assert out.read_bytes() == first  # 确定性：同一 YAML 生成相同字节


def test_dirty_check_covers_all_packaged_inputs() -> None:
    """未提交修改只按影响制品的路径判断；打包读取的每个资源都必须在其中。"""
    from build_lambda import ARTIFACT_INPUTS, RESOURCES

    for rel in RESOURCES:
        assert any(rel == p or rel.startswith(p + "/") for p in ARTIFACT_INPUTS), rel
    assert "backend/src" in ARTIFACT_INPUTS and "uv.lock" in ARTIFACT_INPUTS

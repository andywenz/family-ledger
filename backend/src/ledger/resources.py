"""随代码发布的资源文件（契约、种子、价格）定位。

Lambda 包内资源位于 LEDGER_RESOURCE_DIR（打包脚本设置为包根目录）；本地默认为仓库根目录。
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def path(relative: str) -> Path:
    base = os.environ.get("LEDGER_RESOURCE_DIR")
    root = Path(base) if base else _REPO_ROOT
    return root / relative

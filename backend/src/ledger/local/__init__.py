"""仅限 LEDGER_ENV=local 的本地替身（ADR-0007）。生产构建排除整个包。"""

import os

if os.environ.get("LEDGER_ENV", "prod") != "local" and not os.environ.get("PYTEST_CURRENT_TEST"):
    raise RuntimeError("ledger.local 只能在 LEDGER_ENV=local 或测试中导入")

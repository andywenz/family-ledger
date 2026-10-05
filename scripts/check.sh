#!/usr/bin/env bash
# 运行全部本地静态与离线检查
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python scripts/check_contracts.py
uv run ruff check backend scripts tests
uv run mypy backend/src
uv run pytest -q
pnpm --filter @family-ledger/frontend gen:api
pnpm -r run check
pnpm --filter @family-ledger/frontend test
pnpm --filter @family-ledger/infra test

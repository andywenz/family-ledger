"""生产空账本首次初始化（D6，OPS-12）。在部署完成后由运维人员在本机执行一次。

  AWS_PROFILE=<目标账户> AWS_DEFAULT_REGION=ap-southeast-2 \\
  LEDGER_TABLE=<输出 TableName> LEDGER_DELETION_TABLE=<输出 JournalTableName> \\
  COGNITO_USER_POOL_ID=<输出 UserPoolId> \\
  uv run python -m ledger.ops.initialize --account <12 位账户 ID> --admin <登录名> --name <显示名>

安全检查（任何一项不通过即退出，不写入）：
- 当前凭证所属账户必须等于 --account（防止写错账户，例如课程账户）。
- 两张表与用户池必须存在（由 CDK 创建；本命令不建表）。
- 汇率只用真实供应商（Frankfurter／ECB），不允许合成汇率。
- 显示计划后需要输入 INIT 确认（--yes 可跳过，用于已审阅的重复执行）。
临时密码只打印一次到终端；请立即私下交付（见 docs/管理员操作说明.md「首发账号交付」）。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from typing import Any

from ledger.adapters.dynamo.store import Store
from ledger.application.context import AppContext
from ledger.application.initialize import InitError, initialize


def _require_env(name: str) -> str:
    v = os.environ.get(name, "")
    if not v:
        raise SystemExit(f"缺少环境变量 {name}")
    return v


def preflight(
    sts: Any, ddb: Any, cognito: Any, *, account: str, tables: list[str], pool: str
) -> None:
    actual = sts.get_caller_identity()["Account"]
    if actual != account:
        raise SystemExit(f"当前凭证属于账户 {actual}，与 --account {account} 不符；已停止")
    for t in tables:
        status = ddb.describe_table(TableName=t)["Table"]["TableStatus"]
        if status != "ACTIVE":
            raise SystemExit(f"表 {t} 状态为 {status}")
    cognito.describe_user_pool(UserPoolId=pool)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="目标 AWS 账户 ID（必须与当前凭证一致）")
    ap.add_argument("--admin", required=True, help="初始系统管理员登录名")
    ap.add_argument("--name", required=True, help="显示名")
    ap.add_argument("--days", type=int, default=120, help="回补汇率天数")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)
    if os.environ.get("LEDGER_ENV") == "local":
        raise SystemExit("LEDGER_ENV=local：本地请用 python -m ledger.local.bootstrap")

    import boto3

    from ledger.adapters.frankfurter import FrankfurterClient
    from ledger.identity.cognito import CognitoIdentityProvider

    region = (
        os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "ap-southeast-2"
    )
    table = _require_env("LEDGER_TABLE")
    journal = _require_env("LEDGER_DELETION_TABLE")
    pool = _require_env("COGNITO_USER_POOL_ID")
    cognito = boto3.client("cognito-idp", region_name=region)
    preflight(
        boto3.client("sts", region_name=region),
        boto3.client("dynamodb", region_name=region),
        cognito,
        account=args.account,
        tables=[table, journal],
        pool=pool,
    )
    print(
        f"将在账户 {args.account}／{region} 初始化：表 {table}，用户池 {pool}；"
        f"写入全局币种、最近 {args.days} 天 ECB 汇率，并创建系统管理员 {args.admin}。"
        "不创建家庭或账目，不导入历史。"
    )
    if not args.yes and input("输入 INIT 继续：").strip() != "INIT":
        raise SystemExit("已取消")

    ctx = AppContext(store=Store.connect(table, journal, region=region))
    idp = CognitoIdentityProvider(cognito, pool, os.environ.get("COGNITO_CLIENT_ID", ""))
    try:
        r = initialize(
            ctx,
            idp,
            FrankfurterClient(),
            admin_login=args.admin,
            display_name=args.name,
            today=datetime.now(UTC).date(),
            days=args.days,
        )
    except InitError as e:
        raise SystemExit(f"初始化未完成：{e}") from None
    print(
        f"完成：币种新增 {r.currencies_added}，汇率组新增 {r.rate_sets_added}"
        f"（最近 {r.latest_rate_date}）；"
        f"账号 {r.users}，家庭 {r.families}，账目 {r.entries}。"
    )
    if r.temporary_password:
        # 只写到终端，不进日志；交付后由用户首次登录强制修改
        sys.stdout.write(
            f"系统管理员 {r.admin_login} 的临时密码（仅显示一次）：{r.temporary_password}\n"
        )
    else:
        print(f"登录名 {r.admin_login} 已存在，未创建、未重置密码")


if __name__ == "__main__":
    main()

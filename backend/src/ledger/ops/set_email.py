"""给已有账号补邮箱（运维命令）：同时写应用账号资料与 Cognito 的 email 属性，不改密码、不发邮件。

  AWS_PROFILE=<目标账户> AWS_DEFAULT_REGION=ap-southeast-2 \\
  LEDGER_TABLE=<TableName> LEDGER_DELETION_TABLE=<JournalTableName> \\
  COGNITO_USER_POOL_ID=<UserPoolId> \\
  uv run python -m ledger.ops.set_email --account <账户 ID> --login <登录名> --email <邮箱>

可重复执行。先写 Cognito 再写应用资料，任一步失败重跑即可。
"""

from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="凭证账户必须等于此 ID")
    ap.add_argument("--login", required=True)
    ap.add_argument("--email", required=True)
    a = ap.parse_args(argv)

    import boto3

    from ledger.adapters.dynamo import keys
    from ledger.application.users import user_id_by_login
    from ledger.identity.ports import check_email
    from ledger.ops.history_import import _store_ctx

    if boto3.client("sts").get_caller_identity()["Account"] != a.account:
        raise SystemExit("凭证账户与 --account 不一致")
    pool = os.environ.get("COGNITO_USER_POOL_ID")
    if not pool:
        raise SystemExit("需要环境变量 COGNITO_USER_POOL_ID")
    try:
        email = check_email(a.email)
    except ValueError as e:
        raise SystemExit(str(e)) from e

    ctx = _store_ctx(False)
    uid = user_id_by_login(ctx, a.login)
    if uid is None:
        raise SystemExit(f"找不到登录名 {a.login}")
    profile = ctx.store.get(keys.user(uid), keys.PROFILE) or {}

    boto3.client("cognito-idp").admin_update_user_attributes(
        UserPoolId=pool,
        Username=uid,
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ],
    )
    tx = ctx.store.tx()
    tx.update(
        keys.user(uid),
        keys.PROFILE,
        "SET email = :e, version = version + :one",
        condition="version = :v",
        values={":e": email, ":one": 1, ":v": int(profile["version"])},
        on_fail=lambda _: SystemExit("账号资料刚被修改，请重跑"),
    )
    ctx.store.commit(tx)
    print(f"已为 {a.login} 设置邮箱（Cognito 与应用资料）；密码与会话未改动")


if __name__ == "__main__":
    main()

"""本地开发：为已有登录名设置新的临时密码（只打印到终端）。

运行：LEDGER_ENV=local uv run python -m ledger.local.reset_password <登录名>
"""

from __future__ import annotations

import sys

from ledger.adapters.dynamo import keys
from ledger.application.users import user_id_by_login
from ledger.identity.ports import generate_temporary_password
from ledger.runtime import build


def main() -> None:
    rt = build()
    uid = user_id_by_login(rt.ctx, sys.argv[1])
    if uid is None:
        sys.exit("登录名不存在")
    temp = generate_temporary_password()
    rt.auth.idp.admin_set_temporary_password(uid, temp)
    rt.ctx.store.client.update_item(
        TableName=rt.ctx.store.table,
        Key={"PK": {"S": keys.user(uid)}, "SK": {"S": keys.PROFILE}},
        UpdateExpression="SET must_change_password = :t",
        ExpressionAttributeValues={":t": {"BOOL": True}},
    )
    print(f"{sys.argv[1]} 的新临时密码（仅显示一次）：{temp}")


if __name__ == "__main__":
    main()

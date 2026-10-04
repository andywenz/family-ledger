"""端到端测试专用：创建一个随机登录名的系统管理员，输出 JSON（仅本地）。"""

from __future__ import annotations

import json
import secrets

from ledger.application.users import create_user_record
from ledger.identity.ports import generate_temporary_password
from ledger.runtime import build


def main() -> None:
    rt = build()
    login = f"e2e{secrets.token_hex(3)}"
    uid = f"u{secrets.token_hex(12)}"
    temp = generate_temporary_password()
    sub = rt.auth.idp.admin_create(uid, temp)
    create_user_record(
        rt.ctx,
        login_name=login,
        display_name="测试管理员",
        identity_sub=sub,
        is_system_admin=True,
        user_id=uid,
    )
    print(json.dumps({"login": login, "password": temp}))


if __name__ == "__main__":
    main()

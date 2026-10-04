"""账号的业务侧记录（固定 user_id、登录名映射、显示名、会话边界）。

身份服务（Cognito／本地替身）在 D2 接入；这里只维护应用的权威映射，不保存密码。
"""

from __future__ import annotations

from typing import Any

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.store import Tx
from ledger.domain.errors import DomainError, ValidationFailed

from .context import AppContext

LOGIN_RE_MIN, LOGIN_RE_MAX = 3, 32


def _check_login(name: str) -> None:
    import re

    if not re.match(r"^[A-Za-z][A-Za-z0-9_.-]{2,31}$", name or ""):
        raise ValidationFailed("登录名需为 3–32 位字母开头的字母、数字或 _.-")


def create_user_record(
    ctx: AppContext,
    *,
    login_name: str,
    display_name: str,
    identity_sub: str,
    is_system_admin: bool = False,
    must_change_password: bool = True,
    user_id: str | None = None,
) -> dict[str, Any]:
    """创建账号记录及唯一登录名映射（单事务）。供系统管理员用例与本地初始化调用。"""
    _check_login(login_name)
    if not 1 <= len(display_name) <= 24:
        raise ValidationFailed("显示名需为 1–24 个字符")
    uid = user_id or ctx.ids()
    now = keys.ts(ctx.clock())
    profile = {
        "PK": keys.user(uid),
        "SK": keys.PROFILE,
        "type": "user",
        "user_id": uid,
        "login_name": login_name,
        "display_name": display_name,
        "cognito_sub": identity_sub,
        "status": "active",
        "is_system_admin": is_system_admin,
        "must_change_password": must_change_password,
        "session_epoch": 0,
        "version": 1,
        "created_at": now,
    }
    tx = Tx(ctx.store.table)
    tx.put_new(profile)
    tx.put_new(
        {
            "PK": keys.login(login_name),
            "SK": "LOGIN",
            "type": "login",
            "user_id": uid,
            "state": "active",
        },
        on_fail=lambda _: DomainError("登录名已被使用", code="login_name_taken"),
    )
    tx.put_new({"PK": keys.sub(identity_sub), "SK": "SUB", "type": "sub", "user_id": uid})
    tx.put_new({"PK": "USERS", "SK": uid, "type": "user_index"})
    ctx.store.commit(tx)
    return profile


def user_id_by_login(ctx: AppContext, login_name: str) -> str | None:
    item = ctx.store.get(keys.login(login_name), "LOGIN")
    return item["user_id"] if item and item.get("state") == "active" else None

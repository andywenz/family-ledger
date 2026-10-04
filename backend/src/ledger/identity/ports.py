"""身份服务与令牌校验的接口。业务层只依赖这里，不直接依赖 Cognito 或本地替身。"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from typing import Protocol


class InvalidCredentials(Exception):
    """登录名、密码或 refresh token 无效（不区分原因，避免枚举账号）。"""


class InvalidPassword(Exception):
    """新密码不符合规则。"""


class IdentityUnavailable(Exception):
    """身份服务暂不可用或限流。"""


@dataclass(frozen=True)
class AuthTokens:
    access_token: str
    expires_in: int
    refresh_token: str | None = None


@dataclass(frozen=True)
class Challenge:
    name: str  # NEW_PASSWORD_REQUIRED
    session: str


@dataclass(frozen=True)
class Claims:
    sub: str
    username: str
    issued_at: int  # epoch 秒
    expires_at: int


class IdentityProvider(Protocol):
    def authenticate(self, username: str, password: str) -> AuthTokens | Challenge: ...

    def respond_new_password(
        self, username: str, session: str, new_password: str
    ) -> AuthTokens: ...

    def refresh(self, refresh_token: str) -> AuthTokens: ...

    def revoke(self, refresh_token: str) -> None: ...

    def change_password(self, access_token: str, current: str, new: str) -> None: ...

    def admin_create(self, username: str, temporary_password: str) -> str:
        """创建用户并返回身份服务 sub；用户已存在时设置新临时密码并返回原 sub。"""
        ...

    def admin_set_temporary_password(self, username: str, temporary_password: str) -> None: ...

    def admin_sign_out(self, username: str) -> None: ...

    def admin_set_enabled(self, username: str, enabled: bool) -> None: ...


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Claims: ...


MIN_PASSWORD = 10


def check_password_policy(password: str, login_name: str | None = None) -> None:
    if (
        not isinstance(password, str)
        or len(password) < MIN_PASSWORD
        or len(password) > 256
        or not any(c.isalpha() for c in password)
        or not any(c.isdigit() for c in password)
        or (login_name is not None and password.lower() == login_name.lower())
    ):
        raise InvalidPassword("密码至少 10 位，需同时包含字母和数字，且不能与登录名相同")


def generate_temporary_password() -> str:
    """满足本地与 Cognito 常见策略：大小写、数字、符号各至少一个，共 16 位。"""
    alphabet = string.ascii_letters + string.digits + "!@#%^*-_=+"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
            and any(not c.isalnum() for c in pw)
        ):
            return pw

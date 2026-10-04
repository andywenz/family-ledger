"""本地身份替身：行为模拟 Cognito 的首次改密挑战、refresh 与撤销。

密码只存 scrypt 哈希；令牌用本地 RSA 密钥签名，issuer 固定为 LOCAL_ISSUER，生产校验器会拒绝。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ledger.adapters.dynamo.store import Store, Tx
from ledger.identity.ports import (
    AuthTokens,
    Challenge,
    InvalidCredentials,
    check_password_policy,
)

LOCAL_ISSUER = "urn:family-ledger:local-identity"
LOCAL_CLIENT_ID = "local-web"
ACCESS_TTL = 900
REFRESH_TTL = 30 * 86400


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


@dataclass
class _Key:
    private: Any

    @property
    def public(self) -> Any:
        return self.private.public_key()

    # 兼容 PyJWKClient 接口，供 JwtVerifier 使用
    def get_signing_key_from_jwt(self, _token: str) -> Any:
        return type("K", (), {"key": self.public})()


def load_key(path: Path | None) -> _Key:
    if path is not None and path.exists():
        return _Key(serialization.load_pem_private_key(path.read_bytes(), password=None))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        path.chmod(0o600)
    return _Key(key)


class LocalIdentityProvider:
    def __init__(self, store: Store, key: _Key, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.key = key
        self.clock = clock

    def _pk(self, username: str) -> str:
        return f"LOCALIDP#{username}"

    def _user(self, username: str) -> dict[str, Any] | None:
        return self.store.get(self._pk(username), "USER")

    def _issue(self, username: str, with_refresh: bool = True) -> AuthTokens:
        now = int(self.clock())
        user = self._user(username)
        assert user is not None
        access = jwt.encode(
            {
                "iss": LOCAL_ISSUER,
                "sub": user["sub"],
                "username": username,
                "iat": now,
                "exp": now + ACCESS_TTL,
                "token_use": "access",
                "client_id": LOCAL_CLIENT_ID,
                "jti": secrets.token_hex(8),
            },
            self.key.private,
            algorithm="RS256",
        )
        refresh = None
        if with_refresh:
            raw = secrets.token_urlsafe(32)
            refresh = f"{username}.{raw}"
            tx = Tx(self.store.table)
            tx.put(
                {
                    "PK": self._pk(username),
                    "SK": f"REFRESH#{hashlib.sha256(raw.encode()).hexdigest()}",
                    "type": "local_refresh",
                    "ttl": now + REFRESH_TTL,
                    "issued_at": now,
                }
            )
            self.store.commit(tx)
        return AuthTokens(access, ACCESS_TTL, refresh)

    def _verify_password(self, user: dict[str, Any], password: str) -> bool:
        expected = _hash(password, bytes.fromhex(user["salt"]))
        return hmac.compare_digest(expected, user["password_hash"])

    def authenticate(self, username: str, password: str) -> AuthTokens | Challenge:
        user = self._user(username)
        if user is None:
            _hash(password, b"0" * 16)  # 保持耗时一致，避免以时间差判断账号是否存在
            raise InvalidCredentials("认证失败")
        if not user.get("enabled", True) or not self._verify_password(user, password):
            raise InvalidCredentials("认证失败")
        if user.get("temporary"):
            session = self._challenge_token(username)
            return Challenge("NEW_PASSWORD_REQUIRED", session)
        return self._issue(username)

    def _challenge_token(self, username: str) -> str:
        now = int(self.clock())
        return jwt.encode(
            {
                "iss": LOCAL_ISSUER,
                "purpose": "new_password",
                "username": username,
                "iat": now,
                "exp": now + 300,
            },
            self.key.private,
            algorithm="RS256",
        )

    def respond_new_password(self, username: str, session: str, new_password: str) -> AuthTokens:
        try:
            data = jwt.decode(session, self.key.public, algorithms=["RS256"], issuer=LOCAL_ISSUER)
        except jwt.PyJWTError as e:
            raise InvalidCredentials("挑战已失效") from e
        if data.get("purpose") != "new_password" or data.get("username") != username:
            raise InvalidCredentials("挑战已失效")
        check_password_policy(new_password)
        self._set_password(username, new_password, temporary=False)
        return self._issue(username)

    def refresh(self, refresh_token: str) -> AuthTokens:
        username, _, raw = refresh_token.partition(".")
        item = self.store.get(
            self._pk(username), f"REFRESH#{hashlib.sha256(raw.encode()).hexdigest()}"
        )
        user = self._user(username)
        if item is None or user is None or not user.get("enabled", True):
            raise InvalidCredentials("refresh token 无效")
        if int(item["ttl"]) < int(self.clock()) or int(item["issued_at"]) < int(
            user.get("signed_out_at", 0)
        ):
            raise InvalidCredentials("refresh token 已失效")
        t = self._issue(username, with_refresh=False)
        return AuthTokens(t.access_token, t.expires_in, refresh_token)

    def revoke(self, refresh_token: str) -> None:
        username, _, raw = refresh_token.partition(".")
        self.store.client.delete_item(
            TableName=self.store.table,
            Key={
                "PK": {"S": self._pk(username)},
                "SK": {"S": f"REFRESH#{hashlib.sha256(raw.encode()).hexdigest()}"},
            },
        )

    def change_password(self, access_token: str, current: str, new: str) -> None:
        data = jwt.decode(access_token, self.key.public, algorithms=["RS256"], issuer=LOCAL_ISSUER)
        username = str(data["username"])
        user = self._user(username)
        if user is None or not self._verify_password(user, current):
            raise InvalidCredentials("当前密码不正确")
        check_password_policy(new)
        self._set_password(username, new, temporary=False)

    def _set_password(
        self, username: str, password: str, *, temporary: bool, sub: str | None = None
    ) -> str:
        user = self._user(username) or {}
        salt = secrets.token_bytes(16)
        sub = sub or user.get("sub") or f"local-{secrets.token_hex(8)}"
        tx = Tx(self.store.table)
        tx.put(
            {
                **user,
                "PK": self._pk(username),
                "SK": "USER",
                "type": "local_user",
                "sub": sub,
                "salt": salt.hex(),
                "password_hash": _hash(password, salt),
                "temporary": temporary,
                "enabled": user.get("enabled", True),
            }
        )
        self.store.commit(tx)
        return sub

    def admin_create(self, username: str, temporary_password: str) -> str:
        return self._set_password(username, temporary_password, temporary=True)

    def admin_set_temporary_password(self, username: str, temporary_password: str) -> None:
        if self._user(username) is None:
            raise InvalidCredentials("用户不存在")
        self._set_password(username, temporary_password, temporary=True)

    def admin_sign_out(self, username: str) -> None:
        user = self._user(username)
        if user is not None:
            tx = Tx(self.store.table)
            tx.put({**user, "signed_out_at": int(self.clock()) + 1})
            self.store.commit(tx)

    def admin_set_enabled(self, username: str, enabled: bool) -> None:
        user = self._user(username)
        if user is not None:
            tx = Tx(self.store.table)
            tx.put({**user, "enabled": enabled})
            self.store.commit(tx)

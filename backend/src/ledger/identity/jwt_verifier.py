"""RS256 access token 校验：固定 issuer、client_id 与 token_use（ADR-0012 第 8 条）。"""

from __future__ import annotations

from typing import Any

import jwt

from .ports import Claims, InvalidCredentials


class JwtVerifier:
    def __init__(self, issuer: str, client_id: str, key_resolver: Any) -> None:
        """key_resolver：PyJWKClient（生产 JWKS）或返回本地公钥的对象。"""
        self.issuer = issuer
        self.client_id = client_id
        self.keys = key_resolver

    def verify(self, token: str) -> Claims:
        try:
            key = self.keys.get_signing_key_from_jwt(token).key
            data = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": False},
                leeway=5,
            )
        except (jwt.PyJWTError, KeyError, ValueError) as e:
            raise InvalidCredentials("令牌无效") from e
        if data.get("token_use") != "access" or data.get("client_id") != self.client_id:
            raise InvalidCredentials("令牌类型或客户端不符")
        return Claims(
            sub=str(data["sub"]),
            username=str(data.get("username", "")),
            issued_at=int(data["iat"]),
            expires_at=int(data["exp"]),
        )

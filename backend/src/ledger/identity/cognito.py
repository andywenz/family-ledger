"""Cognito 用户池适配器（USER_PASSWORD_AUTH，无 client secret 的公共应用客户端）。

D2 只以 botocore Stubber 做离线请求形状测试；真实行为在 D5 经授权后验收。
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from .ports import AuthTokens, Challenge, IdentityUnavailable, InvalidCredentials, InvalidPassword

_CRED = {
    "NotAuthorizedException",
    "UserNotFoundException",
    "UserNotConfirmedException",
    "PasswordResetRequiredException",
}
_PASSWORD = {"InvalidPasswordException", "InvalidParameterException"}
_UNAVAILABLE = {"TooManyRequestsException", "LimitExceededException", "InternalErrorException"}


def _map(e: ClientError) -> Exception:
    code = e.response.get("Error", {}).get("Code", "")
    if code in _CRED:
        return InvalidCredentials("认证失败")
    if code in _PASSWORD:
        return InvalidPassword("密码不符合规则")
    if code in _UNAVAILABLE:
        return IdentityUnavailable(code)
    return RuntimeError(f"身份服务错误：{code}")


class CognitoIdentityProvider:
    def __init__(self, client: Any, user_pool_id: str, client_id: str) -> None:
        self.c = client
        self.pool = user_pool_id
        self.client_id = client_id

    def _call(self, name: str, **kw: Any) -> Any:
        try:
            return getattr(self.c, name)(**kw)
        except ClientError as e:
            raise _map(e) from e

    @staticmethod
    def _tokens(result: dict[str, Any]) -> AuthTokens:
        r = result["AuthenticationResult"]
        return AuthTokens(r["AccessToken"], int(r["ExpiresIn"]), r.get("RefreshToken"))

    def authenticate(self, username: str, password: str) -> AuthTokens | Challenge:
        out = self._call(
            "initiate_auth",
            ClientId=self.client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
        if out.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
            return Challenge("NEW_PASSWORD_REQUIRED", out["Session"])
        if "AuthenticationResult" not in out:
            raise InvalidCredentials(f"不支持的挑战：{out.get('ChallengeName')}")
        return self._tokens(out)

    def respond_new_password(self, username: str, session: str, new_password: str) -> AuthTokens:
        out = self._call(
            "respond_to_auth_challenge",
            ClientId=self.client_id,
            ChallengeName="NEW_PASSWORD_REQUIRED",
            Session=session,
            ChallengeResponses={"USERNAME": username, "NEW_PASSWORD": new_password},
        )
        return self._tokens(out)

    def refresh(self, refresh_token: str) -> AuthTokens:
        out = self._call(
            "initiate_auth",
            ClientId=self.client_id,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh_token},
        )
        t = self._tokens(out)
        return AuthTokens(t.access_token, t.expires_in, refresh_token)

    def revoke(self, refresh_token: str) -> None:
        self._call("revoke_token", Token=refresh_token, ClientId=self.client_id)

    def change_password(self, access_token: str, current: str, new: str) -> None:
        self._call(
            "change_password",
            AccessToken=access_token,
            PreviousPassword=current,
            ProposedPassword=new,
        )

    def admin_create(self, username: str, temporary_password: str) -> str:
        try:
            out = self.c.admin_create_user(
                UserPoolId=self.pool,
                Username=username,
                TemporaryPassword=temporary_password,
                MessageAction="SUPPRESS",
            )
            attrs = out["User"]["Attributes"]
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "UsernameExistsException":
                raise _map(e) from e
            self.admin_set_temporary_password(username, temporary_password)
            attrs = self._call("admin_get_user", UserPoolId=self.pool, Username=username)[
                "UserAttributes"
            ]
        return str(next(a["Value"] for a in attrs if a["Name"] == "sub"))

    def admin_set_temporary_password(self, username: str, temporary_password: str) -> None:
        self._call(
            "admin_set_user_password",
            UserPoolId=self.pool,
            Username=username,
            Password=temporary_password,
            Permanent=False,
        )

    def admin_sign_out(self, username: str) -> None:
        self._call("admin_user_global_sign_out", UserPoolId=self.pool, Username=username)

    def admin_set_enabled(self, username: str, enabled: bool) -> None:
        name = "admin_enable_user" if enabled else "admin_disable_user"
        self._call(name, UserPoolId=self.pool, Username=username)

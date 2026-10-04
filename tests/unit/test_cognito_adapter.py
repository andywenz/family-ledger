"""Cognito 适配器请求形状与错误映射（botocore Stubber，离线；不代表真实 Cognito 行为）。"""

from __future__ import annotations

import boto3
import pytest
from botocore.stub import Stubber

from ledger.identity.cognito import CognitoIdentityProvider
from ledger.identity.ports import AuthTokens, Challenge, InvalidCredentials, InvalidPassword

POOL, CLIENT = "ap-southeast-2_TEST", "client123"


@pytest.fixture
def stubbed() -> tuple[CognitoIdentityProvider, Stubber]:
    client = boto3.client(
        "cognito-idp",
        region_name="ap-southeast-2",
        aws_access_key_id="x",
        aws_secret_access_key="x",
    )  # noqa: S106
    return CognitoIdentityProvider(client, POOL, CLIENT), Stubber(client)


def test_user_password_auth_and_challenge(stubbed) -> None:  # noqa: ANN001
    idp, st = stubbed
    st.add_response(
        "initiate_auth",
        {"ChallengeName": "NEW_PASSWORD_REQUIRED", "Session": "session-token-0123456789"},
        {
            "ClientId": CLIENT,
            "AuthFlow": "USER_PASSWORD_AUTH",
            "AuthParameters": {"USERNAME": "u1", "PASSWORD": "pw"},
        },
    )
    st.add_response(
        "respond_to_auth_challenge",
        {"AuthenticationResult": {"AccessToken": "a", "ExpiresIn": 3600, "RefreshToken": "r"}},
        {
            "ClientId": CLIENT,
            "ChallengeName": "NEW_PASSWORD_REQUIRED",
            "Session": "session-token-0123456789",
            "ChallengeResponses": {"USERNAME": "u1", "NEW_PASSWORD": "New-pass-123"},
        },
    )
    with st:
        out = idp.authenticate("u1", "pw")
        assert out == Challenge("NEW_PASSWORD_REQUIRED", "session-token-0123456789")
        tokens = idp.respond_new_password("u1", "session-token-0123456789", "New-pass-123")
        assert tokens == AuthTokens("a", 3600, "r")


def test_errors_mapped_without_revealing_account(stubbed) -> None:  # noqa: ANN001
    idp, st = stubbed
    st.add_client_error("initiate_auth", "UserNotFoundException")
    st.add_client_error("initiate_auth", "NotAuthorizedException")
    st.add_client_error("change_password", "InvalidPasswordException")
    with st:
        with pytest.raises(InvalidCredentials):
            idp.authenticate("missing", "x")
        with pytest.raises(InvalidCredentials):
            idp.authenticate("u1", "wrong")
        with pytest.raises(InvalidPassword):
            idp.change_password("tok", "a", "b")


def test_admin_create_suppresses_email_and_handles_existing(stubbed) -> None:  # noqa: ANN001
    idp, st = stubbed
    st.add_response(
        "admin_create_user",
        {"User": {"Attributes": [{"Name": "sub", "Value": "sub-1"}]}},
        {
            "UserPoolId": POOL,
            "Username": "u1",
            "TemporaryPassword": "T-1aaaaaaa",
            "MessageAction": "SUPPRESS",
        },
    )
    st.add_client_error("admin_create_user", "UsernameExistsException")
    st.add_response(
        "admin_set_user_password",
        {},
        {"UserPoolId": POOL, "Username": "u2", "Password": "T-2aaaaaaa", "Permanent": False},
    )
    st.add_response(
        "admin_get_user",
        {"Username": "u2", "UserAttributes": [{"Name": "sub", "Value": "sub-2"}]},
        {"UserPoolId": POOL, "Username": "u2"},
    )
    st.add_response("admin_user_global_sign_out", {}, {"UserPoolId": POOL, "Username": "u2"})
    with st:
        assert idp.admin_create("u1", "T-1aaaaaaa") == "sub-1"
        assert idp.admin_create("u2", "T-2aaaaaaa") == "sub-2"
        idp.admin_sign_out("u2")
    st.assert_no_pending_responses()

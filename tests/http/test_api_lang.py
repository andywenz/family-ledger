"""英文界面：请求头 X-Ledger-Lang: en 时错误信息为英文，错误码不变；默认仍为中文。"""

from __future__ import annotations

from ledger.http.app import Runtime

from .conftest import Account, Api


def test_error_message_follows_language_header(
    admin_api: tuple[Api, Account], runtime: Runtime
) -> None:
    _, acct = admin_api
    zh = Api(runtime, ip="192.0.2.31")
    s1, d1 = zh.login(acct.login, "Wrong-pass-123")
    en = Api(runtime, ip="192.0.2.32")
    s2, d2 = en.post(
        "/auth/login",
        {"login_name": acct.login, "password": "Wrong-pass-123"},
        auth=False,
        headers={"x-ledger-lang": "en"},
    )
    assert (s1, d1["error"]["code"], d1["error"]["message"]) == (
        401,
        "unauthenticated",
        "登录名或密码错误",
    )
    assert (s2, d2["error"]["code"], d2["error"]["message"]) == (
        401,
        "unauthenticated",
        "Incorrect username or password",
    )


def test_unknown_endpoint_in_english(runtime: Runtime) -> None:
    status, data = Api(runtime).get(
        "/no-such-endpoint", headers={"x-ledger-lang": "en"}, auth=False
    )
    assert (status, data["error"]["message"]) == (404, "Endpoint not found")

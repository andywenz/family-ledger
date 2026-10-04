"""AUTH-01／02／03／04、会话、CSRF、限流（离线：本地身份替身）。"""

from __future__ import annotations

import time

from ledger.http.app import Runtime

from .conftest import Account, Api, complete_first_login, create_member


def test_auth01_first_login_requires_password_change(
    admin_api: tuple[Api, Account], runtime: Runtime
) -> None:
    admin, _ = admin_api
    acct = create_member(admin, f"xc{time.time_ns() % 10**8}")
    m = Api(runtime)
    status, data = m.login(acct.login, acct.password)
    assert status == 200 and data["status"] == "challenge" and m.token is None
    # 改密前不能访问业务接口（没有令牌）
    assert m.get("/families")[0] == 401
    # 新密码不合规
    status, data = m.post(
        "/auth/challenge",
        {"challenge_session": data["challenge_session"], "new_password": "onlyletters"},
        auth=False,
    )
    assert status == 422
    complete_first_login(m, acct, "Member-pass-01")
    status, me = m.get("/me")
    assert status == 200 and me["login_name"] == acct.login and me["families"] == []
    assert "ledger_refresh" in m.cookies and "ledger_csrf" in m.cookies


def test_wrong_password_and_unknown_login_look_the_same(
    admin_api: tuple[Api, Account], runtime: Runtime
) -> None:
    _, acct = admin_api
    a, b = Api(runtime, ip="192.0.2.1"), Api(runtime, ip="192.0.2.2")
    s1, d1 = a.login(acct.login, "Wrong-pass-123")
    s2, d2 = b.login("no-such-user", "Wrong-pass-123")
    assert (
        (s1, d1["error"]["code"], d1["error"]["message"])
        == (s2, d2["error"]["code"], d2["error"]["message"])
        == (401, "unauthenticated", "登录名或密码错误")
    )


def test_login_rate_limited(admin_api: tuple[Api, Account], runtime: Runtime) -> None:
    _, acct = admin_api
    api = Api(runtime, ip="192.0.2.50")
    # 夹具已用该登录名登录过 1 次；成功与失败都计数，窗口内第 11 次起拒绝
    codes = [api.login(acct.login, "Wrong-pass-123")[0] for _ in range(10)]
    assert codes[:9] == [401] * 9 and codes[9] == 429
    assert "retry-after" in api.last["headers"]
    # 限流按登录名：正确密码也被挡住，避免借成功登录绕过
    assert api.login(acct.login, acct.password)[0] == 429


def test_auth02_rename_is_atomic(admin_api: tuple[Api, Account], runtime: Runtime) -> None:
    api, acct = admin_api
    _, me = api.get("/me")
    new = f"user{time.time_ns() % 10**6}"
    status, op = api.patch(
        "/me/login-name", {"new_login_name": new.upper(), "expected_version": me["version"]}
    )
    assert status == 200 and op["state"] == "done"
    assert api.get(f"/me/login-name/operations/{op['op_id']}")[0] == 200
    fresh = Api(runtime, ip="192.0.2.77")
    assert fresh.login(acct.login, acct.password)[0] == 401  # 旧名失效
    assert fresh.login(new.lower(), acct.password)[0] == 200  # 新名不区分大小写
    # 与他人冲突
    other = create_member(api, f"taken{time.time_ns() % 10**6}")
    _, me2 = api.get("/me")
    status, err = api.patch(
        "/me/login-name", {"new_login_name": other.login, "expected_version": me2["version"]}
    )
    assert status == 409 and err["error"]["code"] == "login_name_taken"


def test_auth03_password_change_revokes_other_sessions(
    admin_api: tuple[Api, Account], runtime: Runtime
) -> None:
    api, acct = admin_api
    other = Api(runtime, ip="192.0.2.90")
    assert other.login(acct.login, acct.password)[0] == 200
    status, _ = api.post(
        "/me/password", {"current_password": acct.password, "new_password": "Changed-pass-77"}
    )
    assert status == 204
    # 其他会话：access token 被拒，refresh 也失效
    assert other.get("/me")[0] == 401
    status, _ = other.post(
        "/auth/refresh", headers={"x-csrf-token": other.cookies["ledger_csrf"]}, auth=False
    )
    assert status == 401
    # 当前浏览器：拿到新 refresh cookie，刷新后可继续
    status, data = api.post(
        "/auth/refresh", headers={"x-csrf-token": api.cookies["ledger_csrf"]}, auth=False
    )
    assert status == 200
    api.token = data["access_token"]
    assert api.get("/me")[0] == 200


def test_auth03_admin_reset_forces_change_and_revokes(
    admin_api: tuple[Api, Account], runtime: Runtime
) -> None:
    admin, _ = admin_api
    acct = create_member(admin, f"rs{time.time_ns() % 10**8}")
    m = Api(runtime, ip="192.0.2.120")
    complete_first_login(m, acct, "Member-pass-02")
    assert m.get("/me")[0] == 200
    status, data = admin.post(f"/admin/users/{acct.user_id}/reset-password")
    assert status == 200 and data["user"]["must_change_password"] is True
    assert m.get("/me")[0] == 401
    status, data = Api(runtime, ip="192.0.2.121").login(acct.login, data["temporary_password"])
    assert data["status"] == "challenge"


def test_auth04_non_admin_cannot_use_admin_api(
    admin_api: tuple[Api, Account], runtime: Runtime
) -> None:
    admin, _ = admin_api
    acct = create_member(admin, f"na{time.time_ns() % 10**8}")
    m = Api(runtime, ip="192.0.2.130")
    complete_first_login(m, acct, "Member-pass-03")
    # 成为家庭管理员也没有系统管理员权限
    _, fam = m.post("/families", {"name": "我家"})
    assert fam["my_role"] == "admin"
    for method, path, body in [
        ("GET", "/admin/users", None),
        ("POST", "/admin/users", {"login_name": "zzz", "display_name": "z"}),
        ("POST", f"/admin/users/{admin_api[1].user_id}/reset-password", None),
    ]:
        status, err = m.call(method, path, body)
        assert status == 403 and err["error"]["code"] == "forbidden"


def test_admin_create_user_replay_redacts_password(admin_api: tuple[Api, Account]) -> None:
    api, _ = admin_api
    body = {"login_name": f"rp{time.time_ns() % 10**8}", "display_name": "重放"}
    s1, d1 = api.post("/admin/users", body, key="replay-key-0000000001")
    s2, d2 = api.post("/admin/users", body, key="replay-key-0000000001")
    assert s1 == s2 == 201 and "temporary_password" in d1
    assert "temporary_password" not in d2 and d2["temporary_password_redacted"] is True
    assert d1["user"]["user_id"] == d2["user"]["user_id"]


def test_csrf_and_origin_required_for_refresh(admin_api: tuple[Api, Account]) -> None:
    api, _ = admin_api
    assert api.post("/auth/refresh", auth=False)[0] == 403  # 缺少头
    assert api.post("/auth/refresh", headers={"x-csrf-token": "x" * 20}, auth=False)[0] == 403
    status, _ = api.post(
        "/auth/refresh",
        headers={"x-csrf-token": api.cookies["ledger_csrf"], "origin": "https://evil.example"},
        auth=False,
    )
    assert status == 403
    status, _ = api.post(
        "/auth/logout", headers={"x-csrf-token": api.cookies["ledger_csrf"]}, auth=False
    )
    assert status == 204 and "ledger_refresh" not in api.cookies


def test_disabled_account_loses_access(admin_api: tuple[Api, Account], runtime: Runtime) -> None:
    admin, _ = admin_api
    acct = create_member(admin, f"ds{time.time_ns() % 10**8}")
    m = Api(runtime, ip="192.0.2.140")
    complete_first_login(m, acct, "Member-pass-04")
    _, users = admin.get("/admin/users")
    row = next(u for u in users["items"] if u["user_id"] == acct.user_id)
    status, _ = admin.patch(
        f"/admin/users/{acct.user_id}", {"status": "disabled", "expected_version": row["version"]}
    )
    assert status == 200
    assert m.get("/me")[0] == 401
    assert Api(runtime, ip="192.0.2.141").login(acct.login, acct.password)[0] == 401


def test_request_validation_and_routing(admin_api: tuple[Api, Account]) -> None:
    api, _ = admin_api
    status, err = api.post("/families", {"name": "", "extra": 1})
    assert status == 422 and {f["field"] for f in err["error"]["details"]["fields"]} >= {"(body)"}
    assert api.get("/no/such/route")[0] == 404
    status, err = api.post("/families", {"name": "x"}, headers={"idempotency-key": "bad"})
    assert status == 422
    assert api.get("/families/bad$id")[0] == 422
    status, err = api.get("/health", auth=False)
    assert status == 200 and err["env"] == "local"

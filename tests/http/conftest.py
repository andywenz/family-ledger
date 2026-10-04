"""HTTP 测试：完整分发链路（事件 → 契约校验 → 鉴权 → 用例），每个响应按 OpenAPI 校验。

身份服务为本地替身（ADR-0007），不代表 Cognito 真实行为。证据层级：离线。
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from ledger.adapters.dynamo.store import Store
from ledger.application.users import create_user_record
from ledger.http import contract
from ledger.http.app import Runtime, handle_event, match
from ledger.identity.ports import generate_temporary_password
from ledger.runtime import build
from ledger.settings import Settings

from ..conftest import ENDPOINT, new_key

ORIGIN = "http://localhost:5173"
CONTRACT_VIOLATIONS: list[str] = []


@pytest.fixture(scope="session")
def runtime(store: Store, tmp_path_factory: pytest.TempPathFactory) -> Runtime:
    data = tmp_path_factory.mktemp("local-data")
    s = Settings(
        env="local",
        table=store.table,
        journal_table=store.journal_table,
        dynamodb_endpoint=ENDPOINT,
        allowed_origins=(ORIGIN,),
        local_data_dir=str(data),
    )
    return build(s)


@dataclass
class Api:
    rt: Runtime
    token: str | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    ip: str = "203.0.113.10"
    last: dict[str, Any] = field(default_factory=dict)

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        query: dict[str, Any] | None = None,
        key: str | None = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> tuple[int, Any]:
        h = {"origin": ORIGIN, **(headers or {})}
        if auth and self.token:
            h["authorization"] = f"Bearer {self.token}"
        found = match(method, path)
        if found and found[0].spec.get("x-idempotent") and "idempotency-key" not in h:
            h["idempotency-key"] = key or new_key()
        q = {
            k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in (query or {}).items()
        }
        event = {
            "version": "2.0",
            "rawPath": "/v1" + path,
            "rawQueryString": urlencode(q),
            "headers": h,
            "cookies": [f"{k}={v}" for k, v in self.cookies.items()],
            "requestContext": {
                "http": {"method": method, "sourceIp": self.ip},
                "requestId": secrets.token_hex(6),
            },
            "body": json.dumps(body) if body is not None else None,
            "isBase64Encoded": False,
        }
        resp = handle_event(event, self.rt)
        self.last = resp
        for c in resp.get("cookies", []):
            name, _, rest = c.partition("=")
            value = rest.split(";", 1)[0]
            if "Max-Age=0" in c:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        data = json.loads(resp["body"]) if resp.get("body") else None
        if found:
            CONTRACT_VIOLATIONS.extend(
                contract.response_errors(found[0].op_id, resp["statusCode"], data)
            )
        return resp["statusCode"], data

    def get(self, path: str, **kw: Any) -> tuple[int, Any]:
        return self.call("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> tuple[int, Any]:
        return self.call("POST", path, body, **kw)

    def patch(self, path: str, body: Any = None, **kw: Any) -> tuple[int, Any]:
        return self.call("PATCH", path, body, **kw)

    def delete(self, path: str, **kw: Any) -> tuple[int, Any]:
        return self.call("DELETE", path, **kw)

    def login(self, login_name: str, password: str) -> tuple[int, Any]:
        status, data = self.post(
            "/auth/login", {"login_name": login_name, "password": password}, auth=False
        )
        if status == 200 and data.get("status") == "ok":
            self.token = data["access_token"]
        return status, data


@dataclass
class Account:
    login: str
    password: str
    user_id: str


def bootstrap_admin(rt: Runtime) -> Account:
    """与 scripts/bootstrap 相同的初始化路径：身份服务建用户＋应用记录。"""
    login = f"admin{secrets.token_hex(3)}"
    uid = f"u{secrets.token_hex(12)}"
    temp = generate_temporary_password()
    sub = rt.auth.idp.admin_create(uid, temp)
    create_user_record(
        rt.ctx,
        login_name=login,
        display_name="管理员甲",
        identity_sub=sub,
        is_system_admin=True,
        user_id=uid,
    )
    return Account(login, temp, uid)


def complete_first_login(api: Api, acct: Account, new_password: str) -> None:
    status, data = api.login(acct.login, acct.password)
    assert status == 200 and data["status"] == "challenge"
    status, data = api.post(
        "/auth/challenge",
        {"challenge_session": data["challenge_session"], "new_password": new_password},
        auth=False,
    )
    assert status == 200, data
    api.token = data["access_token"]
    acct.password = new_password


@pytest.fixture
def admin_api(runtime: Runtime) -> tuple[Api, Account]:
    acct = bootstrap_admin(runtime)
    api = Api(runtime, ip=f"198.51.100.{secrets.randbelow(250)}")
    complete_first_login(api, acct, "Admin-pass-2026")
    return api, acct


@pytest.fixture(autouse=True)
def _no_contract_violations() -> Any:
    CONTRACT_VIOLATIONS.clear()
    yield
    assert CONTRACT_VIOLATIONS == [], "\n".join(CONTRACT_VIOLATIONS)


def spec_path() -> Path:
    return contract.DEFAULT_SPEC


def create_member(api: Api, login: str, display: str = "成员乙") -> Account:
    status, data = api.post("/admin/users", {"login_name": login, "display_name": display})
    assert status == 201, data
    assert data["user"]["must_change_password"] is True
    return Account(login, data["temporary_password"], data["user"]["user_id"])


@pytest.fixture
def family(admin_api: tuple[Api, Account], runtime: Runtime) -> dict[str, Any]:
    admin, _ = admin_api
    _, fam = admin.post("/families", {"name": "测试之家"})
    fid = fam["family_id"]
    acct = create_member(admin, f"m{time.time_ns() % 10**9}")
    member = Api(runtime, ip="192.0.2.200")
    complete_first_login(member, acct, "Member-pass-10")
    status, inv = admin.post(
        f"/families/{fid}/invitations", {"login_name": acct.login, "role": "member"}
    )
    assert status == 201
    _, mine = member.get("/me/invitations")
    assert [i["invitation_id"] for i in mine["items"]] == [inv["invitation_id"]]
    assert mine["items"][0]["family_name"] == "测试之家"
    status, joined = member.post(f"/families/{fid}/invitations/{inv['invitation_id']}/accept")
    assert status == 200 and joined["role"] == "member"
    return {"fid": fid, "admin": admin, "member": member, "member_id": acct.user_id}


def new_expense(api: Api, fid: str, **kw: Any) -> dict[str, Any]:
    body = {
        "business_date": "2026-10-02",
        "type": "expense",
        "amount": "45",
        "currency": "NZD",
        "leaf_category_id": "expense-01-01",
        "payment_method": "credit_card",
        **kw,
    }
    status, data = api.post(f"/families/{fid}/entries", body)
    assert status == 201, data
    return data

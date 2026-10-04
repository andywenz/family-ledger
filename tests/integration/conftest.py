"""集成测试夹具：DynamoDB Local（docker compose up -d）。证据层级：离线。

每个测试会话使用独立表名，测试结束删除；每个测试创建独立家庭，互不干扰。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

import pytest

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.store import Store
from ledger.application import families
from ledger.application.context import AppContext
from ledger.application.users import create_user_record
from ledger.domain.authz import Actor

from ..conftest import ENDPOINT, T0, FakeClock, new_key, seed_rates  # noqa: F401


@dataclass
class World:
    ctx: AppContext
    clock: FakeClock
    admin: Actor  # 家庭管理员（也是系统管理员）
    member: Actor  # 普通成员
    outsider: Actor  # 其他家庭的管理员
    fid: str
    other_fid: str


def make_user(ctx: AppContext, name: str, login: str, *, sysadmin: bool = False) -> Actor:
    p = create_user_record(
        ctx,
        login_name=f"{login}{secrets.token_hex(3)}",
        display_name=name,
        identity_sub=f"sub-{secrets.token_hex(8)}",
        is_system_admin=sysadmin,
        must_change_password=False,
    )
    return Actor(p["user_id"], 0, is_system_admin=sysadmin)


@pytest.fixture
def world(store: Store) -> World:
    clock = FakeClock(T0)
    ctx = AppContext(store=store, clock=clock)
    admin = make_user(ctx, "管理员甲", "admin", sysadmin=True)
    member = make_user(ctx, "成员乙", "member")
    outsider = make_user(ctx, "外人", "other")
    fam = families.create_family(ctx, admin, new_key(), name="测试之家")
    other = families.create_family(ctx, outsider, new_key(), name="别人家")
    inv = families.invite(
        ctx, admin, fam["family_id"], new_key(), login_name=_login(ctx, member), role="member"
    )
    families.respond_invitation(
        ctx, member, fam["family_id"], inv["invitation_id"], new_key(), accept=True
    )
    return World(ctx, clock, admin, member, outsider, fam["family_id"], other["family_id"])


def _login(ctx: AppContext, actor: Actor) -> str:
    p = ctx.store.get(keys.user(actor.user_id), keys.PROFILE)
    assert p is not None
    return str(p["login_name"])


def raw_items(ctx: AppContext, fid: str, prefix: str) -> list[dict[str, object]]:
    """独立读回：直接查询表项，不经过应用用例。"""
    return list(ctx.store.query_all(keys.family(fid), prefix))

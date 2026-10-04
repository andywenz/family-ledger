"""读取当前身份与成员关系，并在提交事务中加入条件检查（ISE-011／012）。"""

from __future__ import annotations

from typing import Any

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.codec import membership_from
from ledger.adapters.dynamo.store import Tx
from ledger.domain.authz import Actor, Membership
from ledger.domain.errors import DomainError, Forbidden, StaleRead, Unauthenticated

from .context import AppContext


def load_profile(ctx: AppContext, actor: Actor) -> dict[str, Any]:
    p = ctx.store.get(keys.user(actor.user_id), keys.PROFILE)
    if p is None or p.get("status") != "active":
        raise Unauthenticated("账号不可用")
    if int(p.get("session_epoch", 0)) != actor.session_epoch:
        raise Unauthenticated("会话已失效，请重新登录")
    if p.get("must_change_password"):
        raise DomainError("请先修改临时密码", code="password_change_required")
    return p


def load_membership(ctx: AppContext, actor: Actor, fid: str) -> Membership | None:
    load_profile(ctx, actor)
    item = ctx.store.get(keys.family(fid), keys.member(actor.user_id))
    return membership_from(fid, item) if item else None


def _profile_fail(_: dict[str, Any] | None) -> DomainError:
    return Unauthenticated("会话已失效，请重新登录")


def guard_actor(tx: Tx, actor: Actor) -> None:
    """账号仍有效且会话边界未变化。"""
    tx.check(
        keys.user(actor.user_id),
        keys.PROFILE,
        "#s = :active AND session_epoch = :e",
        names={"#s": "status"},
        values={":active": "active", ":e": actor.session_epoch},
        on_fail=_profile_fail,
    )


def member_fail(current: dict[str, Any] | None) -> DomainError:
    if current is None or current.get("status") != "active":
        return Forbidden("无权访问该家庭")
    return StaleRead("成员关系已变化")


def guard_member(tx: Tx, actor: Actor, m: Membership, *, skip: bool = False) -> None:
    """提交时成员关系仍为读取时的版本且有效。skip=True 表示调用方已在同一项上合并条件。"""
    guard_actor(tx, actor)
    if skip:
        return
    tx.check(
        keys.family(m.family_id),
        keys.member(m.user_id),
        "#s = :active AND version = :v",
        names={"#s": "status"},
        values={":active": "active", ":v": m.version},
        on_fail=member_fail,
    )

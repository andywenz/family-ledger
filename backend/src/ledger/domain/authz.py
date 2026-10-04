"""权限判断（需求 §3.2 权限矩阵）。身份只来自可信上下文（ISE-012）。

这些函数用于读时与提交前的判断；提交事务还会对成员项做条件检查（storage-design §4）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import Forbidden


@dataclass(frozen=True)
class Actor:
    """已认证的业务操作者。由认证层根据验证后的 token 与当前账号状态构造。"""

    user_id: str
    session_epoch: int
    is_system_admin: bool = False
    profile_version: int = 1


@dataclass(frozen=True)
class Membership:
    family_id: str
    user_id: str
    role: str  # admin | member
    status: str  # active | removed
    version: int

    @property
    def active(self) -> bool:
        return self.status == "active"

    @property
    def is_admin(self) -> bool:
        return self.active and self.role == "admin"


def require_member(m: Membership | None) -> Membership:
    if m is None or not m.active:
        raise Forbidden("无权访问该家庭")
    return m


def require_family_admin(m: Membership | None) -> Membership:
    m = require_member(m)
    if not m.is_admin:
        raise Forbidden("需要家庭管理员权限")
    return m


def can_modify_entry(m: Membership, created_by: str) -> bool:
    return m.active and (m.role == "admin" or m.user_id == created_by)


def require_can_modify_entry(m: Membership | None, created_by: str) -> Membership:
    m = require_member(m)
    if not can_modify_entry(m, created_by):
        raise Forbidden("只能修改自己录入的账目")
    return m


def require_system_admin(actor: Actor) -> None:
    if not actor.is_system_admin:
        raise Forbidden("需要系统管理员权限")


def require_candidate_owner(actor: Actor, owner_user_id: str) -> None:
    """候选只能由发起人确认；家庭管理员也不例外（需求 §3.2）。"""
    if actor.user_id != owner_user_id:
        raise Forbidden("只能操作自己发起的候选")

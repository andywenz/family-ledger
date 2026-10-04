"""账号与会话用例（需求 §3.1、ADR-0012）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.codec import dt_in
from ledger.adapters.dynamo.store import Tx
from ledger.domain.authz import Actor, require_system_admin
from ledger.domain.errors import DomainError, NotFound, StaleRead, Unauthenticated, ValidationFailed
from ledger.identity.ports import (
    AuthTokens,
    Challenge,
    IdentityProvider,
    IdentityUnavailable,
    InvalidCredentials,
    InvalidPassword,
    TokenVerifier,
    check_password_policy,
    generate_temporary_password,
)

from . import actions
from .actions import Built, Scope
from .context import AppContext
from .families import list_my_families
from .guard import guard_actor, load_profile
from .users import _check_login, create_user_record, user_id_by_login

LOGIN_WINDOW_S = 900
LOGIN_LIMIT_PER_NAME = 10
LOGIN_LIMIT_PER_IP = 30
CHALLENGE_TTL_S = 300


@dataclass
class AuthDeps:
    idp: IdentityProvider
    verifier: TokenVerifier
    session_secret: bytes


# ── 限流 ──


def rate_limit(ctx: AppContext, bucket: str, limit: int, window_s: int) -> None:
    now = int(ctx.clock().timestamp())
    window = now // window_s
    try:
        ctx.store.client.update_item(
            TableName=ctx.store.table,
            Key={"PK": {"S": f"RATELIMIT#{bucket}"}, "SK": {"S": str(window)}},
            UpdateExpression="ADD hits :one SET #t = :ttl",
            ConditionExpression="attribute_not_exists(hits) OR hits < :limit",
            ExpressionAttributeNames={"#t": "ttl"},
            ExpressionAttributeValues={
                ":one": {"N": "1"},
                ":limit": {"N": str(limit)},
                ":ttl": {"N": str((window + 2) * window_s)},
            },
        )
    except ctx.store.client.exceptions.ConditionalCheckFailedException:
        raise DomainError(
            "尝试次数过多，请稍后再试",
            code="rate_limited",
            current={"retry_after": window_s - now % window_s},
        ) from None


# ── 挑战会话信封（防篡改） ──


def _seal(secret: bytes, payload: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    sig = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _open(secret: bytes, token: str, now: float) -> dict[str, Any]:
    body, _, sig = token.rpartition(".")
    expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    if not body or not hmac.compare_digest(sig, expected):
        raise Unauthenticated("改密会话无效")
    data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(body))
    if data.get("exp", 0) < now:
        raise Unauthenticated("改密会话已过期，请重新登录")
    return data


# ── 登录 ──


@dataclass(frozen=True)
class LoginOutcome:
    status: str  # ok | challenge
    tokens: AuthTokens | None = None
    challenge_session: str | None = None


def _identity_call(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except InvalidCredentials:
        raise Unauthenticated("登录名或密码错误") from None
    except InvalidPassword as e:
        raise ValidationFailed(str(e) or "密码不符合规则") from None
    except IdentityUnavailable:
        raise DomainError("身份服务繁忙，请稍后再试", code="rate_limited") from None


def login(
    ctx: AppContext, deps: AuthDeps, login_name: str, password: str, source_ip: str
) -> LoginOutcome:
    name = (login_name or "").strip().lower()
    rate_limit(ctx, f"login-name#{name}", LOGIN_LIMIT_PER_NAME, LOGIN_WINDOW_S)
    rate_limit(ctx, f"login-ip#{source_ip}", LOGIN_LIMIT_PER_IP, LOGIN_WINDOW_S)
    uid = user_id_by_login(ctx, name) if name else None
    # 不存在的登录名也调用身份服务，保持行为与耗时一致
    result = _identity_call(deps.idp.authenticate, uid or f"missing-{name}", password)
    if uid is None:
        raise Unauthenticated("登录名或密码错误")
    profile = ctx.store.get(keys.user(uid), keys.PROFILE)
    if profile is None or profile.get("status") != "active":
        raise Unauthenticated("登录名或密码错误")
    if isinstance(result, Challenge):
        sealed = _seal(
            deps.session_secret,
            {"u": uid, "s": result.session, "exp": time.time() + CHALLENGE_TTL_S},
        )
        return LoginOutcome("challenge", challenge_session=sealed)
    return LoginOutcome("ok", tokens=result)


def respond_challenge(
    ctx: AppContext, deps: AuthDeps, challenge_session: str, new_password: str
) -> LoginOutcome:
    data = _open(deps.session_secret, challenge_session, time.time())
    uid = str(data["u"])
    profile = ctx.store.get(keys.user(uid), keys.PROFILE)
    if profile is None or profile.get("status") != "active":
        raise Unauthenticated("账号不可用")
    try:
        check_password_policy(new_password, profile.get("login_name"))
    except InvalidPassword as e:
        raise ValidationFailed(str(e)) from None
    tokens = _identity_call(deps.idp.respond_new_password, uid, str(data["s"]), new_password)
    ctx.store.client.update_item(
        TableName=ctx.store.table,
        Key={"PK": {"S": keys.user(uid)}, "SK": {"S": keys.PROFILE}},
        UpdateExpression="SET must_change_password = :f, version = version + :one",
        ExpressionAttributeValues={":f": {"BOOL": False}, ":one": {"N": "1"}},
    )
    return LoginOutcome("ok", tokens=tokens)


def refresh(deps: AuthDeps, refresh_token: str) -> AuthTokens:
    result: AuthTokens = _identity_call(deps.idp.refresh, refresh_token)
    return result


def logout(deps: AuthDeps, refresh_token: str | None) -> None:
    if refresh_token:
        try:
            deps.idp.revoke(refresh_token)
        except (InvalidCredentials, IdentityUnavailable):
            pass  # 退出必须成功；令牌本身会到期


# ── 请求鉴权 ──


def authenticate(ctx: AppContext, deps: AuthDeps, access_token: str) -> Actor:
    """验证令牌 → 映射内部用户 → 检查账号状态与会话边界（ADR-0012 第 3 条）。"""
    try:
        claims = deps.verifier.verify(access_token)
    except InvalidCredentials:
        raise Unauthenticated("请重新登录") from None
    link = ctx.store.get(keys.sub(claims.sub), "SUB")
    if link is None:
        raise Unauthenticated("请重新登录")
    profile = ctx.store.get(keys.user(link["user_id"]), keys.PROFILE)
    if profile is None or profile.get("status") != "active":
        raise Unauthenticated("账号不可用")
    valid_after = dt_in(profile.get("sessions_valid_after"))
    if valid_after is not None and claims.issued_at < int(valid_after.timestamp()):
        raise Unauthenticated("会话已失效，请重新登录")
    return Actor(
        user_id=link["user_id"],
        session_epoch=int(profile.get("session_epoch", 0)),
        is_system_admin=bool(profile.get("is_system_admin", False)),
        profile_version=int(profile.get("version", 1)),
    )


# ── 自己账号 ──


def me(ctx: AppContext, actor: Actor) -> dict[str, Any]:
    p = load_profile(ctx, actor)
    binding = ctx.store.get(keys.user(actor.user_id), "FEISHU")
    feishu: dict[str, Any] = {"status": "unbound"}
    if binding:
        feishu = {
            "status": binding.get("status", "active"),
            "default_family_id": binding.get("default_family_id"),
            "version": int(binding.get("version", 1)),
        }
    return {
        "user_id": actor.user_id,
        "login_name": p["login_name"],
        "display_name": p["display_name"],
        "version": int(p["version"]),
        "is_system_admin": bool(p.get("is_system_admin", False)),
        "families": list_my_families(ctx, actor),
        "feishu": feishu,
    }


def _profile_stale(_: dict[str, Any] | None) -> DomainError:
    return StaleRead("账号信息已变化")


def update_profile(
    ctx: AppContext, actor: Actor, key: str, *, display_name: str, expected_version: int
) -> dict[str, Any]:
    if not 1 <= len(display_name.strip()) <= 24 or display_name != display_name.strip():
        raise ValidationFailed("显示名需为 1–24 个字符，且首尾无空格")

    def build() -> Built[dict[str, Any]]:
        p = load_profile(ctx, actor)
        if int(p["version"]) != expected_version:
            raise DomainError("账号信息已被修改", code="version_conflict")
        tx = ctx.store.tx()
        tx.update(
            keys.user(actor.user_id),
            keys.PROFILE,
            "SET display_name = :n, version = version + :one",
            condition="version = :v AND #s = :active AND session_epoch = :e",
            names={"#s": "status"},
            values={
                ":n": display_name,
                ":one": 1,
                ":v": expected_version,
                ":active": "active",
                ":e": actor.session_epoch,
            },
            on_fail=_profile_stale,
        )
        return Built(
            tx,
            {},
            [{"object_type": "user", "object_id": actor.user_id, "version": expected_version + 1}],
        )

    actions.run(
        ctx,
        actor,
        Scope.account(actor, key),
        key,
        "user.profile",
        {"display_name": display_name, "v": expected_version},
        build,
        lambda r: {},
    )
    return me(ctx, actor)


def change_login_name(
    ctx: AppContext, actor: Actor, key: str, *, new_login_name: str, expected_version: int
) -> dict[str, Any]:
    """单事务换登录名：新名条件写入、旧名条件删除（ADR-0012 第 2 条）。"""
    _check_login(new_login_name)
    new_lower = new_login_name.lower()

    def build() -> Built[dict[str, Any]]:
        p = load_profile(ctx, actor)
        if int(p["version"]) != expected_version:
            raise DomainError("账号信息已被修改", code="version_conflict")
        old = str(p["login_name"])
        tx = ctx.store.tx()
        if old.lower() != new_lower:
            tx.put_new(
                {
                    "PK": keys.login(new_lower),
                    "SK": "LOGIN",
                    "type": "login",
                    "user_id": actor.user_id,
                    "state": "active",
                },
                on_fail=lambda _: DomainError("登录名已被使用", code="login_name_taken"),
            )
            tx.delete(
                keys.login(old),
                "LOGIN",
                condition="user_id = :u",
                values={":u": actor.user_id},
                on_fail=_profile_stale,
            )
        tx.update(
            keys.user(actor.user_id),
            keys.PROFILE,
            "SET login_name = :n, version = version + :one",
            condition="version = :v AND #s = :active AND session_epoch = :e",
            names={"#s": "status"},
            values={
                ":n": new_login_name,
                ":one": 1,
                ":v": expected_version,
                ":active": "active",
                ":e": actor.session_epoch,
            },
            on_fail=_profile_stale,
        )
        op = {"op_id": key, "state": "done", "new_login_name": new_login_name}
        return Built(
            tx,
            op,
            [{"object_type": "user", "object_id": actor.user_id, "version": expected_version + 1}],
        )

    return actions.run(
        ctx,
        actor,
        Scope.account(actor, key),
        key,
        "user.rename",
        {"name": new_lower, "v": expected_version},
        build,
        lambda r: {"op_id": key, "state": "done", "new_login_name": new_login_name},
    )


def session_boundary(now: datetime) -> datetime:
    return now.replace(microsecond=0) + timedelta(seconds=1)


def _bump_sessions(
    tx: Tx, uid: str, now: datetime, extra_set: str = "", values: dict[str, Any] | None = None
) -> None:
    """推进会话边界：旧 access token 被拒绝、提交中的旧 Actor 条件失败。

    iat 只有秒精度：边界取下一个整秒，使本秒内签发的旧令牌也失效（ADR-0012 第 3 条）。
    """
    boundary = session_boundary(now)
    tx.update(
        keys.user(uid),
        keys.PROFILE,
        "SET session_epoch = session_epoch + :one, sessions_valid_after = :va, "
        "version = version + :one" + extra_set,
        values={":one": 1, ":va": keys.ts(boundary), **(values or {})},
    )


def change_password(
    ctx: AppContext,
    deps: AuthDeps,
    actor: Actor,
    access_token: str,
    *,
    current_password: str,
    new_password: str,
) -> AuthTokens:
    p = load_profile(ctx, actor)
    rate_limit(ctx, f"password#{actor.user_id}", LOGIN_LIMIT_PER_NAME, LOGIN_WINDOW_S)
    try:
        check_password_policy(new_password, p["login_name"])
    except InvalidPassword as e:
        raise ValidationFailed(str(e)) from None
    _identity_call(deps.idp.change_password, access_token, current_password, new_password)
    tx = ctx.store.tx()
    _bump_sessions(tx, actor.user_id, ctx.clock())
    _suspend_feishu(ctx, tx, actor.user_id)
    ctx.store.commit(tx)
    _identity_call(deps.idp.admin_sign_out, actor.user_id)
    # 等到会话边界之后再签发新令牌，否则新令牌的 iat 也会落在边界之前（最多 1 秒）
    wait = session_boundary(ctx.clock()).timestamp() - time.time()
    if 0 < wait <= 1.5:
        time.sleep(wait + 0.01)
    # 以新密码重新取得令牌，使当前浏览器继续可用；其他会话已失效
    result = _identity_call(deps.idp.authenticate, actor.user_id, new_password)
    if not isinstance(result, AuthTokens):
        raise Unauthenticated("请重新登录")
    return result


def _suspend_feishu(ctx: AppContext, tx: Tx, uid: str) -> None:
    if ctx.store.get(keys.user(uid), "FEISHU") is not None:
        tx.update(
            keys.user(uid),
            "FEISHU",
            "SET #s = :s",
            names={"#s": "status"},
            values={":s": "suspended"},
        )


# ── 系统管理员 ──


def _admin_view(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": p["user_id"],
        "login_name": p["login_name"],
        "display_name": p["display_name"],
        "status": p["status"],
        "is_system_admin": bool(p.get("is_system_admin", False)),
        "must_change_password": bool(p.get("must_change_password", False)),
        "version": int(p["version"]),
    }


def list_users(ctx: AppContext, actor: Actor) -> list[dict[str, Any]]:
    require_system_admin(actor)
    load_profile(ctx, actor)
    out = []
    for link in ctx.store.query_all("USERS", ""):  # 账号目录项，不扫描家庭数据
        p = ctx.store.get(keys.user(link["SK"]), keys.PROFILE)
        if p:
            out.append(_admin_view(p))
    return sorted(out, key=lambda u: u["login_name"].lower())


def _deterministic_uid(actor: Actor, key: str) -> str:
    """由动作 ID 派生用户 ID：身份服务已建用户但应用未提交时，同 key 重试命中同一用户。"""
    return "u" + hashlib.sha256(f"{actor.user_id}|{key}".encode()).hexdigest()[:25]


def create_user(
    ctx: AppContext,
    deps: AuthDeps,
    actor: Actor,
    key: str,
    *,
    login_name: str,
    display_name: str,
    is_system_admin: bool = False,
) -> dict[str, Any]:
    require_system_admin(actor)
    load_profile(ctx, actor)
    _check_login(login_name)
    scope = Scope.account(actor, key)
    body = {"login": login_name.lower(), "display": display_name, "sysadmin": is_system_admin}
    fp = actions.fingerprint("admin.user.create", scope, actor, body)
    existing = actions.get_receipt(ctx, scope)
    if existing is not None:
        if existing["fingerprint"] != fp:
            raise DomainError("该动作 ID 已用于不同的请求", code="idempotency_key_reused")
        p = ctx.store.get(keys.user(existing["results"][0]["object_id"]), keys.PROFILE)
        assert p is not None
        return {"user": _admin_view(p), "temporary_password_redacted": True}
    if user_id_by_login(ctx, login_name) is not None:
        raise DomainError("登录名已被使用", code="login_name_taken")
    uid = _deterministic_uid(actor, key)
    temp = generate_temporary_password()
    sub = _identity_call(deps.idp.admin_create, uid, temp)
    profile = create_user_record(
        ctx,
        login_name=login_name,
        display_name=display_name,
        identity_sub=sub,
        is_system_admin=is_system_admin,
        must_change_password=True,
        user_id=uid,
    )
    tx = ctx.store.tx()
    tx.put_new(
        {
            "PK": scope.pk,
            "SK": scope.sk,
            "type": "action",
            "action_id": key,
            "actor": actor.user_id,
            "kind": "admin.user.create",
            "fingerprint": fp,
            "status": "committed",
            "committed_at": keys.ts(ctx.clock()),
            "results": [{"object_type": "user", "object_id": uid, "version": 1}],
        }
    )
    ctx.store.commit(tx)
    return {"user": _admin_view(profile), "temporary_password": temp}


def reset_password(
    ctx: AppContext, deps: AuthDeps, actor: Actor, uid: str, key: str
) -> dict[str, Any]:
    require_system_admin(actor)
    load_profile(ctx, actor)
    scope = Scope.account(actor, key)
    fp = actions.fingerprint("admin.user.reset", scope, actor, {"uid": uid})
    existing = actions.get_receipt(ctx, scope)
    target = ctx.store.get(keys.user(uid), keys.PROFILE)
    if target is None:
        raise NotFound("账号不存在")
    if existing is not None:
        if existing["fingerprint"] != fp:
            raise DomainError("该动作 ID 已用于不同的请求", code="idempotency_key_reused")
        return {"user": _admin_view(target), "temporary_password_redacted": True}
    temp = generate_temporary_password()
    _identity_call(deps.idp.admin_set_temporary_password, uid, temp)
    tx = ctx.store.tx()
    _bump_sessions(tx, uid, ctx.clock(), ", must_change_password = :t", {":t": True})
    _suspend_feishu(ctx, tx, uid)
    tx.put_new(
        {
            "PK": scope.pk,
            "SK": scope.sk,
            "type": "action",
            "action_id": key,
            "actor": actor.user_id,
            "kind": "admin.user.reset",
            "fingerprint": fp,
            "status": "committed",
            "committed_at": keys.ts(ctx.clock()),
            "results": [{"object_type": "user", "object_id": uid}],
        }
    )
    ctx.store.commit(tx)
    _identity_call(deps.idp.admin_sign_out, uid)
    updated = ctx.store.get(keys.user(uid), keys.PROFILE)
    assert updated is not None
    return {"user": _admin_view(updated), "temporary_password": temp}


def set_user_status(
    ctx: AppContext,
    deps: AuthDeps,
    actor: Actor,
    uid: str,
    key: str,
    *,
    status: str,
    expected_version: int,
) -> dict[str, Any]:
    require_system_admin(actor)
    if status not in ("active", "disabled"):
        raise ValidationFailed("状态无效")
    if uid == actor.user_id and status == "disabled":
        raise ValidationFailed("不能停用自己的账号")

    def build() -> Built[dict[str, Any]]:
        load_profile(ctx, actor)
        target = ctx.store.get(keys.user(uid), keys.PROFILE)
        if target is None:
            raise NotFound("账号不存在")
        if int(target["version"]) != expected_version:
            raise DomainError("账号信息已被修改", code="version_conflict")
        tx = ctx.store.tx()
        guard_actor(tx, actor)
        now = session_boundary(ctx.clock())
        tx.update(
            keys.user(uid),
            keys.PROFILE,
            "SET #s = :s, session_epoch = session_epoch + :one, sessions_valid_after = :va, "
            "version = version + :one",
            condition="version = :v",
            names={"#s": "status"},
            values={":s": status, ":one": 1, ":va": keys.ts(now), ":v": expected_version},
            on_fail=_profile_stale,
        )
        view = {**_admin_view(target), "status": status, "version": expected_version + 1}
        return Built(
            tx, view, [{"object_type": "user", "object_id": uid, "version": expected_version + 1}]
        )

    out = actions.run(
        ctx,
        actor,
        Scope.account(actor, key),
        key,
        "admin.user.status",
        {"uid": uid, "status": status, "v": expected_version},
        build,
        lambda r: _admin_view(ctx.store.get(keys.user(uid), keys.PROFILE) or {}),
    )
    _identity_call(deps.idp.admin_set_enabled, uid, status == "active")
    if status == "disabled":
        _identity_call(deps.idp.admin_sign_out, uid)
    return out

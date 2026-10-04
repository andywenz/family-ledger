"""认证、自己账号、系统管理、家庭与成员路由。"""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from ledger.application import accounts, families, rates
from ledger.domain.errors import DomainError
from ledger.domain.money import CurrencyMeta
from ledger.identity.ports import AuthTokens

from .app import Request, Response, handles

REFRESH_COOKIE = "ledger_refresh"
CSRF_COOKIE = "ledger_csrf"
REFRESH_MAX_AGE = 30 * 86400


def _session_cookies(refresh_token: str | None) -> list[str]:
    out = []
    if refresh_token:
        out.append(
            f"{REFRESH_COOKIE}={refresh_token}; Max-Age={REFRESH_MAX_AGE}; Path=/v1/auth; "
            "Secure; HttpOnly; SameSite=Strict"
        )
    out.append(
        f"{CSRF_COOKIE}={secrets.token_urlsafe(24)}; Max-Age={REFRESH_MAX_AGE}; Path=/; "
        "Secure; SameSite=Strict"
    )
    return out


def _clear_cookies() -> list[str]:
    return [
        f"{REFRESH_COOKIE}=; Max-Age=0; Path=/v1/auth; Secure; HttpOnly; SameSite=Strict",
        f"{CSRF_COOKIE}=; Max-Age=0; Path=/; Secure; SameSite=Strict",
    ]


def _token_body(t: AuthTokens) -> dict[str, Any]:
    return {"status": "ok", "access_token": t.access_token, "expires_in": t.expires_in}


def check_csrf(req: Request) -> None:
    """双提交 cookie＋Origin 允许列表（ADR-0012 第 4 条）。"""
    origin = req.headers.get("origin", "")
    allowed = req.runtime.settings.allowed_origins
    cookie = req.cookies.get(CSRF_COOKIE, "")
    header = req.headers.get("x-csrf-token", "")
    if (allowed and origin not in allowed) or not cookie or not hmac.compare_digest(cookie, header):
        raise DomainError("请求来源校验失败", code="csrf_failed")


@handles("getHealth")
def health(req: Request) -> Response:
    s = req.runtime.settings
    return Response(
        200,
        {
            "status": "ok",
            "version": s.version,
            "commit": s.commit,
            "artifact_digest": s.artifact_digest,
            "env": s.env if s.env in ("local", "staging", "prod") else "prod",
        },
    )


@handles("login")
def login(req: Request) -> Response:
    out = accounts.login(
        req.ctx, req.runtime.auth, req.body["login_name"], req.body["password"], req.source_ip
    )
    if out.status == "challenge":
        return Response(
            200,
            {
                "status": "challenge",
                "challenge": "new_password_required",
                "challenge_session": out.challenge_session,
            },
        )
    assert out.tokens is not None
    return Response(
        200, _token_body(out.tokens), cookies=_session_cookies(out.tokens.refresh_token)
    )


@handles("respondChallenge")
def respond_challenge(req: Request) -> Response:
    out = accounts.respond_challenge(
        req.ctx, req.runtime.auth, req.body["challenge_session"], req.body["new_password"]
    )
    assert out.tokens is not None
    return Response(
        200, _token_body(out.tokens), cookies=_session_cookies(out.tokens.refresh_token)
    )


@handles("refresh")
def refresh(req: Request) -> Response:
    check_csrf(req)
    token = req.cookies.get(REFRESH_COOKIE)
    if not token:
        from ledger.domain.errors import Unauthenticated

        raise Unauthenticated("请重新登录")
    t = accounts.refresh(req.runtime.auth, token)
    # 刷新后的 access token 仍需通过会话边界检查
    accounts.authenticate(req.ctx, req.runtime.auth, t.access_token)
    return Response(200, _token_body(t))


@handles("logout")
def logout(req: Request) -> Response:
    check_csrf(req)
    accounts.logout(req.runtime.auth, req.cookies.get(REFRESH_COOKIE))
    return Response(204, cookies=_clear_cookies())


@handles("getMe")
def get_me(req: Request) -> Response:
    return Response(200, accounts.me(req.ctx, req.actor))


@handles("updateMyProfile")
def update_profile(req: Request) -> Response:
    return Response(
        200,
        accounts.update_profile(
            req.ctx,
            req.actor,
            req.key,
            display_name=req.body["display_name"],
            expected_version=req.body["expected_version"],
        ),
    )


@handles("changeLoginName")
def change_login_name(req: Request) -> Response:
    return Response(
        200,
        accounts.change_login_name(
            req.ctx,
            req.actor,
            req.key,
            new_login_name=req.body["new_login_name"],
            expected_version=req.body["expected_version"],
        ),
    )


@handles("getRenameOperation")
def get_rename(req: Request) -> Response:
    from ledger.application import actions

    r = actions.get_receipt(req.ctx, actions.Scope.account(req.actor, req.path["op_id"]))
    if r is None or r.get("kind") != "user.rename":
        from ledger.domain.errors import NotFound

        raise NotFound("操作不存在")
    p = accounts.me(req.ctx, req.actor)
    return Response(
        200, {"op_id": req.path["op_id"], "state": "done", "new_login_name": p["login_name"]}
    )


@handles("changeMyPassword")
def change_password(req: Request) -> Response:
    t = accounts.change_password(
        req.ctx,
        req.runtime.auth,
        req.actor,
        req.bearer,
        current_password=req.body["current_password"],
        new_password=req.body["new_password"],
    )
    return Response(204, cookies=_session_cookies(t.refresh_token))


@handles("listMyInvitations")
def my_invitations(req: Request) -> Response:
    return Response(200, {"items": families.list_my_invitations(req.ctx, req.actor)})


@handles("getMyAction")
def my_action(req: Request) -> Response:
    from ledger.application import actions

    r = actions.get_receipt(req.ctx, actions.Scope.account(req.actor, req.path["action_id"]))
    if r is None:
        raise DomainError("该动作尚未提交", code="action_not_found")
    return Response(
        200,
        {
            "action_id": r["action_id"],
            "kind": r["kind"],
            "status": r["status"],
            "committed_at": r["committed_at"],
            "results": r.get("results", []),
        },
    )


# ── 系统管理 ──


@handles("listUsers")
def list_users(req: Request) -> Response:
    return Response(200, {"items": accounts.list_users(req.ctx, req.actor)})


@handles("createUser")
def create_user(req: Request) -> Response:
    return Response(
        201,
        accounts.create_user(
            req.ctx,
            req.runtime.auth,
            req.actor,
            req.key,
            login_name=req.body["login_name"],
            display_name=req.body["display_name"],
            is_system_admin=req.body.get("is_system_admin", False),
        ),
    )


@handles("resetUserPassword")
def reset_password(req: Request) -> Response:
    return Response(
        200, accounts.reset_password(req.ctx, req.runtime.auth, req.actor, req.path["uid"], req.key)
    )


@handles("updateUserStatus")
def user_status(req: Request) -> Response:
    return Response(
        200,
        accounts.set_user_status(
            req.ctx,
            req.runtime.auth,
            req.actor,
            req.path["uid"],
            req.key,
            status=req.body["status"],
            expected_version=req.body["expected_version"],
        ),
    )


@handles("createGlobalCurrency")
def create_currency(req: Request) -> Response:
    b = req.body
    meta = CurrencyMeta(b["code"], b["minor_digits"], b["name_zh"], b["provider_supported"])
    return Response(201, rates.create_global_currency(req.ctx, req.actor, req.key, meta))


# ── 家庭与成员 ──


@handles("listFamilies")
def list_families(req: Request) -> Response:
    return Response(200, {"items": families.list_my_families(req.ctx, req.actor)})


@handles("createFamily")
def create_family(req: Request) -> Response:
    b = req.body
    kw = {k: b[k] for k in ("timezone", "default_currency", "default_payment_method") if k in b}
    return Response(201, families.create_family(req.ctx, req.actor, req.key, name=b["name"], **kw))


@handles("getFamily")
def get_family(req: Request) -> Response:
    return Response(200, families.get_family(req.ctx, req.actor, req.path["fid"]))


@handles("renameFamily")
def rename_family(req: Request) -> Response:
    return Response(
        200,
        families.rename_family(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.key,
            name=req.body["name"],
            expected_version=req.body["expected_version"],
        ),
    )


@handles("getFamilyConfig")
def get_config(req: Request) -> Response:
    return Response(200, families.get_config(req.ctx, req.actor, req.path["fid"]))


@handles("updateFamilyConfig")
def update_config(req: Request) -> Response:
    b = dict(req.body)
    v = b.pop("expected_version")
    return Response(
        200,
        families.update_config(
            req.ctx, req.actor, req.path["fid"], req.key, expected_version=v, **b
        ),
    )


@handles("listMembers")
def list_members(req: Request) -> Response:
    return Response(200, {"items": families.list_members(req.ctx, req.actor, req.path["fid"])})


@handles("changeMemberRole")
def change_role(req: Request) -> Response:
    return Response(
        200,
        families.change_role(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.path["uid"],
            req.key,
            role=req.body["role"],
            expected_version=req.body["expected_version"],
        ),
    )


@handles("removeMember")
def remove_member(req: Request) -> Response:
    families.remove_member(
        req.ctx,
        req.actor,
        req.path["fid"],
        req.path["uid"],
        req.key,
        expected_version=req.query["expected_version"],
    )
    return Response(204)


@handles("listFamilyInvitations")
def family_invitations(req: Request) -> Response:
    return Response(
        200, {"items": families.list_family_invitations(req.ctx, req.actor, req.path["fid"])}
    )


@handles("inviteMember")
def invite(req: Request) -> Response:
    return Response(
        201,
        families.invite(
            req.ctx,
            req.actor,
            req.path["fid"],
            req.key,
            login_name=req.body["login_name"],
            role=req.body["role"],
        ),
    )


@handles("revokeInvitation")
def revoke(req: Request) -> Response:
    families.revoke_invitation(req.ctx, req.actor, req.path["fid"], req.path["iid"], req.key)
    return Response(204)


@handles("acceptInvitation")
def accept(req: Request) -> Response:
    out = families.respond_invitation(
        req.ctx, req.actor, req.path["fid"], req.path["iid"], req.key, accept=True
    )
    return Response(200, out)


@handles("declineInvitation")
def decline(req: Request) -> Response:
    families.respond_invitation(
        req.ctx, req.actor, req.path["fid"], req.path["iid"], req.key, accept=False
    )
    return Response(204)

"""家庭、成员与邀请（需求 §3.2，AUTH-05）。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.codec import category_item, dt_in, membership_from
from ledger.domain.authz import Actor, Membership, require_family_admin, require_member
from ledger.domain.categories import load_template
from ledger.domain.entries import PAYMENT_METHODS
from ledger.domain.errors import DomainError, Forbidden, NotFound, StaleRead, ValidationFailed

from . import actions, secondary
from .actions import Built, Scope
from .context import AppContext
from .guard import guard_actor, guard_member, load_membership, load_profile, member_fail
from .repo import global_currencies
from .users import user_id_by_login

INITIAL_CURRENCIES = ("NZD", "CNY", "USD", "AUD", "EUR")
INVITE_DAYS = 7


def _audit(
    ctx: AppContext, fid: str, actor: Actor, action: str, obj: str, **extra: Any
) -> dict[str, Any]:
    now = ctx.clock()
    return {
        "PK": keys.family(fid),
        "SK": keys.audit(now, ctx.ids()),
        "type": "audit",
        "actor": actor.user_id,
        "action": action,
        "object_id": obj,
        "at": keys.ts(now),
        "ttl": int((now + timedelta(days=180)).timestamp()),
        **extra,
    }


def _check_tz(tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationFailed("时区无效") from None


def family_view(meta: dict[str, Any], role: str, member_count: int) -> dict[str, Any]:
    return {
        "family_id": meta["family_id"],
        "name": meta["name"],
        "timezone": meta["timezone"],
        "version": int(meta["version"]),
        "member_count": member_count,
        "my_role": role,
    }


def _member_count(ctx: AppContext, fid: str) -> int:
    return sum(
        1 for m in ctx.store.query_all(keys.family(fid), "MEMBER#") if m["status"] == "active"
    )


# ── 家庭 ──


def create_family(
    ctx: AppContext,
    actor: Actor,
    key: str,
    *,
    name: str,
    timezone: str = "Pacific/Auckland",
    default_currency: str = "NZD",
    default_payment_method: str = "credit_card",
) -> dict[str, Any]:
    if not 1 <= len(name) <= 40:
        raise ValidationFailed("家庭名称需为 1–40 个字符")
    _check_tz(timezone)
    if default_currency not in INITIAL_CURRENCIES:
        raise ValidationFailed("默认币种必须是已启用币种")
    if default_payment_method not in PAYMENT_METHODS:
        raise ValidationFailed("默认消费方式无效")
    body = {
        "name": name,
        "timezone": timezone,
        "default_currency": default_currency,
        "default_payment_method": default_payment_method,
    }

    def build() -> Built[dict[str, Any]]:
        load_profile(ctx, actor)
        meta_currencies = global_currencies(ctx)
        missing = [c for c in INITIAL_CURRENCIES if c not in meta_currencies]
        if missing:
            raise DomainError(f"全局币种未初始化：{missing}", code="internal")
        fid = ctx.ids()
        now = keys.ts(ctx.clock())
        pk = keys.family(fid)
        tx = ctx.store.tx()
        guard_actor(tx, actor)
        meta = {
            "PK": pk,
            "SK": keys.META,
            "type": "family",
            "family_id": fid,
            "name": name,
            "timezone": timezone,
            "admin_count": 1,
            "version": 1,
            "created_at": now,
        }
        tx.put_new(meta)
        tx.put_new(
            {
                "PK": pk,
                "SK": keys.CONFIG,
                "type": "config",
                "default_currency": default_currency,
                "default_payment_method": default_payment_method,
                "timezone": timezone,
                "category_manifest_version": 1,
                "version": 1,
            }
        )
        tx.put_new(
            {
                "PK": pk,
                "SK": keys.member(actor.user_id),
                "type": "member",
                "user_id": actor.user_id,
                "role": "admin",
                "status": "active",
                "version": 1,
                "joined_at": now,
            }
        )
        tx.put_new(
            {
                "PK": keys.user(actor.user_id),
                "SK": keys.user_family(fid),
                "type": "user_family",
                "family_id": fid,
                "role": "admin",
                "status": "active",
            }
        )
        for c in load_template():
            tx.put_new(category_item(fid, c))
        for code in INITIAL_CURRENCIES:
            tx.put_new(
                {
                    "PK": pk,
                    "SK": keys.family_currency(code),
                    "type": "family_currency",
                    "code": code,
                    "enabled_at": now,
                    "enabled_by": actor.user_id,
                }
            )
        tx.put_new(_audit(ctx, fid, actor, "family.create", fid))
        return Built(
            tx,
            family_view(meta, "admin", 1),
            [{"object_type": "family", "object_id": fid, "version": 1}],
        )

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        return get_family(ctx, actor, r["results"][0]["object_id"])

    return actions.run(
        ctx, actor, Scope.account(actor, key), key, "family.create", body, build, replay
    )


def get_family(ctx: AppContext, actor: Actor, fid: str) -> dict[str, Any]:
    m = require_member(load_membership(ctx, actor, fid))
    meta = ctx.store.get(keys.family(fid), keys.META)
    if meta is None:
        raise NotFound("家庭不存在")
    return family_view(meta, m.role, _member_count(ctx, fid))


def list_my_families(ctx: AppContext, actor: Actor) -> list[dict[str, Any]]:
    load_profile(ctx, actor)
    out = []
    for link in ctx.store.query_all(keys.user(actor.user_id), "FAM#"):
        if link.get("status") != "active":
            continue
        meta = ctx.store.get(keys.family(link["family_id"]), keys.META)
        if meta:
            out.append({"family_id": link["family_id"], "name": meta["name"], "role": link["role"]})
    return out


def rename_family(
    ctx: AppContext, actor: Actor, fid: str, key: str, *, name: str, expected_version: int
) -> dict[str, Any]:
    if not 1 <= len(name) <= 40:
        raise ValidationFailed("家庭名称需为 1–40 个字符")

    def build() -> Built[dict[str, Any]]:
        m = require_family_admin(load_membership(ctx, actor, fid))
        meta = ctx.store.get(keys.family(fid), keys.META)
        assert meta is not None
        if int(meta["version"]) != expected_version:
            raise DomainError(
                "家庭信息已被修改",
                code="version_conflict",
                current=family_view(meta, m.role, _member_count(ctx, fid)),
            )
        tx = ctx.store.tx()
        guard_member(tx, actor, m)
        tx.update(
            keys.family(fid),
            keys.META,
            "SET #n = :n, version = version + :one",
            condition="version = :v",
            names={"#n": "name"},
            values={":n": name, ":one": 1, ":v": expected_version},
            on_fail=lambda _: StaleRead("家庭信息已变化"),
        )
        tx.put_new(_audit(ctx, fid, actor, "family.rename", fid))
        meta2 = {**meta, "name": name, "version": expected_version + 1}
        return Built(
            tx,
            family_view(meta2, m.role, _member_count(ctx, fid)),
            [{"object_type": "family", "object_id": fid, "version": expected_version + 1}],
        )

    return actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "family.rename",
        {"name": name, "v": expected_version},
        build,
        lambda r: get_family(ctx, actor, fid),
    )


def config_view(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "default_currency": cfg["default_currency"],
        "default_payment_method": cfg["default_payment_method"],
        "timezone": cfg["timezone"],
        "category_manifest_version": int(cfg["category_manifest_version"]),
        "version": int(cfg["version"]),
        "secondary_currency": secondary.configured(cfg),
    }


def get_config(ctx: AppContext, actor: Actor, fid: str) -> dict[str, Any]:
    require_member(load_membership(ctx, actor, fid))
    cfg = ctx.store.get(keys.family(fid), keys.CONFIG)
    assert cfg is not None
    return config_view(cfg)


def update_config(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    key: str,
    *,
    expected_version: int,
    default_currency: str | None = None,
    default_payment_method: str | None = None,
    timezone: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    # secondary_currency：键存在才修改；值为 None 表示“不显示辅助币种”
    set_secondary = "secondary_currency" in extra
    secondary_currency: str | None = extra.pop("secondary_currency", None)
    if extra:
        raise ValidationFailed("未知的设置项")
    if timezone is not None:
        _check_tz(timezone)
    if default_payment_method is not None and default_payment_method not in PAYMENT_METHODS:
        raise ValidationFailed("默认消费方式无效")
    body = {
        "c": default_currency,
        "p": default_payment_method,
        "t": timezone,
        "s": [set_secondary, secondary_currency],
        "v": expected_version,
    }

    def build() -> Built[dict[str, Any]]:
        m = require_family_admin(load_membership(ctx, actor, fid))
        cfg = ctx.store.get(keys.family(fid), keys.CONFIG)
        assert cfg is not None
        if int(cfg["version"]) != expected_version:
            raise DomainError("设置已被修改", code="version_conflict", current=config_view(cfg))
        if (
            default_currency is not None
            and ctx.store.get(keys.family(fid), keys.family_currency(default_currency)) is None
        ):
            raise ValidationFailed("默认币种必须是本家庭已启用币种")
        if set_secondary and secondary_currency is not None:
            if ctx.store.get(keys.family(fid), keys.family_currency(secondary_currency)) is None:
                raise ValidationFailed("辅助币种必须是本家庭已启用币种")
        # 只拒绝“明确把辅助币种设成与默认币种相同”；仅改默认币种时，二者相同则辅助币种暂不显示
        final_default = default_currency or cfg["default_currency"]
        if set_secondary and secondary_currency and secondary_currency == final_default:
            raise ValidationFailed("辅助币种不能与默认币种相同")
        new = {**cfg, "version": expected_version + 1}
        if set_secondary:
            new["secondary_currency"] = secondary_currency or ""  # 空字符串＝不显示
        for k, v in (
            ("default_currency", default_currency),
            ("default_payment_method", default_payment_method),
            ("timezone", timezone),
        ):
            if v is not None:
                new[k] = v
        tx = ctx.store.tx()
        guard_member(tx, actor, m)
        tx.put(
            new,
            condition="version = :v",
            values={":v": expected_version},
            on_fail=lambda _: StaleRead("设置已变化"),
        )
        if timezone is not None:
            # timezone 是 DynamoDB 保留字，须用属性名占位
            tx.update(
                keys.family(fid),
                keys.META,
                "SET #tz = :t",
                names={"#tz": "timezone"},
                values={":t": timezone},
            )
        tx.put_new(_audit(ctx, fid, actor, "family.config", fid))
        return Built(
            tx,
            config_view(new),
            [{"object_type": "config", "object_id": fid, "version": new["version"]}],
        )

    return actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "family.config",
        body,
        build,
        lambda r: get_config(ctx, actor, fid),
    )


# ── 成员 ──


def list_members(ctx: AppContext, actor: Actor, fid: str) -> list[dict[str, Any]]:
    require_member(load_membership(ctx, actor, fid))
    out = []
    for m in ctx.store.query_all(keys.family(fid), "MEMBER#"):
        p = ctx.store.get(keys.user(m["user_id"]), keys.PROFILE) or {}
        row = {
            "user_id": m["user_id"],
            "display_name": p.get("display_name", ""),
            "role": m["role"],
            "status": m["status"],
            "version": int(m["version"]),
        }
        if m["status"] == "active":
            row["login_name"] = p.get("login_name", "")
        out.append(row)
    return out


def _target(ctx: AppContext, fid: str, uid: str) -> Membership:
    item = ctx.store.get(keys.family(fid), keys.member(uid))
    if item is None or item["status"] != "active":
        raise NotFound("成员不存在")
    return membership_from(fid, item)


def _last_admin() -> DomainError:
    return DomainError("家庭至少需要保留一名管理员", code="last_admin")


def change_role(
    ctx: AppContext, actor: Actor, fid: str, uid: str, key: str, *, role: str, expected_version: int
) -> dict[str, Any]:
    if role not in ("admin", "member"):
        raise ValidationFailed("角色无效")

    def build() -> Built[dict[str, Any]]:
        me = require_family_admin(load_membership(ctx, actor, fid))
        t = _target(ctx, fid, uid)
        if t.version != expected_version:
            raise DomainError("成员信息已变化", code="version_conflict")
        tx = ctx.store.tx()
        self_change = uid == actor.user_id
        guard_member(tx, actor, me, skip=self_change)
        new_version = t.version + 1
        tx.update(
            keys.family(fid),
            keys.member(uid),
            "SET #r = :r, version = :nv",
            condition="#s = :active AND version = :v",
            names={"#r": "role", "#s": "status"},
            values={":r": role, ":nv": new_version, ":active": "active", ":v": t.version},
            on_fail=member_fail if self_change else (lambda _: StaleRead("成员信息已变化")),
        )
        tx.update(
            keys.user(uid),
            keys.user_family(fid),
            "SET #r = :r",
            names={"#r": "role"},
            values={":r": role},
        )
        if t.role == "admin" and role == "member":
            tx.update(
                keys.family(fid),
                keys.META,
                "SET admin_count = admin_count - :one",
                condition="admin_count > :one",
                values={":one": 1},
                on_fail=lambda _: _last_admin(),
            )
        elif t.role == "member" and role == "admin":
            tx.update(
                keys.family(fid),
                keys.META,
                "SET admin_count = admin_count + :one",
                values={":one": 1},
            )
        tx.put_new(_audit(ctx, fid, actor, "member.role", uid, role=role))
        prof = ctx.store.get(keys.user(uid), keys.PROFILE) or {}
        view = {
            "user_id": uid,
            "display_name": prof.get("display_name", ""),
            "role": role,
            "status": "active",
            "version": new_version,
        }
        return Built(
            tx, view, [{"object_type": "member", "object_id": uid, "version": new_version}]
        )

    return actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "member.role",
        {"uid": uid, "role": role, "v": expected_version},
        build,
        lambda r: next(m for m in list_members(ctx, actor, fid) if m["user_id"] == uid),
    )


def remove_member(
    ctx: AppContext, actor: Actor, fid: str, uid: str, key: str, *, expected_version: int
) -> None:
    def build() -> Built[None]:
        me = require_family_admin(load_membership(ctx, actor, fid))
        t = _target(ctx, fid, uid)
        if t.version != expected_version:
            raise DomainError("成员信息已变化", code="version_conflict")
        tx = ctx.store.tx()
        self_change = uid == actor.user_id
        guard_member(tx, actor, me, skip=self_change)
        tx.update(
            keys.family(fid),
            keys.member(uid),
            "SET #s = :removed, version = :nv, removed_at = :at",
            condition="#s = :active AND version = :v",
            names={"#s": "status"},
            values={
                ":removed": "removed",
                ":nv": t.version + 1,
                ":at": keys.ts(ctx.clock()),
                ":active": "active",
                ":v": t.version,
            },
            on_fail=member_fail if self_change else (lambda _: StaleRead("成员信息已变化")),
        )
        tx.update(
            keys.user(uid),
            keys.user_family(fid),
            "SET #s = :removed",
            names={"#s": "status"},
            values={":removed": "removed"},
        )
        if t.role == "admin":
            tx.update(
                keys.family(fid),
                keys.META,
                "SET admin_count = admin_count - :one",
                condition="admin_count > :one",
                values={":one": 1},
                on_fail=lambda _: _last_admin(),
            )
        tx.put_new(_audit(ctx, fid, actor, "member.remove", uid))
        return Built(
            tx, None, [{"object_type": "member", "object_id": uid, "version": t.version + 1}]
        )

    actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "member.remove",
        {"uid": uid, "v": expected_version},
        build,
        lambda r: None,
    )


# ── 邀请 ──


def _invite_view(item: dict[str, Any], family_name: str = "") -> dict[str, Any]:
    return {
        "invitation_id": item["invitation_id"],
        "family_id": item["family_id"],
        "family_name": family_name,
        "role": item["role"],
        "status": item["status"],
        "expires_at": item["expires_at"],
    }


def _generic_invite_error() -> ValidationFailed:
    # 不存在的登录名与已是成员返回同一错误，避免枚举账号
    return ValidationFailed("无法邀请该登录名")


def invite(
    ctx: AppContext, actor: Actor, fid: str, key: str, *, login_name: str, role: str
) -> dict[str, Any]:
    if role not in ("admin", "member"):
        raise ValidationFailed("角色无效")

    def build() -> Built[dict[str, Any]]:
        me = require_family_admin(load_membership(ctx, actor, fid))
        uid = user_id_by_login(ctx, login_name)
        if uid is None:
            raise _generic_invite_error()
        existing = ctx.store.get(keys.family(fid), keys.member(uid))
        if existing is not None and existing["status"] == "active":
            raise _generic_invite_error()
        iid = ctx.ids()
        now = ctx.clock()
        item = {
            "PK": keys.family(fid),
            "SK": keys.invite(iid),
            "type": "invitation",
            "invitation_id": iid,
            "family_id": fid,
            "invitee_uid": uid,
            "role": role,
            "invited_by": actor.user_id,
            "status": "pending",
            "expires_at": keys.ts(now + timedelta(days=INVITE_DAYS)),
            "created_at": keys.ts(now),
        }
        tx = ctx.store.tx()
        guard_member(tx, actor, me)
        tx.put_new(item)
        tx.put_new(
            {
                "PK": keys.user(uid),
                "SK": keys.user_invite(fid, iid),
                "type": "user_invite",
                "family_id": fid,
                "invitation_id": iid,
                "status": "pending",
                "expires_at": item["expires_at"],
            }
        )
        tx.put_new(_audit(ctx, fid, actor, "invite.create", iid))
        meta = ctx.store.get(keys.family(fid), keys.META) or {}
        return Built(
            tx,
            _invite_view(item, meta.get("name", "")),
            [{"object_type": "invitation", "object_id": iid, "version": 1}],
        )

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        item = ctx.store.get(keys.family(fid), keys.invite(r["results"][0]["object_id"]))
        assert item is not None
        return _invite_view(item)

    return actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "invite.create",
        {"login": login_name.lower(), "role": role},
        build,
        replay,
    )


def list_my_invitations(ctx: AppContext, actor: Actor) -> list[dict[str, Any]]:
    load_profile(ctx, actor)
    now = ctx.clock()
    out = []
    for link in ctx.store.query_all(keys.user(actor.user_id), "INVITE#"):
        item = ctx.store.get(keys.family(link["family_id"]), keys.invite(link["invitation_id"]))
        if item is None or item["status"] != "pending":
            continue
        expires = dt_in(item["expires_at"])
        assert expires is not None
        if expires <= now:
            continue
        meta = ctx.store.get(keys.family(link["family_id"]), keys.META) or {}
        out.append(_invite_view(item, meta.get("name", "")))
    return out


def respond_invitation(
    ctx: AppContext, actor: Actor, fid: str, iid: str, key: str, *, accept: bool
) -> dict[str, Any] | None:
    kind = "invite.accept" if accept else "invite.decline"

    def build() -> Built[dict[str, Any] | None]:
        load_profile(ctx, actor)
        item = ctx.store.get(keys.family(fid), keys.invite(iid))
        if item is None or item["invitee_uid"] != actor.user_id:
            raise Forbidden("无权处理该邀请")
        now = ctx.clock()
        expires = dt_in(item["expires_at"])
        assert expires is not None
        if item["status"] != "pending" or expires <= now:
            raise DomainError("邀请已失效", code="invitation_expired")
        status = "accepted" if accept else "declined"
        tx = ctx.store.tx()
        guard_actor(tx, actor)
        tx.update(
            keys.family(fid),
            keys.invite(iid),
            "SET #s = :st",
            condition="#s = :pending AND expires_at > :now",
            names={"#s": "status"},
            values={":st": status, ":pending": "pending", ":now": keys.ts(now)},
            on_fail=lambda _: DomainError("邀请已失效", code="invitation_expired"),
        )
        tx.update(
            keys.user(actor.user_id),
            keys.user_invite(fid, iid),
            "SET #s = :st",
            names={"#s": "status"},
            values={":st": status},
        )
        view: dict[str, Any] | None = None
        if accept:
            existing = ctx.store.get(keys.family(fid), keys.member(actor.user_id))
            version = int(existing["version"]) + 1 if existing else 1
            tx.put(
                {
                    "PK": keys.family(fid),
                    "SK": keys.member(actor.user_id),
                    "type": "member",
                    "user_id": actor.user_id,
                    "role": item["role"],
                    "status": "active",
                    "version": version,
                    "joined_at": keys.ts(now),
                },
                condition="attribute_not_exists(PK) OR #s = :removed",
                names={"#s": "status"},
                values={":removed": "removed"},
                on_fail=lambda _: ValidationFailed("已是家庭成员"),
            )
            tx.put(
                {
                    "PK": keys.user(actor.user_id),
                    "SK": keys.user_family(fid),
                    "type": "user_family",
                    "family_id": fid,
                    "role": item["role"],
                    "status": "active",
                }
            )
            if item["role"] == "admin":
                tx.update(
                    keys.family(fid),
                    keys.META,
                    "SET admin_count = admin_count + :one",
                    values={":one": 1},
                )
            meta = ctx.store.get(keys.family(fid), keys.META) or {}
            view = {"family_id": fid, "name": meta.get("name", ""), "role": item["role"]}
        tx.put_new(_audit(ctx, fid, actor, kind, iid))
        return Built(tx, view, [{"object_type": "invitation", "object_id": iid, "version": 2}])

    def replay(r: dict[str, Any]) -> dict[str, Any] | None:
        if not accept:
            return None
        return next((f for f in list_my_families(ctx, actor) if f["family_id"] == fid), None)

    return actions.run(
        ctx, actor, Scope.account(actor, key), key, kind, {"fid": fid, "iid": iid}, build, replay
    )


def revoke_invitation(ctx: AppContext, actor: Actor, fid: str, iid: str, key: str) -> None:
    def build() -> Built[None]:
        me = require_family_admin(load_membership(ctx, actor, fid))
        item = ctx.store.get(keys.family(fid), keys.invite(iid))
        if item is None:
            raise NotFound("邀请不存在")
        tx = ctx.store.tx()
        guard_member(tx, actor, me)
        tx.update(
            keys.family(fid),
            keys.invite(iid),
            "SET #s = :r",
            condition="#s = :p",
            names={"#s": "status"},
            values={":r": "revoked", ":p": "pending"},
            on_fail=lambda _: DomainError("邀请已失效", code="invitation_expired"),
        )
        tx.update(
            keys.user(item["invitee_uid"]),
            keys.user_invite(fid, iid),
            "SET #s = :r",
            names={"#s": "status"},
            values={":r": "revoked"},
        )
        tx.put_new(_audit(ctx, fid, actor, "invite.revoke", iid))
        return Built(tx, None, [{"object_type": "invitation", "object_id": iid, "version": 2}])

    actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "invite.revoke",
        {"iid": iid},
        build,
        lambda r: None,
    )


def list_family_invitations(ctx: AppContext, actor: Actor, fid: str) -> list[dict[str, Any]]:
    require_family_admin(load_membership(ctx, actor, fid))
    return [_invite_view(i) for i in ctx.store.query_all(keys.family(fid), "INVITE#")]

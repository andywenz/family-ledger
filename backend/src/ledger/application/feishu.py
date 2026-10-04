"""飞书私聊记账（需求 §6.3；ADR-0013；FS-01～07、AUTH-08／10）。

身份只来自平台可信字段（open_id＋tenant_key）经绑定映射；卡片 value 只用于定位对象。
所有对飞书的发送都经 outbox，入账状态与交付状态分开记录。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta
from typing import Any

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.codec import dt_in
from ledger.adapters.dynamo.store import Tx
from ledger.adapters.feishu import FeishuApi, FeishuDeliveryUnknown, FeishuSecrets, open_envelope
from ledger.domain.authz import Actor, require_member
from ledger.domain.errors import DomainError, StaleRead, ValidationFailed

from . import actions, candidates, recognition, repo
from .actions import Built, Scope
from .context import AppContext
from .guard import guard_actor, load_membership, load_profile

CODE_TTL_S = 600
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
EVENT_TTL_DAYS = 30
FSMSG_TTL_DAYS = 7
DELIVERY_MAX_ATTEMPTS = 5
UUID_WINDOW = timedelta(minutes=50)  # 平台 uuid 去重窗口 1 小时，留 10 分钟余量
BIND_RE = re.compile(r"^\s*绑定\s*([A-Za-z0-9]{8})\s*$")
TYPE_ZH = {
    "expense": "支出",
    "income": "收入",
    "internal_transfer": "内部转账",
    "exchange": "换汇",
    "card_repayment": "信用卡还款",
    "receivable": "往来款",
}
METHOD_ZH = {"credit_card": "信用卡", "debit_card": "借记卡", "cash": "现金"}
SOURCE_TAG = {"family_default": "默认值", "request_date": "接收日", "inferred": "请核对"}


def _fsid(tenant: str, open_id: str) -> str:
    return f"FSID#{tenant}#{open_id}"


def _code_hash(code: str) -> str:
    return f"BINDCODE#{hashlib.sha256(code.upper().encode()).hexdigest()}"


# ── 绑定（网站侧） ──


def create_binding_code(
    ctx: AppContext, actor: Actor, key: str, *, default_family_id: str, secret: bytes
) -> dict[str, Any]:
    """一次性绑定码：由服务端密钥与动作 ID 派生（重放返回同一码），库中只存哈希（FS-01）。"""
    require_member(load_membership(ctx, actor, default_family_id))
    digest = hmac.new(secret, f"bind|{actor.user_id}|{key}".encode(), hashlib.sha256).digest()
    code = "".join(CODE_ALPHABET[b % len(CODE_ALPHABET)] for b in digest[:8])
    now = ctx.clock()
    expires = now + timedelta(seconds=CODE_TTL_S)

    def build() -> Built[dict[str, Any]]:
        load_profile(ctx, actor)
        tx = ctx.store.tx()
        guard_actor(tx, actor)
        tx.put_new(
            {
                "PK": _code_hash(code),
                "SK": "CODE",
                "type": "bind_code",
                "user_id": actor.user_id,
                "family_id": default_family_id,
                "used": False,
                "expires_at": keys.ts(expires),
                "ttl": int((expires + timedelta(days=1)).timestamp()),
            }
        )
        return Built(
            tx,
            {"code": code, "expires_at": keys.ts(expires)},
            [{"object_type": "bind_code", "object_id": _code_hash(code)[9:25]}],
        )

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        item = ctx.store.get(_code_hash(code), "CODE") or {}
        return {"code": code, "expires_at": item.get("expires_at", keys.ts(expires))}

    return actions.run(
        ctx,
        actor,
        Scope.account(actor, key),
        key,
        "feishu.code",
        {"fid": default_family_id},
        build,
        replay,
    )


def binding_view(item: dict[str, Any] | None) -> dict[str, Any]:
    if item is None:
        return {"status": "unbound"}
    return {
        "status": item.get("status", "active"),
        "default_family_id": item["default_family_id"],
        "bound_at": item.get("bound_at"),
        "version": int(item.get("version", 1)),
    }


def update_binding(
    ctx: AppContext, actor: Actor, key: str, *, default_family_id: str, expected_version: int
) -> dict[str, Any]:
    def build() -> Built[dict[str, Any]]:
        require_member(load_membership(ctx, actor, default_family_id))
        item = ctx.store.get(keys.user(actor.user_id), "FEISHU")
        if item is None:
            raise ValidationFailed("尚未绑定飞书")
        if int(item.get("version", 1)) != expected_version:
            raise DomainError("绑定信息已变化", code="version_conflict")
        tx = ctx.store.tx()
        guard_actor(tx, actor)
        tx.update(
            keys.user(actor.user_id),
            "FEISHU",
            "SET default_family_id = :f, version = version + :one",
            condition="version = :v",
            values={":f": default_family_id, ":one": 1, ":v": expected_version},
            on_fail=lambda _: StaleRead("绑定信息已变化"),
        )
        new = {**item, "default_family_id": default_family_id, "version": expected_version + 1}
        return Built(
            tx, binding_view(new), [{"object_type": "feishu_binding", "object_id": actor.user_id}]
        )

    return actions.run(
        ctx,
        actor,
        Scope.account(actor, key),
        key,
        "feishu.update",
        {"fid": default_family_id, "v": expected_version},
        build,
        lambda r: binding_view(ctx.store.get(keys.user(actor.user_id), "FEISHU")),
    )


def delete_binding(ctx: AppContext, actor: Actor, key: str) -> None:
    def build() -> Built[None]:
        load_profile(ctx, actor)
        item = ctx.store.get(keys.user(actor.user_id), "FEISHU")
        tx = ctx.store.tx()
        guard_actor(tx, actor)
        if item is not None:
            tx.delete(keys.user(actor.user_id), "FEISHU")
            tx.delete(
                _fsid(item["tenant_key"], item["open_id"]),
                "FSID",
                condition="user_id = :u",
                values={":u": actor.user_id},
            )
        return Built(tx, None, [{"object_type": "feishu_binding", "object_id": actor.user_id}])

    actions.run(
        ctx, actor, Scope.account(actor, key), key, "feishu.delete", {}, build, lambda r: None
    )


# ── 事件入口 ──


def handle_event(
    ctx: AppContext, secrets: FeishuSecrets, headers: dict[str, str], raw_body: bytes
) -> dict[str, Any]:
    """验签解密后快速持久接收；不在请求内调用模型或飞书接口（FS-02／06）。"""
    body = open_envelope(raw_body, headers, secrets)  # FeishuAuthError → 401
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}
    header = body["header"]
    now = ctx.clock()
    tx = Tx(ctx.store.table)
    tx.put_new(
        {
            "PK": f"FSEVT#{header['event_id']}",
            "SK": "EVT",
            "type": "feishu_event",
            "event_type": header.get("event_type"),
            "ttl": int((now + timedelta(days=EVENT_TTL_DAYS)).timestamp()),
        }
    )
    try:
        ctx.store.commit(tx)
    except DomainError:
        return {}  # 重复投递：已处理
    if header.get("event_type") == "im.message.receive_v1":
        _on_message(ctx, header, body["event"], now)
    return {}


def _reply(
    ctx: AppContext, fid_for_outbox: str, open_id: str, uuid: str, text: str, now: datetime
) -> None:
    tx = Tx(ctx.store.table)
    tx.put(
        recognition.feishu_outbox_item(
            fid_for_outbox, "feishu_text", uuid, open_id, now, text=text
        ),
        condition="attribute_not_exists(PK)",
    )
    try:
        ctx.store.commit(tx)
    except DomainError:
        pass


SYSTEM_PARTITION = "_system"  # 未绑定用户的回复不归属任何家庭


def _on_message(
    ctx: AppContext, header: dict[str, Any], event: dict[str, Any], now: datetime
) -> None:
    sender = event.get("sender") or {}
    msg = event.get("message") or {}
    # 只处理私聊中的用户消息（ADR-0013 第 5 条）
    if sender.get("sender_type") != "user" or msg.get("chat_type") != "p2p":
        return
    tenant = header["tenant_key"]
    open_id = (sender.get("sender_id") or {}).get("open_id", "")
    message_id = msg.get("message_id", "")
    if not open_id or not message_id:
        return
    try:
        content = json.loads(msg.get("content") or "{}")
    except ValueError:
        content = {}
    mapping = ctx.store.get(_fsid(tenant, open_id), "FSID")
    text = content.get("text") if msg.get("message_type") == "text" else None
    if text and BIND_RE.match(text):
        _bind(ctx, tenant, open_id, BIND_RE.match(text).group(1).upper(), message_id, now)  # type: ignore[union-attr]
        return
    if mapping is None:
        # 未绑定：不调用模型、不下载资源，只回复绑定方法（FS-02）
        _reply(
            ctx,
            SYSTEM_PARTITION,
            open_id,
            f"unbound-{message_id}",
            "你还没有绑定账号。请登录小家账本网站，在「我的账号」生成绑定码，"
            "然后在这里发送：绑定 XXXXXXXX",
            now,
        )
        return
    uid = mapping["user_id"]
    binding = ctx.store.get(keys.user(uid), "FEISHU")
    profile = ctx.store.get(keys.user(uid), keys.PROFILE)
    if (
        binding is None
        or binding.get("open_id") != open_id
        or profile is None
        or profile.get("status") != "active"
    ):
        _reply(
            ctx,
            SYSTEM_PARTITION,
            open_id,
            f"invalid-{message_id}",
            "当前绑定已失效，请在网站重新生成绑定码后发送：绑定 XXXXXXXX",
            now,
        )
        return
    if binding.get("status") != "active" or profile.get("must_change_password"):
        _reply(
            ctx,
            SYSTEM_PARTITION,
            open_id,
            f"suspended-{message_id}",
            "账号密码已变更，飞书记账已暂停。请登录网站后重新生成绑定码，再发送：绑定 XXXXXXXX",
            now,
        )
        return
    fid = binding["default_family_id"]
    member = ctx.store.get(keys.family(fid), keys.member(uid))
    if member is None or member.get("status") != "active":
        _reply(
            ctx,
            SYSTEM_PARTITION,
            open_id,
            f"nofamily-{message_id}",
            "默认家庭已不可用，请在网站「我的账号」修改飞书默认家庭。",
            now,
        )
        return
    image_key = content.get("image_key") if msg.get("message_type") == "image" else None
    if image_key is None and not text:
        _reply(ctx, fid, open_id, f"unsupported-{message_id}", "目前支持文字或一张账单照片。", now)
        return
    parent = msg.get("parent_id")
    image_message_id = message_id
    if image_key:
        # 记录图片消息，供用户之后“回复这张图片”时明确关联（不按时间邻近拼接，FS-03）
        tx = Tx(ctx.store.table)
        tx.put(
            {
                "PK": f"FSMSG#{tenant}#{message_id}",
                "SK": "MSG",
                "type": "feishu_message",
                "user_id": uid,
                "image_key": image_key,
                "ttl": int((now + timedelta(days=FSMSG_TTL_DAYS)).timestamp()),
            }
        )
        ctx.store.commit(tx)
    elif parent:
        ref = ctx.store.get(f"FSMSG#{tenant}#{parent}", "MSG")
        if ref is not None and ref.get("user_id") == uid:  # 只关联本人发送的图片
            image_key = ref["image_key"]
            image_message_id = parent
    cfg = repo.family_config(ctx, fid)
    jid = "j" + hashlib.sha256(f"feishu|{tenant}|{message_id}".encode()).hexdigest()[:25]
    feishu_ref: dict[str, Any] = {
        "open_id": open_id,
        "message_id": message_id,
        "tenant_key": tenant,
    }
    if image_key:
        feishu_ref["image_key"] = image_key
        feishu_ref["message_id"] = image_message_id  # 下载资源需用图片所在消息
        feishu_ref["text_message_id"] = message_id
    job, outbox = recognition.new_job_items(
        fid,
        uid,
        jid,
        source="feishu",
        business_key=f"{tenant}|{message_id}",
        text=(text or None) and text[:300],
        attachment_id=None,
        received_date=repo.family_today(cfg, now).isoformat(),
        now=now,
        feishu=feishu_ref,
    )
    tx = Tx(ctx.store.table)
    # Job 以 (tenant, message_id) 确定性派生：平台以不同 event_id 重投同一消息也只建一个 Job
    tx.put(job, condition="attribute_not_exists(PK)")
    tx.put(outbox, condition="attribute_not_exists(PK)")
    tx.put(
        recognition.feishu_outbox_item(
            fid, "feishu_text", f"ack-{message_id}", open_id, now, text="已收到，正在识别…"
        ),
        condition="attribute_not_exists(PK)",
    )
    try:
        ctx.store.commit(tx)
    except DomainError:
        pass  # 同一消息已接收


def _bind(
    ctx: AppContext, tenant: str, open_id: str, code: str, message_id: str, now: datetime
) -> None:
    item = ctx.store.get(_code_hash(code), "CODE")
    expires = dt_in(item["expires_at"]) if item else None
    if item is None or item.get("used") or expires is None or expires <= now:
        _reply(
            ctx,
            SYSTEM_PARTITION,
            open_id,
            f"bindfail-{message_id}",
            "绑定码无效或已过期，请在网站重新生成。",
            now,
        )
        return
    uid, fid = item["user_id"], item["family_id"]
    existing = ctx.store.get(_fsid(tenant, open_id), "FSID")
    if existing is not None and existing["user_id"] != uid:
        _reply(
            ctx,
            SYSTEM_PARTITION,
            open_id,
            f"bindfail-{message_id}",
            "这个飞书账号已绑定其他账号，请先在原账号解绑。",
            now,
        )
        return
    old = ctx.store.get(keys.user(uid), "FEISHU")
    tx = Tx(ctx.store.table)
    tx.update(
        _code_hash(code),
        "CODE",
        "SET used = :t, used_by = :o",
        condition="used = :f AND expires_at > :now",
        values={":t": True, ":f": False, ":o": open_id, ":now": keys.ts(now)},
        on_fail=lambda _: DomainError("绑定码已使用", code="validation_failed"),
    )
    tx.check(
        keys.user(uid),
        keys.PROFILE,
        "#s = :active",
        names={"#s": "status"},
        values={":active": "active"},
        on_fail=lambda _: DomainError("账号不可用", code="forbidden"),
    )
    tx.put(
        {
            "PK": _fsid(tenant, open_id),
            "SK": "FSID",
            "type": "feishu_identity",
            "user_id": uid,
            "bound_at": keys.ts(now),
        }
    )
    if old is not None and (old.get("open_id"), old.get("tenant_key")) != (open_id, tenant):
        tx.delete(_fsid(old["tenant_key"], old["open_id"]), "FSID")
    tx.put(
        {
            "PK": keys.user(uid),
            "SK": "FEISHU",
            "type": "feishu_binding",
            "tenant_key": tenant,
            "open_id": open_id,
            "default_family_id": fid,
            "status": "active",
            "bound_at": keys.ts(now),
            "version": int(old.get("version", 0)) + 1 if old else 1,
        }
    )
    try:
        ctx.store.commit(tx)
    except DomainError:
        _reply(
            ctx,
            SYSTEM_PARTITION,
            open_id,
            f"bindfail-{message_id}",
            "绑定失败，请在网站重新生成绑定码。",
            now,
        )
        return
    meta = ctx.store.get(keys.family(fid), keys.META) or {}
    _reply(
        ctx,
        fid,
        open_id,
        f"bind-{message_id}",
        f"绑定成功。之后发给我的消费会记到「{meta.get('name', '默认家庭')}」，确认前不会入账。",
        now,
    )


# ── 交付（outbox → 飞书） ──


def _actor_for(ctx: AppContext, uid: str) -> Actor | None:
    p = ctx.store.get(keys.user(uid), keys.PROFILE)
    if p is None or p.get("status") != "active":
        return None
    return Actor(
        uid,
        int(p.get("session_epoch", 0)),
        bool(p.get("is_system_admin", False)),
        int(p.get("version", 1)),
    )


def deliver_due(
    ctx: AppContext,
    api: FeishuApi,
    now: datetime | None = None,
    only_family: str | None = None,
    only_open_ids: set[str] | None = None,
) -> int:
    """only_family／only_open_ids 仅供本地与测试隔离；生产处理全部到期交付。"""
    now = now or ctx.clock()
    n = 0
    for pk, sk in recognition.due_outbox(ctx, now):
        if only_family is not None and pk not in (
            keys.family(only_family),
            keys.family(SYSTEM_PARTITION),
        ):
            continue
        item = ctx.store.get(pk, sk)
        if (
            item is None
            or item.get("status") != "pending"
            or not str(item.get("kind", "")).startswith("feishu_")
        ):
            continue
        if only_open_ids is not None and item.get("open_id") not in only_open_ids:
            continue
        _deliver(ctx, api, item, now)
        n += 1
    return n


def _deliver(ctx: AppContext, api: FeishuApi, item: dict[str, Any], now: datetime) -> None:
    first = dt_in(item.get("first_attempt_at")) or now
    attempts = int(item.get("attempts", 0)) + 1
    try:
        if item["kind"] == "feishu_card":
            card = render_batch_card(ctx, item["PK"].removeprefix("FAMILY#"), item["batch_id"])
            if card is None:
                _set_delivery(ctx, item, "skipped", attempts, first, None, final=True)
                return
            mid = api.send_card(item["open_id"], card, item["uuid"])
        else:
            mid = api.send_text(item["open_id"], item["text"], item["uuid"])
    except FeishuDeliveryUnknown:
        # 已发出但结果未知：在 uuid 去重窗口内可安全重试；超出窗口不再自动重发（FS-07）
        final = attempts >= DELIVERY_MAX_ATTEMPTS or now - first > UUID_WINDOW
        _set_delivery(ctx, item, "unknown", attempts, first, None, final=final, retry_s=60)
        return
    except Exception:
        final = attempts >= DELIVERY_MAX_ATTEMPTS
        _set_delivery(
            ctx, item, "failed", attempts, first, None, final=final, retry_s=30 * attempts
        )
        return
    _set_delivery(ctx, item, "sent", attempts, first, mid, final=True)


def _set_delivery(
    ctx: AppContext,
    item: dict[str, Any],
    delivery: str,
    attempts: int,
    first: datetime,
    message_id: str | None,
    *,
    final: bool,
    retry_s: int = 0,
) -> None:
    new = {
        **item,
        "delivery": delivery,
        "attempts": attempts,
        "first_attempt_at": keys.ts(first),
        "status": delivery if final else "pending",
    }
    if message_id:
        new["message_id"] = message_id
    for k in ("GSI1PK", "GSI1SK"):
        new.pop(k, None)
    if not final:
        due = ctx.clock() + timedelta(seconds=retry_s)
        new.update(recognition._outbox_gsi(item["outbox_id"], due))
    tx = Tx(ctx.store.table)
    tx.put(new, condition="attempts = :a", values={":a": int(item.get("attempts", 0))})
    try:
        ctx.store.commit(tx)
    except DomainError:
        pass  # 并发发送器已更新


# ── 卡片 ──


def _txt(s: str) -> dict[str, str]:
    return {"tag": "plain_text", "content": s}


def _button(label: str, value: dict[str, Any], kind: str = "default") -> dict[str, Any]:
    return {
        "tag": "button",
        "text": _txt(label),
        "type": kind,
        "behaviors": [{"type": "callback", "value": value}],
    }


def _row(*buttons: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": "column_set",
        "flex_mode": "flow",
        "columns": [{"tag": "column", "width": "auto", "elements": [b]} for b in buttons],
    }


def action_key(*parts: Any) -> str:
    """服务端在构建卡片时确定性生成的动作 ID：重复点击／重复回调命中同一回执（FS-05）。"""
    return "fs" + hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:30]


def _tag(c: dict[str, Any], field: str) -> str:
    src = c["field_sources"].get(field)
    if src in SOURCE_TAG:
        return f"（{SOURCE_TAG[src]}）"
    return "（请核对）" if field in c.get("needs_review", []) else ""


def candidate_lines(c: dict[str, Any], i: int) -> str:
    cat = c.get("category") or {}
    amount = (
        c["fx_preview"]["display_amounts"]["amount"]
        if c.get("fx_preview", {}).get("status") == "ok"
        else c.get("amount") or "待补"
    )
    lines = [
        f"**{i + 1:02d} · {c.get('note') or '候选'}**　{c.get('currency', '')} {amount}"
        f"{_tag(c, 'amount')}",
        f"类型：{TYPE_ZH.get(c.get('type') or '', '待选择')}{_tag(c, 'type')}　"
        f"日期：{c.get('business_date', '')}{_tag(c, 'business_date')}",
        f"分类：{cat.get('parent_name', '待选择')}／{cat.get('leaf_name', '')}"
        f"{_tag(c, 'category_id')}　"
        f"方式：{METHOD_ZH.get(c.get('payment_method') or '', '—')}{_tag(c, 'payment_method')}",
    ]
    fx = c.get("fx_preview") or {}
    if fx.get("status") == "ok":
        d = fx["display_amounts"]
        lines.append(
            f"合 NZD {d['nzd']} · 合 CNY {d['cny']} · 汇率日期 {fx['snapshot']['effective_date']}"
        )
    if c["missing_fields"]:
        lines.append(f"⚠️ 待补充：{'、'.join(c['missing_fields'])}")
    for p in c.get("problems", []):
        lines.append(f"⚠️ {candidates_problem(p)}")
    if c["status"] == "confirmed":
        lines.append("✅ 已入账")
    return "\n".join(lines)


def candidates_problem(code: str) -> str:
    return {
        "refund_not_supported": "看起来是退款：请在网站从原消费录入退款",
        "category_inactive": "分类已停用，请编辑后重新选择",
        "category_kind_mismatch": "分类与类型不符，请编辑",
        "rate_pending": "缺少汇率，需管理员补录",
    }.get(code, code)


def build_card(
    fid: str,
    family_name: str,
    batch: dict[str, Any],
    *,
    edit: dict[str, Any] | None = None,
    edit_parent: str | None = None,
    catalog: Any = None,
    currencies: list[str] | None = None,
    notice: str = "",
) -> dict[str, Any]:
    """卡片 JSON 2.0（字段待飞书卡片搭建工具核实，ADR-0013）。"""
    b = batch["batch_id"]
    open_ = batch["status"] == "open"
    pending = [c for c in batch["candidates"] if c["status"] != "confirmed"]
    title = (
        f"待确认 {len(pending)} 笔"
        if open_ and pending
        else "已全部处理"
        if batch["status"] == "closed"
        else {"expired": "已过期", "cancelled": "已取消"}.get(batch["status"], "已处理")
    )
    els: list[dict[str, Any]] = []
    if notice:
        els.append({"tag": "markdown", "content": notice})
    for issue in batch.get("input_issues", []):
        els.append({"tag": "markdown", "content": f"⚠️ {issue}"})
    if not batch["candidates"]:
        els.append(
            {
                "tag": "markdown",
                "content": "没有识别出可记录的内容，可以重新发送或在网站手工记一笔。",
            }
        )
    for i, c in enumerate(batch["candidates"]):
        els.append({"tag": "markdown", "content": candidate_lines(c, i)})
        cid, v = c["candidate_id"], c["version"]
        if edit is not None and edit["candidate_id"] == cid and open_:
            els.extend(_edit_form(fid, b, c, edit_parent, catalog, currencies or []))
        elif open_ and c["status"] not in ("confirmed", "expired"):
            btns = []
            if c["status"] == "ready":
                btns.append(
                    _button(
                        "确认",
                        {
                            "a": "confirm",
                            "f": fid,
                            "b": b,
                            "c": cid,
                            "v": v,
                            "d": c["content_digest"],
                            "k": action_key(b, cid, v, "confirm"),
                        },
                        "primary",
                    )
                )
            btns.append(_button("编辑", {"a": "edit", "f": fid, "b": b, "c": cid}))
            btns.append(
                _button(
                    "删除",
                    {
                        "a": "delete",
                        "f": fid,
                        "b": b,
                        "c": cid,
                        "v": v,
                        "k": action_key(b, cid, v, "delete"),
                    },
                    "danger",
                )
            )
            els.append(_row(*btns))
        els.append({"tag": "hr"})
    ready = [c for c in batch["candidates"] if c["status"] == "ready"]
    if open_ and edit is None:
        footer = []
        if len(ready) > 1:
            items = [
                {"c": c["candidate_id"], "v": c["version"], "d": c["content_digest"]} for c in ready
            ]
            footer.append(
                _button(
                    f"全部确认（{len(ready)}）",
                    {
                        "a": "confirm_all",
                        "f": fid,
                        "b": b,
                        "items": items,
                        "k": action_key(b, json.dumps(items, sort_keys=True)),
                    },
                    "primary",
                )
            )
        footer.append(
            _button(
                "取消整批",
                {
                    "a": "cancel",
                    "f": fid,
                    "b": b,
                    "v": batch["version"],
                    "k": action_key(b, batch["version"], "cancel"),
                },
            )
        )
        els.append(_row(*footer))
    els.append(
        {
            "tag": "markdown",
            "content": "<font color='grey'>确认后才会入账 · 7 天内有效 · "
            "复杂拆分请在网站完成</font>",
        }
    )
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": _txt(f"小家账本 · {title}"),
            "subtitle": _txt(family_name),
            "template": "green" if open_ else "grey",
        },
        "body": {"elements": els},
    }


def _edit_form(
    fid: str,
    b: str,
    c: dict[str, Any],
    edit_parent: str | None,
    catalog: Any,
    currencies: list[str],
) -> list[dict[str, Any]]:
    cid, v = c["candidate_id"], c["version"]
    ctype = c.get("type") or "expense"
    kind = "expense" if ctype == "expense" else ctype
    groups = [g for g in catalog.groups() if g.kind == kind and g.status == "active"]
    parent = (
        edit_parent
        or (c.get("category") or {}).get("parent_id")
        or (groups[0].category_id if groups else "")
    )
    leaves = [x for x in catalog.children(parent) if x.status == "active" and not x.redirect_to]
    leaf = (c.get("category") or {}).get("leaf_id")
    if leaf not in [x.category_id for x in leaves]:
        leaf = leaves[0].category_id if leaves else None

    def opts(pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
        return [{"text": _txt(label), "value": value} for value, label in pairs]

    parent_select = {
        "tag": "select_static",
        "placeholder": _txt("一级分类"),
        "initial_option": parent,
        "options": opts([(g.category_id, g.name) for g in groups]),
        "behaviors": [{"type": "callback", "value": {"a": "parent", "f": fid, "b": b, "c": cid}}],
    }
    form_elements: list[dict[str, Any]] = [
        {
            "tag": "select_static",
            "name": "type",
            "placeholder": _txt("类型"),
            "initial_option": ctype,
            "options": opts(list(TYPE_ZH.items())),
        },
        {
            "tag": "input",
            "name": "amount",
            "label": _txt("金额"),
            "default_value": c.get("amount") or "",
        },
        {
            "tag": "select_static",
            "name": "currency",
            "placeholder": _txt("币种"),
            "initial_option": c.get("currency") or "",
            "options": opts([(x, x) for x in currencies]),
        },
        {"tag": "date_picker", "name": "business_date", "initial_date": c.get("business_date")},
        {
            "tag": "select_static",
            "name": "leaf_category_id",
            "placeholder": _txt("二级分类"),
            "initial_option": leaf or "",
            "options": opts([(x.category_id, x.name) for x in leaves]),
        },
        {
            "tag": "select_static",
            "name": "payment_method",
            "placeholder": _txt("方式"),
            "initial_option": c.get("payment_method") or "",
            "options": opts(list(METHOD_ZH.items())),
        },
        {"tag": "input", "name": "note", "label": _txt("备注"), "default_value": c.get("note", "")},
        _row(
            {
                "tag": "button",
                "name": "save",
                "text": _txt("保存修改"),
                "type": "primary",
                "form_action_type": "submit",
                "behaviors": [
                    {
                        "type": "callback",
                        "value": {
                            "a": "save",
                            "f": fid,
                            "b": b,
                            "c": cid,
                            "v": v,
                            "k": action_key(b, cid, v, "save"),
                        },
                    }
                ],
            },
            _button("返回", {"a": "view", "f": fid, "b": b}),
        ),
    ]
    return [
        {"tag": "markdown", "content": "先选一级分类，再修改其他字段并保存："},
        parent_select,
        {"tag": "form", "name": f"edit_{cid}", "elements": form_elements},
    ]


def render_batch_card(
    ctx: AppContext,
    fid: str,
    bid: str,
    *,
    edit_cid: str | None = None,
    edit_parent: str | None = None,
    notice: str = "",
) -> dict[str, Any] | None:
    batch_item = ctx.store.get(keys.family(fid), f"BATCH#{bid}")
    if batch_item is None:
        return None
    actor = _actor_for(ctx, batch_item["actor_uid"])
    if actor is None:
        return None
    view = candidates.get_batch(ctx, actor, fid, bid)
    meta = ctx.store.get(keys.family(fid), keys.META) or {}
    edit = next((c for c in view["candidates"] if c["candidate_id"] == edit_cid), None)
    return build_card(
        fid,
        meta.get("name", ""),
        view,
        edit=edit,
        edit_parent=edit_parent,
        catalog=repo.catalog(ctx, fid),
        currencies=sorted(repo.family_currencies(ctx, fid)),
        notice=notice,
    )


# ── 卡片回调 ──


def handle_card(
    ctx: AppContext, secrets: FeishuSecrets, headers: dict[str, str], raw_body: bytes
) -> dict[str, Any]:
    """3 秒内同步完成并返回新卡片（ADR-0013 第 9 条）。身份只取 operator.open_id。"""
    body = open_envelope(raw_body, headers, secrets)
    if body.get("type") == "url_verification":  # 配置卡片回调地址时的验证
        return {"challenge": body.get("challenge", "")}
    header, event = body["header"], body["event"]
    open_id = (event.get("operator") or {}).get("open_id", "")
    action = event.get("action") or {}
    value = action.get("value") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    mapping = ctx.store.get(_fsid(header["tenant_key"], open_id), "FSID")
    if mapping is None:
        return _toast("error", "请先绑定账号")
    uid = mapping["user_id"]
    binding = ctx.store.get(keys.user(uid), "FEISHU")
    profile = ctx.store.get(keys.user(uid), keys.PROFILE)
    if (
        binding is None
        or binding.get("status") != "active"
        or profile is None
        or profile.get("status") != "active"
        or profile.get("must_change_password")
    ):
        return _toast("error", "飞书记账已暂停，请在网站重新绑定")
    actor = Actor(
        uid,
        int(profile.get("session_epoch", 0)),
        bool(profile.get("is_system_admin", False)),
        int(profile.get("version", 1)),
    )
    fid, bid = str(value.get("f", "")), str(value.get("b", ""))
    a = value.get("a")
    try:
        notice, edit_cid, edit_parent = _apply(ctx, actor, fid, bid, a, value, action)
    except DomainError as e:
        if e.code in ("forbidden", "unauthenticated", "password_change_required", "not_found"):
            return _toast("error", "无权操作这张卡片")  # 不泄露对象内容（AUTH-08／10）
        card = render_batch_card(ctx, fid, bid, notice=f"⚠️ {e.message}")
        return _toast("warning", e.message, card)
    card = render_batch_card(ctx, fid, bid, edit_cid=edit_cid, edit_parent=edit_parent)
    return _toast("success", notice, card) if notice else _card_only(card)


def _apply(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    bid: str,
    a: Any,
    value: dict[str, Any],
    action: dict[str, Any],
) -> tuple[str, str | None, str | None]:
    if a in ("view", None):
        candidates.get_batch(ctx, actor, fid, bid)  # 权限检查
        return "", None, None
    if a == "edit":
        candidates.get_batch(ctx, actor, fid, bid)
        return "", str(value["c"]), None
    if a == "parent":
        candidates.get_batch(ctx, actor, fid, bid)
        return "", str(value["c"]), str(action.get("option") or "")
    if a == "confirm":
        out = candidates.confirm(
            ctx,
            actor,
            fid,
            bid,
            value["k"],
            [{"candidate_id": value["c"], "version": value["v"], "content_digest": value["d"]}],
        )
        return f"已入账 {len(out['entries'])} 笔", None, None
    if a == "confirm_all":
        items = [
            {"candidate_id": i["c"], "version": i["v"], "content_digest": i["d"]}
            for i in value.get("items", [])
        ]
        out = candidates.confirm(ctx, actor, fid, bid, value["k"], items)
        return f"已入账 {len(out['entries'])} 笔", None, None
    if a == "delete":
        candidates.delete_candidate(ctx, actor, fid, bid, value["c"], value["k"], value["v"])
        return "已删除该候选", None, None
    if a == "cancel":
        candidates.cancel_batch(ctx, actor, fid, bid, value["k"], value["v"])
        return "已取消整批", None, None
    if a == "save":
        form = action.get("form_value") or {}
        batch = candidates.get_batch(ctx, actor, fid, bid)
        cur = next(c for c in batch["candidates"] if c["candidate_id"] == value["c"])
        patch: dict[str, Any] = {}
        mapping = {
            "type": "type",
            "amount": "amount",
            "currency": "currency",
            "business_date": "business_date",
            "leaf_category_id": "leaf_category_id",
            "payment_method": "payment_method",
            "note": "note",
        }
        current = {**cur, "leaf_category_id": (cur.get("category") or {}).get("leaf_id")}
        for k, field in mapping.items():
            if k in form and form[k] not in (None, "") and form[k] != current.get(field):
                patch[field] = str(form[k]).strip()
        if form.get("business_date"):
            patch["business_date"] = str(form["business_date"])[:10]
            if patch["business_date"] == cur.get("business_date"):
                patch.pop("business_date")
        if patch:
            candidates.update_candidate(
                ctx, actor, fid, bid, value["c"], value["k"], patch, value["v"]
            )
        return "已保存修改" if patch else "没有修改", None, None
    raise ValidationFailed("不支持的操作")


def _toast(kind: str, content: str, card: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"toast": {"type": kind, "content": content}}
    if card is not None:
        out["card"] = {"type": "raw", "data": card}
    return out


def _card_only(card: dict[str, Any] | None) -> dict[str, Any]:
    return {"card": {"type": "raw", "data": card}} if card is not None else {}

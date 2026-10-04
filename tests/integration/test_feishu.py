"""FS-01～07、AUTH-03（飞书暂停）／08／10：飞书私聊记账闭环。

证据层级：离线（签名加密的合成回调＋飞书替身＋替身模型）。
不代表真实飞书协议与时延（见 ADR-0013 待核实项）。
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, PngImagePlugin

from ledger.adapters.dynamo import keys
from ledger.adapters.feishu import FeishuAuthError, FeishuSecrets
from ledger.ai.fake import FakeModel, ScriptedModel
from ledger.application import families, feishu, recognition
from ledger.domain.authz import Actor
from ledger.local.blobs import LocalBlobStore
from ledger.local.feishu import LOCAL_SECRETS, FakeFeishu, card_event, message_event, signed_request

from .conftest import World, new_key, raw_items

SECRET = b"s" * 32


class Bot:
    """一个家庭的飞书测试环境：发送事件、跑 Worker 与交付、点击卡片。"""

    def __init__(self, w: World, blobs: LocalBlobStore, model: Any = None) -> None:
        self.w = w
        self.api = FakeFeishu()
        self.open_ids: set[str] = set()
        self.deps = recognition.WorkerDeps(
            model=model or FakeModel(), blobs=blobs, sleep=lambda s: None, feishu=self.api
        )

    def event(self, payload: dict[str, Any], **kw: Any) -> dict[str, Any]:
        return feishu.handle_event(self.w.ctx, LOCAL_SECRETS, *signed_request(payload, **kw))

    def say(self, open_id: str, **kw: Any) -> dict[str, Any]:
        sig = {k: kw.pop(k) for k in ("ts", "encrypted") if k in kw}
        return self.event(message_event(open_id, **kw), **sig)

    def o(self, name: str) -> str:
        """每个测试独立的 open_id（FSID 映射是全局命名空间，测试共用一套表）。"""
        oid = f"ou_{name}_{self.w.fid[-8:]}"
        self.open_ids.add(oid)
        return oid

    def work(self) -> None:
        recognition.process_pending(self.w.ctx, self.deps, only_family=self.w.fid)
        feishu.deliver_due(
            self.w.ctx, self.api, only_family=self.w.fid, only_open_ids=self.open_ids
        )

    def click(self, open_id: str, value: dict[str, Any], **kw: Any) -> dict[str, Any]:
        return feishu.handle_card(
            self.w.ctx, LOCAL_SECRETS, *signed_request(card_event(open_id, value, **kw))
        )

    def texts(self, open_id: str) -> list[str]:
        return [
            m["content"] for m in self.api.sent if m["kind"] == "text" and m["open_id"] == open_id
        ]

    def cards(self, open_id: str) -> list[dict[str, Any]]:
        return [
            m["content"] for m in self.api.sent if m["kind"] == "card" and m["open_id"] == open_id
        ]


def values(card: dict[str, Any], action: str) -> list[dict[str, Any]]:
    out = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for b in node.get("behaviors", []):
                if b.get("value", {}).get("a") == action:
                    out.append(b["value"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(card)
    return out


def _walk(node: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        out.append(node)
        for v in node.values():
            out += _walk(v)
    elif isinstance(node, list):
        for v in node:
            out += _walk(v)
    return out


def bind(bot: Bot, actor: Actor, open_id: str) -> None:
    code = feishu.create_binding_code(
        bot.w.ctx, actor, new_key(), default_family_id=bot.w.fid, secret=SECRET
    )["code"]
    bot.say(open_id, text=f"绑定 {code}")


@pytest.fixture
def bot(world: World, tmp_path: Path) -> Bot:
    return Bot(world, LocalBlobStore(tmp_path / "b", b"k" * 32, "", "fs-photos"))


def png(total: str) -> bytes:
    info = PngImagePlugin.PngInfo()
    info.add_text("total", total)
    info.add_text("currency", "NZD")
    buf = io.BytesIO()
    Image.new("RGB", (100, 160), "white").save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


class RawFake(FakeModel):
    wants_raw_image = True


class TestBinding:
    def test_fs01_one_time_code(self, bot: Bot) -> None:
        w = bot.w
        code = feishu.create_binding_code(
            w.ctx, w.member, new_key(), default_family_id=w.fid, secret=SECRET
        )["code"]
        bot.say(bot.o("member"), text=f"绑定 {code.lower()}")
        binding = w.ctx.store.get(keys.user(w.member.user_id), "FEISHU")
        assert binding is not None and binding["open_id"] == bot.o("member")
        bot.work()
        assert any("绑定成功" in t for t in bot.texts(bot.o("member")))
        # 同一码不能再被别人使用
        bot.say(bot.o("attacker"), text=f"绑定 {code}")
        assert w.ctx.store.get(f"FSID#tenant-local#{bot.o('attacker')}", "FSID") is None
        bot.work()
        assert any("无效或已过期" in t for t in bot.texts(bot.o("attacker")))

    def test_fs01_expired_and_wrong_codes(self, bot: Bot) -> None:
        w = bot.w
        code = feishu.create_binding_code(
            w.ctx, w.member, new_key(), default_family_id=w.fid, secret=SECRET
        )["code"]
        w.clock.advance(minutes=11)
        bot.say(bot.o("member"), text=f"绑定 {code}")
        bot.say(bot.o("member"), text="绑定 ZZZZZZZZ")
        assert w.ctx.store.get(keys.user(w.member.user_id), "FEISHU") is None

    def test_code_replay_with_same_key_returns_same_code(self, bot: Bot) -> None:
        w = bot.w
        key = new_key()
        a = feishu.create_binding_code(w.ctx, w.member, key, default_family_id=w.fid, secret=SECRET)
        b = feishu.create_binding_code(w.ctx, w.member, key, default_family_id=w.fid, secret=SECRET)
        assert a["code"] == b["code"] and len(a["code"]) == 8


class TestIngress:
    def test_fs02_unbound_group_and_bad_requests_never_reach_model(self, bot: Bot) -> None:
        w = bot.w
        bot.deps.model = ScriptedModel([])  # 若被调用会抛错
        bot.say(bot.o("stranger"), text="超市 45")
        bind(bot, w.member, bot.o("member"))
        bot.say(bot.o("member"), text="超市 45", chat_type="group")
        bot.say(bot.o("member"), text="超市 45", sender_type="bot")
        with pytest.raises(FeishuAuthError):
            bot.say(bot.o("member"), text="超市 45", tenant="other-tenant")
        with pytest.raises(FeishuAuthError):
            bot.say(bot.o("member"), text="超市 45", app_id="cli_other")
        with pytest.raises(FeishuAuthError):
            bot.say(bot.o("member"), text="超市 45", ts=int(time.time()) - 3600)  # 过期重放
        headers, raw = signed_request(message_event(bot.o("member"), text="超市 45"))
        headers["x-lark-signature"] = "0" * 64
        with pytest.raises(FeishuAuthError):
            feishu.handle_event(w.ctx, LOCAL_SECRETS, headers, raw)
        wrong = FeishuSecrets("cli_local", "x", "wrong-token", "local-encrypt-key", "tenant-local")
        with pytest.raises(FeishuAuthError):
            feishu.handle_event(
                w.ctx,
                LOCAL_SECRETS,
                *signed_request(message_event(bot.o("member"), text="x", s=wrong), s=wrong),
            )
        bot.work()
        assert raw_items(w.ctx, w.fid, "JOB#") == []
        assert any("还没有绑定" in t for t in bot.texts(bot.o("stranger")))

    def test_url_verification(self, bot: Bot) -> None:
        out = bot.event(
            {
                "type": "url_verification",
                "challenge": "abc",
                "token": LOCAL_SECRETS.verification_token,
            }
        )
        assert out == {"challenge": "abc"}

    def test_fs05_duplicate_event_and_redelivered_message(self, bot: Bot) -> None:
        w = bot.w
        bind(bot, w.member, bot.o("member"))
        payload = message_event(bot.o("member"), text="超市45纽币", message_id="om_dup")
        headers, raw = signed_request(payload)
        feishu.handle_event(w.ctx, LOCAL_SECRETS, headers, raw)
        feishu.handle_event(w.ctx, LOCAL_SECRETS, headers, raw)  # 同一事件重投
        bot.say(bot.o("member"), text="超市45纽币", message_id="om_dup")  # 新 event_id、同一消息
        assert len(raw_items(w.ctx, w.fid, "JOB#")) == 1

    def test_fs06_ingress_does_not_call_feishu_synchronously(self, bot: Bot) -> None:
        bind(bot, bot.w.member, bot.o("member"))
        bot.api.sent.clear()
        bot.say(bot.o("member"), text="超市 45")
        assert bot.api.sent == []  # 回执、卡片都经 outbox 异步发送
        bot.work()
        assert "已收到，正在识别…" in bot.texts(bot.o("member"))
        assert len(bot.cards(bot.o("member"))) == 1

    def test_fs03_image_and_explicit_text_association(self, bot: Bot) -> None:
        w = bot.w
        bot.deps.model = RawFake()
        bind(bot, w.member, bot.o("member"))
        bot.api.images[("om_img", "img_key_1")] = png("45.00")
        bot.say(bot.o("member"), image_key="img_key_1", message_id="om_img")
        bot.say(
            bot.o("member"), text="这是昨天超市的小票", message_id="om_reply", parent_id="om_img"
        )
        bot.say(bot.o("member"), text="停车 8 纽币", message_id="om_alone")
        bot.work()
        jobs = {j["business_key"].split("|")[1]: j for j in raw_items(w.ctx, w.fid, "JOB#")}
        assert jobs["om_img"]["attachment_id"] and jobs["om_img"]["status"] == "candidate_ready"
        assert jobs["om_reply"]["feishu"]["image_key"] == "img_key_1"  # 明确关联
        assert "image_key" not in jobs["om_alone"]["feishu"]  # 不按时间邻近拼接
        att = w.ctx.store.get(keys.family(w.fid), f"ATT#{jobs['om_img']['attachment_id']}")
        assert att is not None and att["status"] == "ready" and att["uploader"] == w.member.user_id

    def test_reply_to_someone_elses_image_not_associated(self, bot: Bot) -> None:
        w = bot.w
        bind(bot, w.member, bot.o("member"))
        bind(bot, w.admin, bot.o("admin"))
        bot.say(bot.o("admin"), image_key="img_admin", message_id="om_admin_img")
        bot.say(bot.o("member"), text="超市 45", message_id="om_m", parent_id="om_admin_img")
        job = next(j for j in raw_items(w.ctx, w.fid, "JOB#") if j["business_key"].endswith("om_m"))
        assert "image_key" not in job["feishu"]


class TestCards:
    def _card(self, bot: Bot, text: str = "超市45纽币，停车8纽币") -> dict[str, Any]:
        bind(bot, bot.w.member, bot.o("member"))
        bot.say(bot.o("member"), text=text)
        bot.work()
        return bot.cards(bot.o("member"))[-1]

    def test_fs04_card_shows_required_fields(self, bot: Bot) -> None:
        card = self._card(bot)
        text = json.dumps(card, ensure_ascii=False)
        for s in ("测试之家", "超市", "NZD", "合 CNY", "食品与餐饮", "默认值", "接收日", "确认"):
            assert s in text, s

    def test_fs04_edit_in_card_with_parent_refresh_then_confirm(self, bot: Bot) -> None:
        w = bot.w
        card = self._card(bot, "超市45纽币")
        edit = values(card, "edit")[0]
        r = bot.click(bot.o("member"), edit)
        form_card = r["card"]["data"]
        assert values(form_card, "save")
        parent = values(form_card, "parent")[0]
        r = bot.click(bot.o("member"), parent, option="expense-02")  # 改一级分类 → 刷新二级选项
        leaf_select = next(
            e for e in _walk(r["card"]["data"]) if e.get("name") == "leaf_category_id"
        )
        leaf_names = [o["text"]["content"] for o in leaf_select["options"]]
        assert "日用杂物" in leaf_names and "超市综合购物" not in leaf_names
        save = values(r["card"]["data"], "save")[0]
        r = bot.click(
            bot.o("member"),
            save,
            form_value={
                "amount": "46.5",
                "leaf_category_id": "expense-02-01",
                "payment_method": "cash",
                "note": "日用品",
            },
        )
        assert r["toast"]["type"] == "success"
        new_card = r["card"]["data"]
        assert "46.50" in json.dumps(new_card, ensure_ascii=False)
        confirm = values(new_card, "confirm")[0]
        r = bot.click(bot.o("member"), confirm)
        assert r["toast"]["content"] == "已入账 1 笔"
        entries = raw_items(w.ctx, w.fid, "ENTRY#")
        assert len(entries) == 1 and entries[0]["source"] == "feishu"
        assert entries[0]["amount_minor"] == 4650 and entries[0]["payment_method"] == "cash"

    def test_fs05_repeated_click_and_atomic_batch(self, bot: Bot) -> None:
        w = bot.w
        card = self._card(bot)
        confirm = values(card, "confirm")[0]
        bot.click(bot.o("member"), confirm)
        again = bot.click(bot.o("member"), confirm)  # 重复点击：命中同一回执
        assert again["toast"]["content"] == "已入账 1 笔"
        assert len(raw_items(w.ctx, w.fid, "ENTRY#")) == 1
        # 旧卡片上的“全部确认”包含已入账候选的旧版本 → 整批拒绝，不部分入账
        stale_all = values(card, "confirm_all")[0]
        r = bot.click(bot.o("member"), stale_all)
        assert r["toast"]["type"] == "warning"
        assert len(raw_items(w.ctx, w.fid, "ENTRY#")) == 1

    def test_fs07_delivery_unknown_retries_same_uuid_and_keeps_entries(self, bot: Bot) -> None:
        w = bot.w
        bind(bot, w.member, bot.o("member"))
        bot.work()
        bot.api.fail_next = ["unknown"]  # 卡片已发出但响应丢失
        bot.say(bot.o("member"), text="超市45纽币")
        recognition.process_pending(w.ctx, bot.deps, only_family=w.fid)
        # 先交付回执，再交付卡片（顺序依 outbox）；跑两轮覆盖重试
        feishu.deliver_due(w.ctx, bot.api, only_family=w.fid, only_open_ids=bot.open_ids)
        w.clock.advance(seconds=61)
        feishu.deliver_due(w.ctx, bot.api, only_family=w.fid, only_open_ids=bot.open_ids)
        cards = [m for m in bot.api.sent if m["kind"] == "card"]
        assert len(cards) == 1  # 平台按 uuid 去重，没有重复卡片
        out = [o for o in raw_items(w.ctx, w.fid, "OUTBOX#") if o.get("kind") == "feishu_card"]
        assert out[0]["delivery"] == "sent" and int(out[0]["attempts"]) >= 1
        confirm = values(cards[0]["content"], "confirm")[0]
        bot.click(bot.o("member"), confirm)
        assert len(raw_items(w.ctx, w.fid, "ENTRY#")) == 1

    def test_fs07_send_failure_never_changes_ledger(self, bot: Bot) -> None:
        w = bot.w
        card = self._card(bot, "超市45纽币")
        bot.click(bot.o("member"), values(card, "confirm")[0])
        bot.api.fail_next = ["error"] * 10
        bot.say(bot.o("member"), text="停车 8 纽币")
        for _ in range(6):
            bot.work()
            w.clock.advance(minutes=5)
        outs = [
            o for o in raw_items(w.ctx, w.fid, "OUTBOX#") if str(o.get("kind")).startswith("feishu")
        ]
        failed = [o for o in outs if o.get("status") == "failed"]
        assert failed and len(raw_items(w.ctx, w.fid, "ENTRY#")) == 1

    def test_auth08_removed_member_cannot_confirm_old_card(self, bot: Bot) -> None:
        w = bot.w
        card = self._card(bot)
        m = w.ctx.store.get(keys.family(w.fid), keys.member(w.member.user_id))
        assert m is not None
        families.remove_member(
            w.ctx, w.admin, w.fid, w.member.user_id, new_key(), expected_version=int(m["version"])
        )
        r = bot.click(bot.o("member"), values(card, "confirm")[0])
        assert r == {"toast": {"type": "error", "content": "无权操作这张卡片"}}
        assert raw_items(w.ctx, w.fid, "ENTRY#") == []

    def test_auth10_other_user_cannot_use_my_card(self, bot: Bot) -> None:
        w = bot.w
        card = self._card(bot)
        bind(bot, w.admin, bot.o("admin"))  # 家庭管理员也不能确认他人候选
        r = bot.click(bot.o("admin"), values(card, "confirm")[0])
        assert r["toast"]["type"] == "error" and "card" not in r
        assert raw_items(w.ctx, w.fid, "ENTRY#") == []
        assert bot.click(bot.o("nobody"), values(card, "confirm")[0])["toast"]["type"] == "error"

    def test_auth03_password_reset_suspends_feishu(self, bot: Bot) -> None:
        w = bot.w
        card = self._card(bot)
        w.ctx.store.client.update_item(
            TableName=w.ctx.store.table,
            Key={"PK": {"S": keys.user(w.member.user_id)}, "SK": {"S": "FEISHU"}},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": {"S": "suspended"}},
        )
        r = bot.click(bot.o("member"), values(card, "confirm")[0])
        assert r["toast"]["type"] == "error"
        jobs_before = len(raw_items(w.ctx, w.fid, "JOB#"))
        bot.say(bot.o("member"), text="停车 8 纽币")
        assert len(raw_items(w.ctx, w.fid, "JOB#")) == jobs_before
        bot.work()
        assert any("已暂停" in t for t in bot.texts(bot.o("member")))
        # 重新绑定后恢复
        bind(bot, w.member, bot.o("member"))
        binding = w.ctx.store.get(keys.user(w.member.user_id), "FEISHU")
        assert binding is not None and binding["status"] == "active"

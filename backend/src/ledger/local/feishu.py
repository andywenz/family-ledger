"""本地飞书替身：记录发送内容；构造带签名、加密的平台回调（测试与本地演示）。"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from ledger.adapters.feishu import FeishuDeliveryUnknown, FeishuSecrets, encrypt, signature

# 本地替身的固定假值，非真实凭证
LOCAL_SECRETS = FeishuSecrets(
    app_id="cli_local",
    app_secret="local-secret",
    verification_token="local-token",
    encrypt_key="local-encrypt-key",
    tenant_key="tenant-local",
)


@dataclass
class FakeFeishu:
    sent: list[dict[str, Any]] = field(default_factory=list)
    images: dict[tuple[str, str], bytes] = field(default_factory=dict)
    fail_next: list[str] = field(default_factory=list)  # "unknown" | "error"
    seen_uuids: dict[str, str] = field(default_factory=dict)

    def _send(self, open_id: str, kind: str, content: Any, uuid: str) -> str:
        if uuid in self.seen_uuids:  # 模拟平台 1 小时 uuid 去重
            return self.seen_uuids[uuid]
        mode = self.fail_next.pop(0) if self.fail_next else ""
        mid = f"om_{secrets.token_hex(6)}"
        if mode == "error":
            raise RuntimeError("模拟发送失败")
        self.seen_uuids[uuid] = mid
        self.sent.append(
            {"open_id": open_id, "kind": kind, "content": content, "uuid": uuid, "message_id": mid}
        )
        if mode == "unknown":  # 平台已发送但客户端超时
            raise FeishuDeliveryUnknown("模拟超时")
        return mid

    def send_card(self, open_id: str, card: dict[str, Any], uuid: str) -> str:
        return self._send(open_id, "card", card, uuid)

    def send_text(self, open_id: str, text: str, uuid: str) -> str:
        return self._send(open_id, "text", text, uuid)

    def download_image(self, message_id: str, file_key: str) -> bytes:
        return self.images[(message_id, file_key)]


def signed_request(
    payload: dict[str, Any],
    s: FeishuSecrets = LOCAL_SECRETS,
    ts: int | None = None,
    encrypted: bool = True,
) -> tuple[dict[str, str], bytes]:
    body_obj = (
        {"encrypt": encrypt(payload, s.encrypt_key, secrets.token_bytes(16))}
        if encrypted
        else payload
    )
    raw = json.dumps(body_obj).encode()
    stamp = str(ts if ts is not None else int(time.time()))
    nonce = secrets.token_hex(8)
    headers = {
        "x-lark-request-timestamp": stamp,
        "x-lark-request-nonce": nonce,
        "x-lark-signature": signature(stamp, nonce, s.encrypt_key, raw),
    }
    return headers, raw


def message_event(
    open_id: str,
    *,
    text: str | None = None,
    image_key: str | None = None,
    message_id: str | None = None,
    parent_id: str | None = None,
    chat_type: str = "p2p",
    sender_type: str = "user",
    s: FeishuSecrets = LOCAL_SECRETS,
    tenant: str | None = None,
    app_id: str | None = None,
) -> dict[str, Any]:
    mtype = "image" if image_key else "text"
    content = {"image_key": image_key} if image_key else {"text": text}
    msg: dict[str, Any] = {
        "message_id": message_id or f"om_{secrets.token_hex(6)}",
        "chat_id": "oc_local",
        "chat_type": chat_type,
        "message_type": mtype,
        "content": json.dumps(content),
    }
    if parent_id:
        msg["parent_id"] = parent_id
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"ev_{secrets.token_hex(6)}",
            "event_type": "im.message.receive_v1",
            "token": s.verification_token,
            "app_id": app_id or s.app_id,
            "tenant_key": tenant or s.tenant_key,
            "create_time": str(int(time.time() * 1000)),
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": open_id},
                "sender_type": sender_type,
                "tenant_key": tenant or s.tenant_key,
            },
            "message": msg,
        },
    }


def card_event(
    open_id: str,
    value: dict[str, Any],
    *,
    form_value: dict[str, Any] | None = None,
    option: str | None = None,
    s: FeishuSecrets = LOCAL_SECRETS,
) -> dict[str, Any]:
    action: dict[str, Any] = {"tag": "button", "value": value}
    if form_value is not None:
        action["form_value"] = form_value
    if option is not None:
        action["option"] = option
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"ev_{secrets.token_hex(6)}",
            "event_type": "card.action.trigger",
            "token": s.verification_token,
            "app_id": s.app_id,
            "tenant_key": s.tenant_key,
        },
        "event": {
            "operator": {"open_id": open_id, "tenant_key": s.tenant_key},
            "token": "c-" + secrets.token_hex(8),
            "action": action,
            "context": {"open_message_id": "om_card", "open_chat_id": "oc_local"},
        },
    }

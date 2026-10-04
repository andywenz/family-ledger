"""飞书开放平台适配器（ADR-0013）：验签、解密、发送消息、下载图片。

协议依据 2026-10-04 官方文档核实；卡片 JSON 与 URL 验证结构待真实联调确认。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

BASE_URL = "https://open.feishu.cn/open-apis"
MAX_SKEW_S = 300
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class FeishuAuthError(Exception):
    """签名、时间戳、token、应用或租户校验失败。"""


class FeishuDeliveryUnknown(Exception):
    """发送请求已发出但结果未知（超时／连接中断）。"""


@dataclass(frozen=True)
class FeishuSecrets:
    app_id: str
    app_secret: str
    verification_token: str
    encrypt_key: str
    tenant_key: str  # 允许的企业租户


def signature(timestamp: str, nonce: str, encrypt_key: str, raw_body: bytes) -> str:
    return hashlib.sha256((timestamp + nonce + encrypt_key).encode() + raw_body).hexdigest()


# 只解析开头的“日期 时间[.小数] 时区偏移”；
# 其后可能有时区名与 Go 单调时钟读数（如 "CST m=+3612.1"），忽略
_GO_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(\d+))? ([+-]\d{4})(?:\s|$)")


def _parse_timestamp(ts: str) -> float | None:
    """事件回调为 Unix 秒；卡片回调为 Go time.String()，如
    '2026-10-04 18:23:46.052290112 +0800 CST m=+3612.1'
    （2026-10-04 生产实测，末尾可能带单调时钟读数）。"""
    ts = ts.strip()
    if ts.isdigit():
        return float(ts)
    m = _GO_TIME.match(ts)
    if not m:
        return None
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    dt = datetime.strptime(f"{m.group(1)}.{frac} {m.group(3)}", "%Y-%m-%d %H:%M:%S.%f %z")
    return dt.timestamp()


def verify_request(
    headers: dict[str, str], raw_body: bytes, secrets: FeishuSecrets, now: float | None = None
) -> None:
    ts = headers.get("x-lark-request-timestamp", "")
    nonce = headers.get("x-lark-request-nonce", "")
    sig = headers.get("x-lark-signature", "")
    if not ts or not nonce or not sig:
        raise FeishuAuthError("缺少签名头")
    sent_at = _parse_timestamp(ts)
    if sent_at is None:
        raise FeishuAuthError(f"时间戳无效：{ts[:80]!r}")
    if abs((now or time.time()) - sent_at) > MAX_SKEW_S:
        raise FeishuAuthError("请求已过期")
    # 签名按原始头部字符串计算；网关可能去掉首尾空格，因此也接受去空格后的版本
    candidates = {ts, ts.strip()}
    if not any(
        hmac.compare_digest(sig, signature(t, nonce, secrets.encrypt_key, raw_body))
        for t in candidates
    ):
        raise FeishuAuthError("签名不匹配")


def decrypt(encrypted: str, encrypt_key: str) -> dict[str, Any]:
    data = base64.b64decode(encrypted)
    if len(data) < 32 or len(data) % 16:
        raise FeishuAuthError("密文长度无效")
    key = hashlib.sha256(encrypt_key.encode()).digest()
    dec = Cipher(algorithms.AES(key), modes.CBC(data[:16])).decryptor()
    padded = dec.update(data[16:]) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        plain = unpadder.update(padded) + unpadder.finalize()
        out = json.loads(plain)
    except (ValueError, UnicodeDecodeError):
        raise FeishuAuthError("解密失败") from None
    if not isinstance(out, dict):
        raise FeishuAuthError("解密内容不是对象")
    return out


def encrypt(payload: dict[str, Any], encrypt_key: str, iv: bytes) -> str:
    """测试与本地替身使用：构造与平台相同格式的密文。"""
    key = hashlib.sha256(encrypt_key.encode()).digest()
    padder = padding.PKCS7(128).padder()
    plain = padder.update(json.dumps(payload).encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + enc.update(plain) + enc.finalize()).decode()


def open_envelope(
    raw_body: bytes, headers: dict[str, str], secrets: FeishuSecrets, now: float | None = None
) -> dict[str, Any]:
    """验签 → 解密 → 校验 token、app_id、tenant_key。返回明文事件。"""
    signed = all(
        headers.get(h)
        for h in ("x-lark-request-timestamp", "x-lark-request-nonce", "x-lark-signature")
    )
    if signed:
        verify_request(headers, raw_body, secrets, now)  # 带签名头时签名必须正确
    try:
        outer = json.loads(raw_body)
    except ValueError:
        raise FeishuAuthError("请求体不是 JSON") from None
    if not isinstance(outer, dict):
        raise FeishuAuthError("请求体不是对象")
    cipher = outer.get("encrypt") or outer.get("encrypted")
    body = decrypt(cipher, secrets.encrypt_key) if cipher else outer
    if body.get("type") == "url_verification":
        # 配置请求地址时的验证：官方文档未写明是否带签名头（2026-10-04 核实不到），
        # 因此允许无签名，但必须能用 Encrypt Key 解密且 token 正确；只回显 challenge，不处理任何数据
        if not cipher and not signed:
            raise FeishuAuthError("未加密且无签名的验证请求")
        if not hmac.compare_digest(str(body.get("token", "")), secrets.verification_token):
            raise FeishuAuthError("token 不匹配")
        return body
    if not signed:
        raise FeishuAuthError("缺少签名头")  # 普通事件与卡片回调必须带签名
    header = body.get("header") or {}
    if not hmac.compare_digest(str(header.get("token", "")), secrets.verification_token):
        raise FeishuAuthError("token 不匹配")
    if header.get("app_id") != secrets.app_id:
        raise FeishuAuthError("应用不匹配")
    if header.get("tenant_key") != secrets.tenant_key:
        # 记录收到的租户标识（非密钥），便于首次配置白名单
        raise FeishuAuthError(f"租户不在允许范围：{header.get('tenant_key')}")
    return body


class FeishuApi(Protocol):
    def send_card(self, open_id: str, card: dict[str, Any], uuid: str) -> str: ...

    def send_text(self, open_id: str, text: str, uuid: str) -> str: ...

    def download_image(self, message_id: str, file_key: str) -> bytes: ...


class FeishuClient:  # pragma: no cover - 真实调用在 D4 经授权后验收
    def __init__(self, secrets: FeishuSecrets, http: httpx.Client | None = None) -> None:
        self.secrets = secrets
        self.http = http or httpx.Client(base_url=BASE_URL, timeout=8.0)
        self._token: tuple[str, float] | None = None

    def _tenant_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        r = self.http.post(
            "/auth/v3/tenant_access_token/internal",
            json={"app_id": self.secrets.app_id, "app_secret": self.secrets.app_secret},
        )
        data = r.json()
        if r.status_code != 200 or data.get("code") != 0:
            raise RuntimeError(f"获取 tenant_access_token 失败：{data.get('code')}")
        self._token = (data["tenant_access_token"], time.time() + int(data.get("expire", 0)))
        return self._token[0]

    def _send(self, open_id: str, msg_type: str, content: dict[str, Any], uuid: str) -> str:
        try:
            r = self.http.post(
                "/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {self._tenant_token()}"},
                json={
                    "receive_id": open_id,
                    "msg_type": msg_type,
                    "content": json.dumps(content, ensure_ascii=False),
                    "uuid": uuid[:50],
                },
            )
        except (httpx.TimeoutException, httpx.RemoteProtocolError) as e:
            raise FeishuDeliveryUnknown(str(e)) from e
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"发送失败：{data.get('code')}")
        return str(data["data"]["message_id"])

    def send_card(self, open_id: str, card: dict[str, Any], uuid: str) -> str:
        return self._send(open_id, "interactive", card, uuid)

    def send_text(self, open_id: str, text: str, uuid: str) -> str:
        return self._send(open_id, "text", {"text": text}, uuid)

    def download_image(self, message_id: str, file_key: str) -> bytes:
        with self.http.stream(
            "GET",
            f"/im/v1/messages/{message_id}/resources/{file_key}",
            params={"type": "image"},
            headers={"Authorization": f"Bearer {self._tenant_token()}"},
        ) as r:
            if r.status_code != 200:
                raise RuntimeError(f"下载图片失败：{r.status_code}")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_IMAGE_BYTES:
                    raise ValueError("图片过大")
            return bytes(buf)

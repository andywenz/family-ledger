"""飞书请求外壳：配置地址时的验证请求与签名要求（离线）。"""

from __future__ import annotations

import json
import secrets

import pytest

from ledger.adapters.feishu import FeishuAuthError, encrypt, open_envelope
from ledger.local.feishu import LOCAL_SECRETS, message_event, signed_request

VERIFY = {
    "type": "url_verification",
    "challenge": "c-123",
    "token": LOCAL_SECRETS.verification_token,
}


def _encrypted(payload: dict, key: str = "encrypt") -> bytes:
    return json.dumps(
        {key: encrypt(payload, LOCAL_SECRETS.encrypt_key, secrets.token_bytes(16))}
    ).encode()


@pytest.mark.parametrize("field", ["encrypt", "encrypted"])
def test_encrypted_verification_without_signature_headers(field: str) -> None:
    assert open_envelope(_encrypted(VERIFY, field), {}, LOCAL_SECRETS)["challenge"] == "c-123"


def test_verification_with_wrong_token_rejected() -> None:
    with pytest.raises(FeishuAuthError):
        open_envelope(_encrypted({**VERIFY, "token": "wrong"}), {}, LOCAL_SECRETS)


def test_plaintext_unsigned_verification_rejected() -> None:
    with pytest.raises(FeishuAuthError):
        open_envelope(json.dumps(VERIFY).encode(), {}, LOCAL_SECRETS)


def test_unsigned_event_rejected_even_if_encrypted() -> None:
    """普通事件必须带签名：只有密文不够。"""
    with pytest.raises(FeishuAuthError, match="签名"):
        open_envelope(_encrypted(message_event("ou_x", text="hi")), {}, LOCAL_SECRETS)


def test_bad_signature_rejected_for_verification_too() -> None:
    headers, raw = signed_request(VERIFY)
    headers["x-lark-signature"] = "0" * 64
    with pytest.raises(FeishuAuthError):
        open_envelope(raw, headers, LOCAL_SECRETS)


def test_card_callback_url_verification() -> None:
    from ledger.application import feishu

    out = feishu.handle_card(None, LOCAL_SECRETS, {}, _encrypted(VERIFY))  # type: ignore[arg-type]
    assert out == {"challenge": "c-123"}


def _go_time(epoch: float) -> str:
    from datetime import UTC, datetime, timedelta, timezone

    dt = datetime.fromtimestamp(epoch, UTC).astimezone(timezone(timedelta(hours=8)))
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + "052290112 +0800 CST "


def _signed_with_ts(payload: dict, ts: str) -> tuple[dict[str, str], bytes]:
    from ledger.adapters.feishu import signature

    raw = _encrypted(payload)
    nonce = "abcdefghi"
    return {
        "x-lark-request-timestamp": ts,
        "x-lark-request-nonce": nonce,
        "x-lark-signature": signature(ts, nonce, LOCAL_SECRETS.encrypt_key, raw),
    }, raw


def test_card_callback_go_time_timestamp_accepted() -> None:
    """卡片回调时间戳是 Go 时间字符串（2026-10-04 生产实测），签名按原始字符串计算。"""
    import time

    ts = _go_time(time.time())
    headers, raw = _signed_with_ts(VERIFY, ts)
    assert open_envelope(raw, headers, LOCAL_SECRETS)["challenge"] == "c-123"
    headers["x-lark-request-timestamp"] = ts.strip()  # 网关去掉尾部空格时签名仍可核对
    headers["x-lark-signature"] = __import__(
        "ledger.adapters.feishu", fromlist=["signature"]
    ).signature(ts.strip(), headers["x-lark-request-nonce"], LOCAL_SECRETS.encrypt_key, raw)
    assert open_envelope(raw, headers, LOCAL_SECRETS)["challenge"] == "c-123"


def test_stale_go_time_timestamp_rejected() -> None:
    import time

    headers, raw = _signed_with_ts(VERIFY, _go_time(time.time() - 3600))
    with pytest.raises(FeishuAuthError, match="过期"):
        open_envelope(raw, headers, LOCAL_SECRETS)


@pytest.mark.parametrize("ts", ["yesterday", "2026-10-04T18:23:46Z", "12a"])
def test_unparseable_timestamp_rejected(ts: str) -> None:
    headers, raw = _signed_with_ts(VERIFY, ts)
    with pytest.raises(FeishuAuthError, match="时间戳无效"):
        open_envelope(raw, headers, LOCAL_SECRETS)

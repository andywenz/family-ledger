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

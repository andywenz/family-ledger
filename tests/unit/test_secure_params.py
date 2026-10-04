"""SSM 加密参数读取：只接受 SecureString（离线，botocore Stubber）。"""

from __future__ import annotations

import boto3
import pytest
from botocore.stub import Stubber

from ledger.runtime import InsecureParameter, secure_parameter


def _ssm() -> tuple[object, Stubber]:
    c = boto3.client(
        "ssm", region_name="ap-southeast-2", aws_access_key_id="x", aws_secret_access_key="x"
    )  # noqa: S106
    return c, Stubber(c)


def test_secure_string_accepted() -> None:
    c, st = _ssm()
    st.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/p", "Type": "SecureString", "Value": "s3cr3t"}},
        {"Name": "/p", "WithDecryption": True},
    )
    with st:
        assert secure_parameter(c, "/p") == "s3cr3t"


@pytest.mark.parametrize("kind", ["String", "StringList"])
def test_plaintext_parameter_rejected(kind: str) -> None:
    """误存为明文时拒绝使用，而不是照常读取。"""
    c, st = _ssm()
    st.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/p", "Type": kind, "Value": "x"}},
        {"Name": "/p", "WithDecryption": True},
    )
    with st, pytest.raises(InsecureParameter):
        secure_parameter(c, "/p")

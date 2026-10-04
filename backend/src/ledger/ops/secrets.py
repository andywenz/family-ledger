"""写入运行所需的加密参数（SSM Parameter Store SecureString）。值不打印、不落盘。

  AWS_PROFILE=<目标账户> AWS_DEFAULT_REGION=ap-southeast-2 \\
  uv run python -m ledger.ops.secrets --account <账户 ID> init-session   # 生成随机会话密钥
  uv run python -m ledger.ops.secrets --account <账户 ID> set-feishu     # 输入飞书参数（隐藏）

参数名：/family-ledger/<env>/session-secret、/family-ledger/<env>/feishu（与 CDK 一致）。
更换会话密钥会使所有登录失效（需重新登录）；修改后需让函数冷启动才会读到新值（docs/运维Runbook.md）。
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import secrets
from typing import Any

FEISHU_FIELDS = ("app_id", "app_secret", "verification_token", "encrypt_key", "tenant_key")


def put_secure(ssm: Any, name: str, value: str, *, overwrite: bool) -> str:
    ssm.put_parameter(
        Name=name, Value=value, Type="SecureString", Overwrite=overwrite, Tier="Standard"
    )
    back = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]
    if back["Type"] != "SecureString" or back["Value"] != value:
        raise SystemExit(f"{name} 写入后读回不一致")
    return hashlib.sha256(value.encode()).hexdigest()[:8]  # 仅指纹，可安全显示


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--env", default="prod")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已有参数")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-session")
    sub.add_parser("set-feishu")
    a = ap.parse_args(argv)

    import boto3

    if boto3.client("sts").get_caller_identity()["Account"] != a.account:
        raise SystemExit("当前凭证不属于 --account 指定的账户；已停止")
    ssm = boto3.client("ssm")
    prefix = f"/family-ledger/{a.env}"
    if a.cmd == "init-session":
        fp = put_secure(
            ssm, f"{prefix}/session-secret", secrets.token_urlsafe(36), overwrite=a.overwrite
        )
    else:
        data = {
            k: getpass.getpass(f"{k}（tenant_key 未知可填 pending）：").strip()
            for k in FEISHU_FIELDS
        }
        if not all(data.values()):
            raise SystemExit("有字段为空；已停止")
        fp = put_secure(ssm, f"{prefix}/feishu", json.dumps(data), overwrite=a.overwrite)
    print(f"已写入（SecureString），指纹 {fp}")


if __name__ == "__main__":
    main()

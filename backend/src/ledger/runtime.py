"""按配置组装运行时：存储、身份服务、令牌校验与路由登记。"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

from ledger.adapters.dynamo.store import Store
from ledger.application.accounts import AuthDeps
from ledger.application.context import AppContext
from ledger.http.app import Runtime
from ledger.identity.jwt_verifier import JwtVerifier
from ledger.settings import Settings, load

log = logging.getLogger(__name__)


def _register_routes() -> None:
    from ledger.http import (  # noqa: F401  # 导入即登记
        routes_ai,
        routes_core,
        routes_feishu,
        routes_files,
        routes_ledger,
        routes_ops,
    )


def build(settings: Settings | None = None) -> Runtime:
    s = settings or load()
    store = Store.connect(s.table, s.journal_table, endpoint=s.dynamodb_endpoint, region=s.region)
    ctx = AppContext(store=store)
    if s.is_local:
        from ledger.local.identity import (
            LOCAL_CLIENT_ID,
            LOCAL_ISSUER,
            LocalIdentityProvider,
            load_key,
        )

        data = Path(s.local_data_dir)
        key = load_key(data / "local-idp-key.pem")
        secret_file = data / "session-secret"
        if not secret_file.exists():
            data.mkdir(parents=True, exist_ok=True)
            secret_file.write_bytes(secrets.token_bytes(32))
            secret_file.chmod(0o600)
        deps = AuthDeps(
            LocalIdentityProvider(store, key),
            JwtVerifier(LOCAL_ISSUER, LOCAL_CLIENT_ID, key),
            secret_file.read_bytes(),
        )
        from ledger.local.blobs import LocalBlobStore
        from ledger.local.feishu import LOCAL_SECRETS, FakeFeishu

        services: dict[str, Any] = {
            "blobs": LocalBlobStore(data / "blobs", secret_file.read_bytes(), "", "photos"),
            "exports": LocalBlobStore(data / "exports", secret_file.read_bytes(), "", "exports"),
            "feishu_secrets": LOCAL_SECRETS,
            "feishu_api": FakeFeishu(),
        }
    else:  # pragma: no cover - 生产路径在 D5 真实验收
        import boto3
        from jwt import PyJWKClient

        from ledger.identity.cognito import CognitoIdentityProvider

        idp = CognitoIdentityProvider(
            boto3.client("cognito-idp", region_name=s.region),
            s.cognito_user_pool_id,
            s.cognito_client_id,
        )
        jwks = PyJWKClient(f"{s.cognito_issuer}/.well-known/jwks.json", cache_keys=True)
        sm = boto3.client("secretsmanager", region_name=s.region)
        secret = sm.get_secret_value(SecretId=s.session_secret_arn)["SecretString"].encode()
        deps = AuthDeps(idp, JwtVerifier(s.cognito_issuer, s.cognito_client_id, jwks), secret)
        from ledger.adapters.blobs import S3BlobStore

        def s3() -> Any:
            return boto3.client("s3", region_name=s.region)

        def feishu_secrets() -> Any:
            return _feishu_secrets(
                sm.get_secret_value(SecretId=os.environ["LEDGER_FEISHU_SECRET_ARN"])["SecretString"]
            )

        def feishu_api(svc: LazyServices) -> Any:
            from ledger.adapters.feishu import FeishuClient

            fs = svc["feishu_secrets"]
            return FeishuClient(fs) if fs else None

        # 冷启动只做每个请求都需要的事；S3、飞书、模型客户端首次使用时才创建（2026-10-05）
        services = LazyServices(
            {
                "blobs": lambda _: S3BlobStore(s3(), s.blob_bucket),
                "exports": lambda _: S3BlobStore(s3(), s.export_bucket),
                "feishu_secrets": lambda _: feishu_secrets(),
                "feishu_api": feishu_api,
                "model": lambda _: build_model(s),
            }
        )
    if not isinstance(services, LazyServices):
        services["model"] = build_model(s)
    _register_routes()
    return Runtime(settings=s, ctx=ctx, auth=deps, services=services)


class LazyServices(dict[str, Any]):
    """按需创建的服务表：首次读取某项时调用其工厂并缓存；直接赋值的项照常覆盖。"""

    def __init__(self, factories: dict[str, Any]) -> None:
        super().__init__()
        self.factories = factories

    def __getitem__(self, key: str) -> Any:
        if not dict.__contains__(self, key) and key in self.factories:
            dict.__setitem__(self, key, self.factories[key](self))
        return dict.__getitem__(self, key)


def _feishu_secrets(raw: str) -> Any:
    """解析飞书 Secret。未配置（初始随机值或字段不全）时返回 None：只停用飞书，网站照常运行。"""
    import json as _json

    from ledger.adapters.feishu import FeishuSecrets

    try:
        return FeishuSecrets(**_json.loads(raw))
    except (ValueError, TypeError):
        log.warning("飞书 Secret 未配置或格式不对：飞书功能停用")
        return None


def build_model(s: Settings) -> Any:
    """本地默认替身模型；只有显式配置 LEDGER_MODEL 时才使用 Bedrock（会产生费用）。"""
    import os

    model_id = os.environ.get("LEDGER_MODEL", "")
    if not model_id:
        if not s.is_local:
            raise RuntimeError("生产环境必须配置 LEDGER_MODEL")
        from ledger.ai.fake import FakeModel

        return FakeModel()
    import boto3

    from ledger.ai.bedrock import BedrockModel, client_config

    client = boto3.client("bedrock-runtime", region_name=s.region, config=client_config(45))
    return BedrockModel(client, model_id)

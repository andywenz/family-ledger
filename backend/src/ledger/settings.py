"""运行配置（环境变量）。缺省视为生产，缺少必需配置即启动失败（ADR-0007）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    env: str
    table: str
    journal_table: str
    region: str = "ap-southeast-2"
    dynamodb_endpoint: str | None = None
    allowed_origins: tuple[str, ...] = ()
    local_data_dir: str = ".local-data"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_issuer: str = ""
    session_secret_param: str = ""
    blob_bucket: str = ""
    export_bucket: str = ""
    version: str = "dev"
    commit: str = "unknown"
    artifact_digest: str = "unknown"
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        return self.env == "local"


def load() -> Settings:
    env = os.environ.get("LEDGER_ENV", "prod")
    s = Settings(
        env=env,
        table=os.environ.get("LEDGER_TABLE", "ledger-main"),
        journal_table=os.environ.get("LEDGER_DELETION_TABLE", "ledger-deletion-journal"),
        region=os.environ.get("AWS_REGION", "ap-southeast-2"),
        dynamodb_endpoint=os.environ.get("DYNAMODB_ENDPOINT") if env == "local" else None,
        allowed_origins=tuple(
            o for o in os.environ.get("LEDGER_ALLOWED_ORIGINS", "").split(",") if o
        ),
        local_data_dir=os.environ.get("LEDGER_LOCAL_DATA_DIR", ".local-data"),
        cognito_user_pool_id=os.environ.get("COGNITO_USER_POOL_ID", ""),
        cognito_client_id=os.environ.get("COGNITO_CLIENT_ID", ""),
        cognito_issuer=os.environ.get("COGNITO_ISSUER", ""),
        session_secret_param=os.environ.get("LEDGER_SESSION_SECRET_PARAM", ""),
        blob_bucket=os.environ.get("LEDGER_BLOB_BUCKET", ""),
        export_bucket=os.environ.get("LEDGER_EXPORT_BUCKET", ""),
        version=os.environ.get("LEDGER_VERSION", "dev"),
        commit=os.environ.get("LEDGER_COMMIT", "unknown"),
        artifact_digest=os.environ.get("LEDGER_ARTIFACT_DIGEST", "unknown"),
    )
    if not s.is_local:
        missing = [
            k
            for k, v in {
                "COGNITO_USER_POOL_ID": s.cognito_user_pool_id,
                "COGNITO_CLIENT_ID": s.cognito_client_id,
                "COGNITO_ISSUER": s.cognito_issuer,
                "LEDGER_SESSION_SECRET_PARAM": s.session_secret_param,
                "LEDGER_BLOB_BUCKET": s.blob_bucket,
                "LEDGER_EXPORT_BUCKET": s.export_bucket,
                "LEDGER_ALLOWED_ORIGINS": ",".join(s.allowed_origins),
            }.items()
            if not v
        ]
        if missing:
            raise RuntimeError(f"生产配置缺失：{missing}")
        if "local" in s.cognito_issuer:
            raise RuntimeError("生产环境拒绝本地身份 issuer")
    return s

"""空账本首次初始化（D6，OPS-12）：全局币种、供应商汇率、初始系统管理员。

本地与生产共用同一流程；差别只在身份服务与汇率来源由调用方注入。
- 不创建任何家庭、账目或分类，不导入历史数据（历史导入须单独请求）。
- 可重复执行：币种与汇率只补缺不覆盖；管理员已存在则跳过且不再生成密码。
- 已有其他账号时拒绝再建系统管理员（那不是首次初始化，应走管理页）。
- 临时密码只放在返回值中，由 CLI 打印一次，不写日志或文件。
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.codec import currency_item
from ledger.adapters.dynamo.store import Tx
from ledger.domain.categories import SEED_PATH
from ledger.domain.errors import DomainError
from ledger.domain.money import CurrencyMeta
from ledger.identity.ports import generate_temporary_password

from . import rates
from .context import AppContext
from .users import create_user_record, user_id_by_login

RATE_LOOKBACK_DAYS = 7  # 与汇率解析回看上限一致（ADR-0004）


class IdentityAdmin(Protocol):
    def admin_create(self, username: str, temporary_password: str) -> str: ...


class RateSource(Protocol):
    def fetch(self, start: date, end: date, currencies: Any) -> list[Any]: ...


class InitError(Exception):
    """初始化前置条件不满足；不做任何后续写入。"""


@dataclass(frozen=True)
class InitReport:
    currencies_added: int
    rate_sets_added: int
    latest_rate_date: str | None
    admin_login: str
    admin_created: bool
    temporary_password: str | None  # 只供 CLI 打印一次
    users: int
    families: int
    entries: int


def seed_currencies(ctx: AppContext) -> int:
    seed = json.loads((SEED_PATH.parent / "currencies.json").read_text(encoding="utf-8"))
    added = 0
    for c in seed["currencies"]:
        tx = Tx(ctx.store.table)
        tx.put_new(
            currency_item(
                CurrencyMeta(c["code"], c["minor_digits"], c["name_zh"], c["provider_supported"])
            )
        )
        try:
            ctx.store.commit(tx)
            added += 1
        except DomainError:
            pass  # 已存在：不覆盖（可能已被后续操作更新）
    return added


def ledger_counts(ctx: AppContext) -> dict[str, int]:
    """统计账号、家庭与账目数量（首发验收：空账本）。初始化时表很小，扫描成本可忽略。"""
    users = sum(1 for _ in ctx.store.query_all("USERS", ""))
    families = entries = 0
    kwargs: dict[str, Any] = {
        "TableName": ctx.store.table,
        "ProjectionExpression": "PK, SK, #t",
        "ExpressionAttributeNames": {"#t": "type"},
    }
    while True:
        page = ctx.store.client.scan(**kwargs)
        for it in page.get("Items", []):
            pk, sk = it["PK"]["S"], it["SK"]["S"]
            if pk.startswith("FAMILY#") and sk == keys.META:
                families += 1
            elif pk.startswith("FAMILY#") and it.get("type", {}).get("S") == "entry":
                entries += 1
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return {"users": users, "families": families, "entries": entries}


def _latest_rate_date(ctx: AppContext, today: date) -> date | None:
    from ledger.adapters.dynamo.store import query_between

    items = query_between(
        ctx.store,
        keys.provider_rates(ctx.provider),
        (today - timedelta(days=RATE_LOOKBACK_DAYS)).isoformat(),
        today.isoformat(),
    )
    return date.fromisoformat(items[-1]["SK"][:10]) if items else None


def initialize(
    ctx: AppContext,
    idp: IdentityAdmin,
    rate_source: RateSource,
    *,
    admin_login: str,
    display_name: str,
    today: date,
    days: int = 120,
) -> InitReport:
    before = ledger_counts(ctx)
    existing_admin = user_id_by_login(ctx, admin_login)
    if existing_admin is None and before["users"] > 0:
        raise InitError("已有其他账号：这不是首次初始化。新增账号请在管理页操作。")

    currencies_added = seed_currencies(ctx)
    rate_sets_added = rates.sync_provider(ctx, rate_source, today - timedelta(days=days), today)
    latest = _latest_rate_date(ctx, today)
    if latest is None:
        raise InitError(
            f"最近 {RATE_LOOKBACK_DAYS} 天没有完整汇率组；多币种折算将不可用。"
            "请检查汇率来源后重试。"
        )

    temp: str | None = None
    if existing_admin is None:
        temp = generate_temporary_password()
        uid = f"u{secrets.token_hex(12)}"
        sub = idp.admin_create(uid, temp)
        create_user_record(
            ctx,
            login_name=admin_login,
            display_name=display_name,
            identity_sub=sub,
            is_system_admin=True,
            user_id=uid,
        )
    after = ledger_counts(ctx)
    return InitReport(
        currencies_added=currencies_added,
        rate_sets_added=rate_sets_added,
        latest_rate_date=latest.isoformat(),
        admin_login=admin_login,
        admin_created=existing_admin is None,
        temporary_password=temp,
        **after,
    )

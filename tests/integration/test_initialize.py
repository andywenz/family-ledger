"""空账本首次初始化（OPS-12）：本地与生产共用流程。每个用例使用全新空表（离线）。"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from ledger.adapters.dynamo.schema import create_tables
from ledger.adapters.dynamo.store import Store
from ledger.application.context import AppContext
from ledger.application.initialize import InitError, initialize, ledger_counts
from ledger.application.users import user_id_by_login
from ledger.domain.fx import ProviderRateSet
from ledger.ops.initialize import preflight

from ..conftest import ENDPOINT

TODAY = date(2026, 10, 5)


class Idp:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []

    def admin_create(self, username: str, temporary_password: str) -> str:
        self.created.append((username, temporary_password))
        return f"sub-{username}"


class Rates:
    def __init__(self, days: int = 30) -> None:
        self.days = days

    def fetch(self, start: date, end: date, currencies: Any) -> list[ProviderRateSet]:
        out, d = [], max(start, end - timedelta(days=self.days))
        while d <= end:
            if d.weekday() < 5:
                r = {"NZD": Decimal("0.58"), "CNY": Decimal("0.14"), "USD": Decimal(1)}
                out.append(ProviderRateSet("ecb", d, r, 1, datetime.now(UTC)))
            d += timedelta(days=1)
        return out


@pytest.fixture
def ctx() -> Iterator[AppContext]:
    suffix = secrets.token_hex(4)
    s = Store.connect(f"ledger-init-{suffix}", f"ledger-init-j-{suffix}", endpoint=ENDPOINT)
    try:
        s.client.list_tables()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"DynamoDB Local 不可用：{e}")
    create_tables(s.client, s.table, s.journal_table)
    yield AppContext(store=s)
    for t in (s.table, s.journal_table):
        s.client.delete_table(TableName=t)


def run(ctx: AppContext, idp: Idp, rates: Rates | None = None, login: str = "admin1") -> Any:
    return initialize(
        ctx, idp, rates or Rates(), admin_login=login, display_name="管理员", today=TODAY
    )


def test_ops12_empty_ledger_with_admin_only(ctx: AppContext) -> None:
    idp = Idp()
    r = run(ctx, idp)
    assert (r.users, r.families, r.entries) == (1, 0, 0)  # 不自动出现任何家庭或账目
    assert r.currencies_added == 12 and r.rate_sets_added > 0  # seed/currencies.json 全部币种
    assert r.latest_rate_date == "2026-10-05"  # 周一，当天有完整汇率组
    assert r.admin_created and r.temporary_password and len(idp.created) == 1
    assert idp.created[0][1] == r.temporary_password
    uid = user_id_by_login(ctx, "admin1")
    profile = ctx.store.get(f"USER#{uid}", "PROFILE")
    assert profile is not None
    assert profile["is_system_admin"] and profile["must_change_password"]  # 首登强制改密
    assert r.temporary_password not in str(profile)  # 密码不入库


def test_rerun_is_idempotent_and_never_reissues_password(ctx: AppContext) -> None:
    idp = Idp()
    run(ctx, idp)
    r = run(ctx, idp)
    assert (r.currencies_added, r.rate_sets_added) == (0, 0)
    assert not r.admin_created and r.temporary_password is None and len(idp.created) == 1


def test_refuses_second_admin_when_accounts_exist(ctx: AppContext) -> None:
    idp = Idp()
    run(ctx, idp)
    with pytest.raises(InitError, match="不是首次初始化"):
        run(ctx, idp, login="other")
    assert len(idp.created) == 1 and user_id_by_login(ctx, "other") is None


def test_no_recent_rates_stops_before_creating_admin(ctx: AppContext) -> None:
    idp = Idp()

    class Stale(Rates):
        def fetch(self, start: date, end: date, currencies: Any) -> list[ProviderRateSet]:
            return super().fetch(start, end - timedelta(days=20), currencies)

    with pytest.raises(InitError, match="汇率"):
        run(ctx, idp, Stale())
    assert idp.created == [] and ledger_counts(ctx)["users"] == 0


class _Sts:
    def __init__(self, account: str) -> None:
        self.account = account

    def get_caller_identity(self) -> dict[str, str]:
        return {"Account": self.account}


class _Ddb:
    def describe_table(self, TableName: str) -> dict[str, Any]:  # noqa: N803
        return {"Table": {"TableStatus": "ACTIVE"}}


class _Cognito:
    def describe_user_pool(self, UserPoolId: str) -> dict[str, Any]:  # noqa: N803
        return {}


def test_preflight_refuses_wrong_account() -> None:
    with pytest.raises(SystemExit, match="不符"):
        preflight(
            _Sts("222222222222"), _Ddb(), _Cognito(), account="111111111111", tables=["t"], pool="p"
        )
    preflight(
        _Sts("111111111111"), _Ddb(), _Cognito(), account="111111111111", tables=["t"], pool="p"
    )

"""主键规则：与 docs/storage-design.md §2 一一对应。"""

from __future__ import annotations

from datetime import UTC, date, datetime


def ts(dt: datetime) -> str:
    """固定宽度的 UTC 时间戳（毫秒），保证字典序即时间序。"""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def family(fid: str) -> str:
    return f"FAMILY#{fid}"


def user(uid: str) -> str:
    return f"USER#{uid}"


def login(name: str) -> str:
    return f"LOGIN#{name.lower()}"


def sub(cognito_sub: str) -> str:
    return f"SUB#{cognito_sub}"


META = "META"
CONFIG = "CONFIG"
PROFILE = "PROFILE"
CURRENCY_PK = "CURRENCY"


def member(uid: str) -> str:
    return f"MEMBER#{uid}"


def user_family(fid: str) -> str:
    return f"FAM#{fid}"


def invite(iid: str) -> str:
    return f"INVITE#{iid}"


def user_invite(fid: str, iid: str) -> str:
    return f"INVITE#{fid}#{iid}"


def category(cid: str) -> str:
    return f"CAT#{cid}"


def entry_prefix(month: str) -> str:
    return f"ENTRY#{month}#"


def entry(business_date: date, created_at: datetime, eid: str) -> str:
    d = business_date.isoformat()
    return f"ENTRY#{d[:7]}#{d}#{ts(created_at)}#{eid}"


def locator(eid: str) -> str:
    return f"ENTRYLOC#{eid}"


def trash(eid: str) -> str:
    return f"TRASH#{eid}"


def month_version(month: str) -> str:
    return f"MONTHVER#{month}"


def action(actor_uid: str, key: str) -> str:
    return f"ACTION#{actor_uid}#{key}"


def user_action(key: str) -> str:
    return f"ACTION#{key}"


def audit(at: datetime, aid: str) -> str:
    return f"AUDIT#{ts(at)}#{aid}"


def family_rate(d: date, currency: str) -> str:
    return f"RATE#{d.isoformat()}#{currency}"


def family_currency(code: str) -> str:
    return f"CUR#{code}"


def provider_rates(provider: str) -> str:
    return f"RATES#{provider}"


def work(kind: str, shard: int) -> str:
    return f"WORK#{kind}#{shard}"

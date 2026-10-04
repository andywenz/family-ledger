"""按 seed/currencies.json 补齐全局币种、回补供应商汇率，并可在指定管理员的家庭中启用（运维命令）。

  AWS_PROFILE=<目标账户> AWS_DEFAULT_REGION=ap-southeast-2 \\
  LEDGER_TABLE=<TableName> LEDGER_DELETION_TABLE=<JournalTableName> \\
  uv run python -m ledger.ops.currencies --account <账户 ID> [--days 125] [--enable-for <登录名>]

- 已有币种与已有日期的汇率不改写；只新增缺少的币种，并把新币种补入已有日期的汇率组。
- ECB 不发布的币种（provider_supported=false，如 MOP）不会有自动汇率，需家庭管理员补录。
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", help="生产：凭证账户必须等于此 ID")
    ap.add_argument("--days", type=int, default=125, help="回补汇率天数")
    ap.add_argument("--enable-for", help="在该登录名担任管理员的所有家庭中启用全部种子币种")
    a = ap.parse_args(argv)

    from ledger.application import families, initialize, rates, repo
    from ledger.application.users import user_id_by_login
    from ledger.domain.authz import Actor
    from ledger.ops.history_import import _store_ctx

    local = os.environ.get("LEDGER_ENV") == "local"
    if not local:
        import boto3

        if not a.account or boto3.client("sts").get_caller_identity()["Account"] != a.account:
            raise SystemExit("生产环境必须提供与当前凭证一致的 --account")
    ctx = _store_ctx(local)
    added = initialize.seed_currencies(ctx)
    print(f"新增全局币种 {added} 个")

    from ledger.adapters.frankfurter import FrankfurterClient

    today = datetime.now(UTC).date()
    n = rates.sync_provider(ctx, FrankfurterClient(), today - timedelta(days=a.days), today)
    print(f"汇率组新建或补入新币种：{n} 天")

    if a.enable_for:
        from ledger.adapters.dynamo import keys

        uid = user_id_by_login(ctx, a.enable_for)
        if uid is None:
            raise SystemExit(f"找不到登录名 {a.enable_for}")
        p = ctx.store.get(keys.user(uid), keys.PROFILE) or {}
        actor = Actor(
            uid,
            int(p.get("session_epoch", 0)),
            bool(p.get("is_system_admin")),
            int(p.get("version", 1)),
        )
        codes = sorted(repo.global_currencies(ctx))
        for fam in families.list_my_families(ctx, actor):
            if fam.get("role") != "admin":
                continue
            enabled = set(repo.family_currencies(ctx, fam["family_id"]))
            new = [c for c in codes if c not in enabled]
            for code in new:
                rates.enable_family_currency(
                    ctx, actor, fam["family_id"], f"cur-{fam['family_id'][-10:]}-{code}", code
                )
            print(f"家庭“{fam['name']}”启用 {len(new)} 个币种：{'、'.join(new) or '无'}")


if __name__ == "__main__":
    main()

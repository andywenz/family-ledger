"""分类管理用例（需求 §8、分类规则 §4；storage-design T8／T9）。

每次变更同事务递增 manifest_version，使并发录入与候选确认重新校验分类。
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.codec import category_item
from ledger.adapters.dynamo.store import Tx
from ledger.domain.authz import Actor, require_family_admin, require_member
from ledger.domain.categories import Catalog, Category
from ledger.domain.errors import StaleRead

from . import actions, repo
from .actions import Built, Scope
from .context import AppContext
from .guard import guard_member, load_membership


def usage_counts(ctx: AppContext, fid: str) -> Counter[str]:
    """叶子分类被有效与回收站账目直接引用的次数（家庭规模小，按需统计）。"""
    c: Counter[str] = Counter()
    for prefix in ("ENTRY#", "TRASH#"):
        for item in ctx.store.query_all(keys.family(fid), prefix):
            c[item["leaf_category_id"]] += 1
    return c


def _view(c: Category, usage: Counter[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "category_id": c.category_id,
        "kind": c.kind,
        "name": c.name,
        "status": c.status,
        "sort": c.sort,
        "version": c.version,
    }
    if c.parent_id:
        out["parent_id"] = c.parent_id
    if c.redirect_to:
        out["redirect_to"] = c.redirect_to
    if usage is not None:
        out["usage_count"] = usage.get(c.category_id, 0)
    return out


def list_categories(
    ctx: AppContext, actor: Actor, fid: str, *, include_inactive: bool = False
) -> dict[str, Any]:
    require_member(load_membership(ctx, actor, fid))
    cat = repo.catalog(ctx, fid)
    usage = usage_counts(ctx, fid)

    def keep(c: Category) -> bool:
        return include_inactive or c.status == "active"

    groups = []
    for g in cat.groups():
        if not keep(g):
            continue
        groups.append(
            {
                **_view(g),
                "children": [_view(ch, usage) for ch in cat.children(g.category_id) if keep(ch)],
            }
        )
    return {"manifest_version": cat.manifest_version, "groups": groups}


def _commit_change(
    ctx: AppContext, actor: Actor, fid: str, key: str, kind: str, body: Any, plan_fn: Any
) -> dict[str, Any]:
    """通用流程：管理员鉴权 → 计划变更 → 同事务写分类＋递增 manifest。"""

    def build() -> Built[dict[str, Any]]:
        m = require_family_admin(load_membership(ctx, actor, fid))
        cat = repo.catalog(ctx, fid)
        changed: Category = plan_fn(cat)
        tx = ctx.store.tx()
        guard_member(tx, actor, m)
        before = cat.by_id.get(changed.category_id)
        if before is None:
            tx.put_new(category_item(fid, changed))
        else:
            tx.put(
                category_item(fid, changed),
                condition="version = :v",
                values={":v": before.version},
                on_fail=lambda _: StaleRead("分类已变化"),
            )
        _bump_manifest(tx, fid, cat)
        now = ctx.clock()
        tx.put_new(
            {
                "PK": keys.family(fid),
                "SK": keys.audit(now, ctx.ids()),
                "type": "audit",
                "actor": actor.user_id,
                "action": kind,
                "object_id": changed.category_id,
                "before_name": before.name if before else None,
                "after_name": changed.name,
                "at": keys.ts(now),
                "ttl": int((now + timedelta(days=180)).timestamp()),
            }
        )
        return Built(
            tx,
            _view(changed),
            [
                {
                    "object_type": "category",
                    "object_id": changed.category_id,
                    "version": changed.version,
                }
            ],
        )

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        cat = repo.catalog(ctx, fid)
        return _view(cat.get(r["results"][0]["object_id"]))

    return actions.run(ctx, actor, Scope.family(fid, actor, key), key, kind, body, build, replay)


def _bump_manifest(tx: Tx, fid: str, cat: Catalog) -> None:
    tx.update(
        keys.family(fid),
        keys.CONFIG,
        "SET category_manifest_version = category_manifest_version + :one",
        condition="category_manifest_version = :m",
        values={":one": 1, ":m": cat.manifest_version},
        on_fail=lambda _: StaleRead("分类目录已变化"),
    )


def _expect(cat: Catalog, cid: str, expected_version: int) -> None:
    from ledger.domain.errors import DomainError

    c = cat.get(cid)
    if c.version != expected_version:
        raise DomainError("分类已被修改", code="version_conflict", current=_view(c))


def create_category(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    key: str,
    *,
    kind: str,
    name: str,
    parent_id: str | None = None,
) -> dict[str, Any]:
    new_id = ctx.ids()
    return _commit_change(
        ctx,
        actor,
        fid,
        key,
        "category.create",
        {"kind": kind, "name": name, "parent": parent_id},
        lambda cat: cat.plan_create(f"c{new_id}", kind, name, parent_id),
    )


def update_category(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    cid: str,
    key: str,
    *,
    expected_version: int,
    name: str | None = None,
    parent_id: str | None = None,
    sort: int | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    from dataclasses import replace

    def plan(cat: Catalog) -> Category:
        _expect(cat, cid, expected_version)
        c = cat.get(cid)
        work = Catalog(cat.by_id.values(), cat.manifest_version)
        if name is not None and name != c.name:
            c = work.plan_rename(cid, name)
            work.by_id[cid] = c
        if parent_id is not None and parent_id != c.parent_id:
            c = work.plan_move(cid, parent_id)
            work.by_id[cid] = c
        if status is not None and status != c.status:
            c = work.plan_status(cid, status)
            work.by_id[cid] = c
        if sort is not None and sort != c.sort:
            c = replace(c, sort=sort)
        return replace(c, version=expected_version + 1)

    return _commit_change(
        ctx,
        actor,
        fid,
        key,
        "category.update",
        {
            "cid": cid,
            "v": expected_version,
            "name": name,
            "parent": parent_id,
            "sort": sort,
            "status": status,
        },
        plan,
    )


def merge_category(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    cid: str,
    key: str,
    *,
    target_id: str,
    expected_version: int,
) -> dict[str, Any]:
    def plan(cat: Catalog) -> Category:
        _expect(cat, cid, expected_version)
        return cat.plan_merge(cid, target_id)

    return _commit_change(
        ctx,
        actor,
        fid,
        key,
        "category.merge",
        {"cid": cid, "target": target_id, "v": expected_version},
        plan,
    )


def delete_category(
    ctx: AppContext, actor: Actor, fid: str, cid: str, key: str, *, expected_version: int
) -> None:
    def plan(cat: Catalog) -> Category:
        _expect(cat, cid, expected_version)
        return cat.plan_delete(cid, usage_counts(ctx, fid).get(cid, 0))

    _commit_change(
        ctx, actor, fid, key, "category.delete", {"cid": cid, "v": expected_version}, plan
    )

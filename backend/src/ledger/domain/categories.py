"""分类目录（需求 §8、分类规则 §4、ADR-0002）。

账目只保存稳定叶子 ID；显示名称与一级归属在查询时按当前目录与重定向解析。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from ledger import resources

from .errors import DomainError, NotFound, ValidationFailed

KIND_FOR_ENTRY_TYPE: dict[str, str] = {
    "expense": "expense",
    "refund": "expense",
    "income": "income",
    "internal_transfer": "internal_transfer",
    "exchange": "exchange",
    "card_repayment": "card_repayment",
    "receivable": "receivable",
}
KINDS = frozenset(KIND_FOR_ENTRY_TYPE.values())
MAX_REDIRECT_DEPTH = 16


@dataclass(frozen=True)
class Category:
    category_id: str
    kind: str
    name: str
    parent_id: str | None = None  # None 表示一级分类
    status: str = "active"  # active | disabled | deleted（软删除，保留以便解析与审计）
    redirect_to: str | None = None
    sort: int = 0
    version: int = 1

    @property
    def is_leaf(self) -> bool:
        return self.parent_id is not None


@dataclass(frozen=True)
class ResolvedCategory:
    leaf_id: str
    leaf_name: str
    parent_id: str
    parent_name: str
    kind: str
    leaf_status: str
    stored_leaf_id: str

    def to_dict(self) -> dict[str, str]:
        d = {
            "leaf_id": self.leaf_id,
            "leaf_name": self.leaf_name,
            "parent_id": self.parent_id,
            "parent_name": self.parent_name,
            "kind": self.kind,
            "leaf_status": self.leaf_status,
        }
        if self.stored_leaf_id != self.leaf_id:
            d["stored_leaf_id"] = self.stored_leaf_id
        return d


class Catalog:
    def __init__(self, categories: Iterable[Category], manifest_version: int = 1) -> None:
        self.by_id: dict[str, Category] = {c.category_id: c for c in categories}
        self.manifest_version = manifest_version

    def get(self, category_id: str) -> Category:
        c = self.by_id.get(category_id)
        if c is None:
            raise NotFound("分类不存在")
        return c

    def _live(self, category_id: str) -> Category:
        c = self.get(category_id)
        if c.status == "deleted":
            raise NotFound("分类不存在")
        return c

    def resolve(self, stored_leaf_id: str) -> ResolvedCategory:
        """沿重定向解析到当前叶子，返回其当前名称与一级归属。"""
        seen: set[str] = set()
        cur = self.get(stored_leaf_id)
        while cur.redirect_to is not None:
            if cur.category_id in seen or len(seen) > MAX_REDIRECT_DEPTH:
                raise DomainError("分类重定向存在环", code="category_cycle")
            seen.add(cur.category_id)
            cur = self.get(cur.redirect_to)
        if not cur.is_leaf:
            raise DomainError("账目引用的不是叶子分类", code="internal")
        assert cur.parent_id is not None
        parent = self.get(cur.parent_id)
        return ResolvedCategory(
            leaf_id=cur.category_id,
            leaf_name=cur.name,
            parent_id=parent.category_id,
            parent_name=parent.name,
            kind=cur.kind,
            leaf_status=cur.status,
            stored_leaf_id=stored_leaf_id,
        )

    def require_selectable(self, leaf_id: str, entry_type: str) -> Category:
        """新录入或修改时选择叶子：必须存在、为叶子、有效、无重定向且 kind 匹配。"""
        c = self.by_id.get(leaf_id)
        if c is None or not c.is_leaf:
            raise ValidationFailed("请选择有效的二级分类", code="validation_failed")
        if c.status != "active" or c.redirect_to is not None:
            raise DomainError("该分类已停用", code="category_inactive")
        parent = self.get(c.parent_id) if c.parent_id else None
        if parent is not None and parent.status != "active":
            raise DomainError("该分类的一级分类已停用", code="category_inactive")
        if KIND_FOR_ENTRY_TYPE[entry_type] != c.kind:
            raise DomainError("分类与记录类型不匹配", code="category_kind_mismatch")
        return c

    def groups(self) -> list[Category]:
        return sorted(
            (c for c in self.by_id.values() if not c.is_leaf and c.status != "deleted"),
            key=lambda c: (c.kind, c.sort, c.category_id),
        )

    def children(self, parent_id: str) -> list[Category]:
        return sorted(
            (c for c in self.by_id.values() if c.parent_id == parent_id and c.status != "deleted"),
            key=lambda c: (c.sort, c.category_id),
        )

    # ── 变更校验（返回新对象，由应用层事务提交） ──

    def plan_rename(self, category_id: str, name: str) -> Category:
        c = self._live(category_id)
        _check_name(name)
        siblings = [
            s
            for s in self.by_id.values()
            if s.parent_id == c.parent_id and s.kind == c.kind and s.category_id != c.category_id
        ]
        if any(s.name == name and s.status == "active" for s in siblings):
            raise ValidationFailed("同级已有同名分类")
        return replace(c, name=name, version=c.version + 1)

    def plan_move(self, leaf_id: str, new_parent_id: str) -> Category:
        c = self._live(leaf_id)
        if not c.is_leaf:
            raise ValidationFailed("只能移动二级分类")
        parent = self._live(new_parent_id)
        if parent.is_leaf:
            raise ValidationFailed("目标必须是一级分类")
        if parent.kind != c.kind:
            raise DomainError("不能跨收支类型移动分类", code="category_kind_mismatch")
        if parent.status != "active":
            raise DomainError("目标一级分类已停用", code="category_inactive")
        return replace(c, parent_id=new_parent_id, version=c.version + 1)

    def plan_status(self, category_id: str, status: str) -> Category:
        c = self._live(category_id)
        if status not in ("active", "disabled"):
            raise ValidationFailed("状态无效")
        if status == "active" and c.redirect_to is not None:
            raise ValidationFailed("已合并的分类不能重新启用")
        return replace(c, status=status, version=c.version + 1)

    def plan_merge(self, source_id: str, target_id: str) -> Category:
        src = self._live(source_id)
        tgt = self._live(target_id)
        if not (src.is_leaf and tgt.is_leaf):
            raise ValidationFailed("只能合并二级分类")
        if src.category_id == tgt.category_id:
            raise DomainError("不能合并到自身", code="category_cycle")
        if src.kind != tgt.kind:
            raise DomainError("不能跨类型合并", code="category_kind_mismatch")
        if tgt.status != "active" or tgt.redirect_to is not None:
            raise DomainError("合并目标必须是有效分类", code="category_inactive")
        if src.redirect_to is not None:
            raise ValidationFailed("该分类已经合并过")
        # 目标链不得回到源（目标无重定向，因此只需检查直接关系；保留通用检查）
        probe = tgt
        for _ in range(MAX_REDIRECT_DEPTH):
            if probe.redirect_to is None:
                break
            if probe.redirect_to == src.category_id:
                raise DomainError("合并会形成环", code="category_cycle")
            probe = self.get(probe.redirect_to)
        return replace(src, redirect_to=tgt.category_id, status="disabled", version=src.version + 1)

    def plan_create(
        self, category_id: str, kind: str, name: str, parent_id: str | None
    ) -> Category:
        if kind not in KINDS:
            raise ValidationFailed("分类类型无效")
        _check_name(name)
        if parent_id is not None:
            parent = self._live(parent_id)
            if parent.is_leaf:
                raise ValidationFailed("二级分类的上级必须是一级分类")
            if parent.kind != kind:
                raise DomainError("分类类型与上级不一致", code="category_kind_mismatch")
            if parent.status != "active":
                raise DomainError("上级分类已停用", code="category_inactive")
        siblings = [
            s
            for s in self.by_id.values()
            if s.parent_id == parent_id and s.kind == kind and s.status != "deleted"
        ]
        if any(s.name == name and s.status == "active" for s in siblings):
            raise ValidationFailed("同级已有同名分类")
        sort = max((s.sort for s in siblings), default=-1) + 1
        return Category(category_id, kind, name, parent_id, "active", None, sort, 1)

    def plan_delete(self, category_id: str, usage_count: int) -> Category:
        """软删除未使用的分类。保留项本身，避免与并发录入竞争时产生悬空引用。"""
        c = self.check_deletable(category_id, usage_count)
        return replace(c, status="deleted", version=c.version + 1)

    def check_deletable(self, category_id: str, usage_count: int) -> Category:
        c = self.get(category_id)
        if c.status == "deleted":
            raise NotFound("分类不存在")
        if usage_count > 0:
            raise DomainError("分类已被使用，请先合并或停用", code="category_in_use")
        if not c.is_leaf and self.children(category_id):
            raise DomainError("请先处理该一级分类下的二级分类", code="category_in_use")
        if any(o.redirect_to == category_id and o.status != "deleted" for o in self.by_id.values()):
            raise DomainError("有其他分类合并到此分类", code="category_in_use")
        return c


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 24 or name != name.strip():
        raise ValidationFailed("分类名称需为 1–24 个字符，且首尾无空格")


SEED_PATH = resources.path("seed/categories.json")


def load_template(path: Path = SEED_PATH) -> list[Category]:
    """读取初始分类模板；ID 在每个家庭内保持模板值（家庭分区隔离）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[Category] = []
    for gi, g in enumerate(data["categories"]):
        out.append(Category(g["id"], g["kind"], g["name"], None, g["status"], None, gi, 1))
        for ci, ch in enumerate(g["children"]):
            out.append(
                Category(ch["id"], g["kind"], ch["name"], g["id"], ch["status"], None, ci, 1)
            )
    return out

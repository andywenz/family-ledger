"""CAT-02（规则部分）、AUTH-04、AUTH-06、AUTH-10。"""

from __future__ import annotations

import pytest

from ledger.domain.authz import (
    Actor,
    Membership,
    require_can_modify_entry,
    require_candidate_owner,
    require_family_admin,
    require_member,
    require_system_admin,
)
from ledger.domain.categories import Catalog
from ledger.domain.errors import DomainError, Forbidden

from .factories import catalog


class TestCategories:
    def test_seed_has_six_kinds_and_every_group_has_leaves(self) -> None:
        cat = catalog()
        assert {g.kind for g in cat.groups()} == {
            "expense",
            "income",
            "internal_transfer",
            "exchange",
            "card_repayment",
            "receivable",
        }
        assert all(cat.children(g.category_id) for g in cat.groups())

    def test_merge_redirects_and_disables_source(self) -> None:
        cat = catalog()
        merged = cat.plan_merge("expense-01-02", "expense-01-01")
        assert (merged.redirect_to, merged.status) == ("expense-01-01", "disabled")
        cat2 = Catalog(
            [*(c for c in cat.by_id.values() if c.category_id != merged.category_id), merged]
        )
        r = cat2.resolve("expense-01-02")
        assert (r.leaf_id, r.stored_leaf_id) == ("expense-01-01", "expense-01-02")
        with pytest.raises(DomainError) as e:
            cat2.require_selectable("expense-01-02", "expense")
        assert e.value.code == "category_inactive"

    def test_merge_rejects_cross_kind_self_and_inactive_target(self) -> None:
        cat = catalog()
        for src, tgt, code in [
            ("expense-01-01", "income-01-01", "category_kind_mismatch"),
            ("expense-01-01", "expense-01-01", "category_cycle"),
        ]:
            with pytest.raises(DomainError) as e:
                cat.plan_merge(src, tgt)
            assert e.value.code == code

    def test_redirect_cycle_detected_on_resolve(self) -> None:
        from dataclasses import replace

        cat = catalog()
        a = replace(cat.get("expense-01-01"), redirect_to="expense-01-02")
        b = replace(cat.get("expense-01-02"), redirect_to="expense-01-01")
        bad = Catalog(
            [
                *(
                    c
                    for c in cat.by_id.values()
                    if c.category_id not in ("expense-01-01", "expense-01-02")
                ),
                a,
                b,
            ]
        )
        with pytest.raises(DomainError) as e:
            bad.resolve("expense-01-01")
        assert e.value.code == "category_cycle"

    def test_move_cannot_cross_kind(self) -> None:
        with pytest.raises(DomainError) as e:
            catalog().plan_move("expense-01-01", "income-01")
        assert e.value.code == "category_kind_mismatch"

    def test_delete_only_unused(self) -> None:
        cat = catalog()
        with pytest.raises(DomainError) as e:
            cat.check_deletable("expense-01-01", usage_count=1)
        assert e.value.code == "category_in_use"
        cat.check_deletable("expense-01-01", usage_count=0)
        with pytest.raises(DomainError):
            cat.check_deletable("expense-01", usage_count=0)  # 仍有子分类

    def test_create_and_duplicate_names(self) -> None:
        cat = catalog()
        c = cat.plan_create("c1", "expense", "宠物", None)
        assert c.parent_id is None
        with pytest.raises(DomainError):
            cat.plan_create("c2", "income", "子类", "expense-01")
        with pytest.raises(DomainError):
            cat.plan_create("c3", "expense", "外出用餐", "expense-01")
        with pytest.raises(DomainError):
            cat.plan_create("c4", "expense", " 空格", None)


ADMIN = Membership("f1", "u1", "admin", "active", 1)
MEMBER = Membership("f1", "u2", "member", "active", 1)
REMOVED = Membership("f1", "u3", "member", "removed", 2)


class TestAuthz:
    def test_auth06_member_edits_own_only(self) -> None:
        require_can_modify_entry(MEMBER, "u2")
        with pytest.raises(Forbidden):
            require_can_modify_entry(MEMBER, "u1")
        require_can_modify_entry(ADMIN, "u2")

    def test_removed_member_has_no_access(self) -> None:
        for fn in (require_member, require_family_admin):
            with pytest.raises(Forbidden):
                fn(REMOVED)
        with pytest.raises(Forbidden):
            require_member(None)
        with pytest.raises(Forbidden):
            require_can_modify_entry(Membership("f1", "u3", "admin", "removed", 3), "u3")

    def test_auth04_family_admin_is_not_system_admin(self) -> None:
        with pytest.raises(Forbidden):
            require_system_admin(Actor("u1", 0, is_system_admin=False))
        require_system_admin(Actor("u9", 0, is_system_admin=True))

    def test_auth10_only_candidate_owner(self) -> None:
        require_candidate_owner(Actor("u2", 0), "u2")
        with pytest.raises(Forbidden):
            require_candidate_owner(Actor("u1", 0, is_system_admin=True), "u2")

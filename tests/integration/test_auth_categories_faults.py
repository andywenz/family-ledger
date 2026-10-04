"""AUTH-04／05／06／07／08、CAT-01／02、OPS-01／02（离线，含故障注入）。"""

from __future__ import annotations

import pytest
from botocore.exceptions import ReadTimeoutError

from ledger.adapters.dynamo import keys
from ledger.application import categories, entries, families, rates
from ledger.domain.authz import Actor
from ledger.domain.entries import EntryInput
from ledger.domain.errors import DomainError

from .conftest import World, new_key, raw_items
from .helpers import expense


def member_version(w: World, uid: str) -> int:
    item = w.ctx.store.get(keys.family(w.fid), keys.member(uid))
    assert item is not None
    return int(item["version"])


class TestMembers:
    def test_auth05_roles_and_last_admin(self, world: World) -> None:
        w = world
        with pytest.raises(DomainError) as ex:
            families.change_role(
                w.ctx,
                w.admin,
                w.fid,
                w.admin.user_id,
                new_key(),
                role="member",
                expected_version=member_version(w, w.admin.user_id),
            )
        assert ex.value.code == "last_admin"
        families.change_role(
            w.ctx,
            w.admin,
            w.fid,
            w.member.user_id,
            new_key(),
            role="admin",
            expected_version=member_version(w, w.member.user_id),
        )
        families.change_role(
            w.ctx,
            w.admin,
            w.fid,
            w.admin.user_id,
            new_key(),
            role="member",
            expected_version=member_version(w, w.admin.user_id),
        )
        roles = {m["user_id"]: m["role"] for m in families.list_members(w.ctx, w.member, w.fid)}
        assert roles == {w.admin.user_id: "member", w.member.user_id: "admin"}
        with pytest.raises(DomainError) as ex:
            families.remove_member(
                w.ctx,
                w.member,
                w.fid,
                w.member.user_id,
                new_key(),
                expected_version=member_version(w, w.member.user_id),
            )
        assert ex.value.code == "last_admin"

    def test_auth05_removed_member_entries_stay_and_access_ends(self, world: World) -> None:
        w = world
        e = expense(w, w.member, amount="12")
        families.remove_member(
            w.ctx,
            w.admin,
            w.fid,
            w.member.user_id,
            new_key(),
            expected_version=member_version(w, w.member.user_id),
        )
        rows = entries.list_entries(w.ctx, w.admin, w.fid, "2026-10")["items"]
        assert rows[0]["entry_id"] == e["entry_id"]
        assert rows[0]["created_by"] == w.member.user_id
        assert (rows[0]["created_by_display"], rows[0]["created_by_left"]) == ("成员乙", True)
        for call in (
            lambda: entries.dashboard(w.ctx, w.member, w.fid, "2026-10"),
            lambda: expense(w, w.member),
        ):
            with pytest.raises(DomainError) as ex:
                call()
            assert ex.value.code == "forbidden"
        assert all(f["family_id"] != w.fid for f in families.list_my_families(w.ctx, w.member))

    def test_invite_errors_do_not_reveal_accounts(self, world: World) -> None:
        w = world
        login = w.ctx.store.get(keys.user(w.member.user_id), keys.PROFILE)["login_name"]  # type: ignore[index]
        msgs = []
        for name in ("nobody-here", login):
            with pytest.raises(DomainError) as ex:
                families.invite(w.ctx, w.admin, w.fid, new_key(), login_name=name, role="member")
            msgs.append((ex.value.code, ex.value.message))
        assert msgs[0] == msgs[1]

    def test_invitation_expiry(self, world: World) -> None:
        w = world
        from .conftest import make_user

        newbie = make_user(w.ctx, "新人", "newbie")
        login = w.ctx.store.get(keys.user(newbie.user_id), keys.PROFILE)["login_name"]  # type: ignore[index]
        inv = families.invite(w.ctx, w.admin, w.fid, new_key(), login_name=login, role="member")
        w.clock.advance(days=7, seconds=1)
        with pytest.raises(DomainError) as ex:
            families.respond_invitation(
                w.ctx, newbie, w.fid, inv["invitation_id"], new_key(), accept=True
            )
        assert ex.value.code == "invitation_expired"


class TestAuthz:
    def test_auth06_member_only_edits_own(self, world: World) -> None:
        w = world
        mine = expense(w, w.member)
        theirs = expense(w, w.admin)
        entries.update_entry(w.ctx, w.member, w.fid, mine["entry_id"], new_key(), {"note": "a"}, 1)
        with pytest.raises(DomainError) as ex:
            entries.update_entry(
                w.ctx, w.member, w.fid, theirs["entry_id"], new_key(), {"note": "b"}, 1
            )
        assert ex.value.code == "forbidden"
        entries.update_entry(w.ctx, w.admin, w.fid, mine["entry_id"], new_key(), {"note": "c"}, 2)
        with pytest.raises(DomainError):
            categories.create_category(w.ctx, w.member, w.fid, new_key(), kind="expense", name="X")
        with pytest.raises(DomainError):
            rates.put_manual_rate(
                w.ctx,
                w.member,
                w.fid,
                new_key(),
                d=__import__("datetime").date(2026, 10, 2),
                currency="AUD",
                usd_value="1",
                reason="x",
            )

    def test_auth07_cross_family_access_denied_without_leak(self, world: World) -> None:
        w = world
        e = expense(w, note="私人备注")
        with pytest.raises(DomainError) as ex:
            entries.get_entry(w.ctx, w.outsider, w.fid, e["entry_id"])
        assert ex.value.code == "forbidden" and "私人备注" not in ex.value.message
        # 用自己家庭的路径伪造别人的 entry ID：只查自己的分区，找不到
        with pytest.raises(DomainError) as ex:
            entries.get_entry(w.ctx, w.outsider, w.other_fid, e["entry_id"])
        assert ex.value.code == "not_found"
        with pytest.raises(DomainError):
            entries.update_entry(
                w.ctx, w.outsider, w.other_fid, e["entry_id"], new_key(), {"note": "x"}, 1
            )

    def test_auth04_stale_session_rejected(self, world: World) -> None:
        w = world
        w.ctx.store.client.update_item(
            TableName=w.ctx.store.table,
            Key={"PK": {"S": keys.user(w.member.user_id)}, "SK": {"S": keys.PROFILE}},
            UpdateExpression="SET session_epoch = :e",
            ExpressionAttributeValues={":e": {"N": "1"}},
        )
        with pytest.raises(DomainError) as ex:
            expense(w, w.member)
        assert ex.value.code == "unauthenticated"
        expense(w, Actor(w.member.user_id, 1))

    def test_auth08_revoked_between_read_and_commit(self, world: World, monkeypatch) -> None:  # noqa: ANN001
        """撤权发生在读取之后、提交之前：提交事务内的成员条件检查拒绝，且无账目写入。"""
        w = world
        real_commit = w.ctx.store.commit
        fired = {"done": False}

        def commit(tx, **kw):  # noqa: ANN001, ANN003, ANN202
            if not fired["done"] and any(k[1].startswith("ENTRY#") for k in tx.keys()):
                fired["done"] = True
                monkeypatch.setattr(w.ctx.store, "commit", real_commit)
                families.remove_member(
                    w.ctx,
                    w.admin,
                    w.fid,
                    w.member.user_id,
                    new_key(),
                    expected_version=member_version(w, w.member.user_id),
                )
                monkeypatch.setattr(w.ctx.store, "commit", commit)
            return real_commit(tx, **kw)

        monkeypatch.setattr(w.ctx.store, "commit", commit)
        with pytest.raises(DomainError) as ex:
            expense(w, w.member)
        assert ex.value.code == "forbidden" and fired["done"]
        assert raw_items(w.ctx, w.fid, "ENTRY#") == []
        assert raw_items(w.ctx, w.fid, f"ACTION#{w.member.user_id}#") == []


class TestCategories:
    def test_cat01_rename_reflects_in_history(self, world: World) -> None:
        w = world
        e = expense(w, leaf="expense-01-03")
        tree = categories.list_categories(w.ctx, w.admin, w.fid)
        group = next(g for g in tree["groups"] if g["category_id"] == "expense-01")
        categories.update_category(
            w.ctx,
            w.admin,
            w.fid,
            "expense-01",
            new_key(),
            name="吃喝",
            expected_version=group["version"],
        )
        categories.update_category(
            w.ctx,
            w.admin,
            w.fid,
            "expense-01-03",
            new_key(),
            parent_id="expense-07",
            expected_version=1,
        )
        got = entries.get_entry(w.ctx, w.admin, w.fid, e["entry_id"])
        assert got["category"]["parent_id"] == "expense-07"
        assert got["nzd_minor"] == e["nzd_minor"] and got["fx_snapshot"] == e["fx_snapshot"]
        pie = entries.dashboard(w.ctx, w.admin, w.fid, "2026-10")["category_pie"]
        assert pie["slices"][0]["category_id"] == "expense-07"

    def test_cat02_merge_disable_delete(self, world: World) -> None:
        w = world
        e = expense(w, leaf="expense-01-02")
        with pytest.raises(DomainError) as ex:
            categories.delete_category(
                w.ctx, w.admin, w.fid, "expense-01-02", new_key(), expected_version=1
            )
        assert ex.value.code == "category_in_use"
        categories.merge_category(
            w.ctx,
            w.admin,
            w.fid,
            "expense-01-02",
            new_key(),
            target_id="expense-01-01",
            expected_version=1,
        )
        got = entries.get_entry(w.ctx, w.admin, w.fid, e["entry_id"])
        assert got["category"]["leaf_id"] == "expense-01-01"
        assert got["category"]["stored_leaf_id"] == "expense-01-02"
        with pytest.raises(DomainError) as ex:
            expense(w, leaf="expense-01-02")
        assert ex.value.code == "category_inactive"
        categories.delete_category(
            w.ctx, w.admin, w.fid, "expense-09-01", new_key(), expected_version=1
        )
        tree = categories.list_categories(w.ctx, w.admin, w.fid, include_inactive=True)
        ids = {c["category_id"] for g in tree["groups"] for c in g["children"]}
        assert "expense-09-01" not in ids and "expense-01-02" in ids

    def test_category_change_during_entry_creation_retries(self, world: World, monkeypatch) -> None:  # noqa: ANN001
        """分类在录入读取后被停用：提交条件失败 → 重新读取 → 以 category_inactive 拒绝。"""
        w = world
        real_commit = w.ctx.store.commit
        fired = {"done": False}

        def commit(tx, **kw):  # noqa: ANN001, ANN003, ANN202
            if not fired["done"] and any(k[1].startswith("ENTRY#") for k in tx.keys()):
                fired["done"] = True
                monkeypatch.setattr(w.ctx.store, "commit", real_commit)
                categories.update_category(
                    w.ctx,
                    w.admin,
                    w.fid,
                    "expense-01-04",
                    new_key(),
                    status="disabled",
                    expected_version=1,
                )
                monkeypatch.setattr(w.ctx.store, "commit", commit)
            return real_commit(tx, **kw)

        monkeypatch.setattr(w.ctx.store, "commit", commit)
        with pytest.raises(DomainError) as ex:
            expense(w, leaf="expense-01-04")
        assert ex.value.code == "category_inactive" and fired["done"]
        assert raw_items(w.ctx, w.fid, "ENTRY#") == []


class TestFaults:
    def test_ops01_committed_but_response_lost(self, world: World, monkeypatch) -> None:  # noqa: ANN001
        """事务已提交但客户端超时：报告 unknown；按动作 ID 查到原结果；同 key 重试不重复入账。"""
        w = world
        client = w.ctx.store.client
        real = client.transact_write_items
        state = {"n": 0}

        def flaky(**kw):  # noqa: ANN003, ANN202
            out = real(**kw)
            if state["n"] == 0:
                state["n"] += 1
                raise ReadTimeoutError(endpoint_url="http://localhost:8000")
            return out

        monkeypatch.setattr(client, "transact_write_items", flaky)
        key = new_key()
        with pytest.raises(DomainError) as ex:
            expense(w, amount="66", key=key)
        assert ex.value.code == "upstream_unknown"
        receipt = entries.get_action(w.ctx, w.admin, w.fid, key)
        again = expense(w, amount="66", key=key)
        assert again["entry_id"] == receipt["results"][0]["object_id"]
        assert len(raw_items(w.ctx, w.fid, "ENTRY#")) == 1

    def test_ops02_mid_transaction_failure_leaves_nothing(self, world: World, monkeypatch) -> None:  # noqa: ANN001
        """事务后段某项（定位器）条件失败：账目、回执、月版本均不写入；修复后同 key 可重试。"""
        w = world
        existing = expense(w, amount="40")
        before = raw_items(w.ctx, w.fid, "")
        w.clock.advance(seconds=1)  # 排序键不同，只有定位器 ENTRYLOC#<id> 冲突
        monkeypatch.setattr(w.ctx, "ids", lambda: existing["entry_id"])
        key = new_key()
        with pytest.raises(DomainError):
            expense(w, amount="7", key=key)
        after = raw_items(w.ctx, w.fid, "")
        assert after == before  # 整个家庭分区逐项不变（含 MONTHVER、AUDIT、ACTION）
        monkeypatch.undo()
        ok = expense(w, amount="7", key=key)
        assert ok["entry_id"] != existing["entry_id"]
        assert len(raw_items(w.ctx, w.fid, "ENTRY#")) == 2

    def test_unknown_create_type_and_injected_fields_rejected(self, world: World) -> None:
        with pytest.raises(TypeError):
            EntryInput(
                business_date="2026-10-02",
                type="expense",
                amount="1",  # type: ignore[call-arg]
                family_id="other",
            )

"""ACC-05／12、AUTH-05／07、FX-01／02、OPS-01 经 HTTP 全链路（离线）。"""

from __future__ import annotations

import time
from typing import Any

from ledger.http.app import Runtime

from .conftest import Account, Api, complete_first_login, create_member, new_expense


def test_family_and_members_via_api(family: dict[str, Any]) -> None:
    fid, admin, member = family["fid"], family["admin"], family["member"]
    status, members = member.get(f"/families/{fid}/members")
    assert status == 200 and len(members["items"]) == 2
    status, cfg = member.get(f"/families/{fid}/config")
    assert cfg["default_currency"] == "NZD"
    status, err = member.patch(
        f"/families/{fid}/config", {"default_currency": "CNY", "expected_version": cfg["version"]}
    )
    assert status == 403
    status, cfg2 = admin.patch(
        f"/families/{fid}/config", {"default_currency": "CNY", "expected_version": cfg["version"]}
    )
    assert status == 200 and cfg2["default_currency"] == "CNY"
    m = next(x for x in members["items"] if x["user_id"] == family["member_id"])
    status, _ = admin.patch(
        f"/families/{fid}/members/{m['user_id']}",
        {"role": "admin", "expected_version": m["version"]},
    )
    assert status == 200


def test_entry_crud_dashboard_and_trash(family: dict[str, Any]) -> None:
    fid, admin, member = family["fid"], family["admin"], family["member"]
    status, preview = member.post(
        f"/families/{fid}/entries/preview",
        {
            "business_date": "2026-10-04",
            "type": "expense",
            "amount": "5",
            "currency": "NZD",
            "leaf_category_id": "expense-01-01",
            "payment_method": "cash",
        },
    )
    assert status == 200 and preview["display_amounts"]["cny"] == "18.67"
    assert preview["snapshot"]["effective_date"] == "2026-10-02"  # 周日→周五
    e = new_expense(member, fid, note="=SUM(A1)")
    assert e["can_edit"] is True and e["created_by_display"] == "成员乙"
    # ACC-12 经 HTTP：修改预览→保存同一 ID
    status, pv = member.post(
        f"/families/{fid}/entries/preview",
        {
            "entry_id": e["entry_id"],
            "patch": {"expected_version": 1, "business_date": "2026-09-30"},
        },
    )
    assert status == 200 and pv["snapshot_changed"] is True
    status, e2 = member.patch(
        f"/families/{fid}/entries/{e['entry_id']}",
        {"expected_version": 1, "business_date": "2026-09-30"},
    )
    assert status == 200 and e2["entry_id"] == e["entry_id"] and e2["version"] == 2
    status, err = member.patch(
        f"/families/{fid}/entries/{e['entry_id']}", {"expected_version": 1, "note": "旧版本"}
    )
    assert status == 409 and err["error"]["details"]["current"]["version"] == 2
    _, sept = admin.get(f"/families/{fid}/dashboard", query={"month": "2026-09"})
    assert sept["entry_count"] == 1 and sept["totals"]["net_expense"]["nzd"] == "45.00"
    status, d = admin.delete(
        f"/families/{fid}/entries/{e['entry_id']}", query={"expected_version": 2}
    )
    assert status == 200 and d["state"] == "trashed"
    _, trash = admin.get(f"/families/{fid}/trash")
    assert [t["entry_id"] for t in trash["items"]] == [e["entry_id"]]
    status, back = admin.post(
        f"/families/{fid}/entries/{e['entry_id']}/restore", {"expected_version": d["version"]}
    )
    assert status == 200 and back["state"] == "active"


def test_acc05_paging_is_stable_and_detects_changes(family: dict[str, Any]) -> None:
    fid, admin = family["fid"], family["admin"]
    ids = {new_expense(admin, fid, amount=str(i + 1))["entry_id"] for i in range(5)}
    seen: list[str] = []
    cursor = None
    while True:
        q: dict[str, Any] = {"month": "2026-10", "page_size": 2}
        if cursor:
            q["cursor"] = cursor
        status, page = admin.get(f"/families/{fid}/entries", query=q)
        assert status == 200
        seen += [i["entry_id"] for i in page["items"]]
        cursor = page.get("next_cursor")
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 5 and set(seen) == ids
    # 翻页期间数据变化：返回 409 data_changed，而不是拼接旧页
    _, first = admin.get(f"/families/{fid}/entries", query={"month": "2026-10", "page_size": 2})
    new_expense(admin, fid, amount="99")
    status, err = admin.get(
        f"/families/{fid}/entries",
        query={"month": "2026-10", "page_size": 2, "cursor": first["next_cursor"]},
    )
    assert status == 409 and err["error"]["code"] == "data_changed"
    # 筛选范围与汇总一致
    _, d = admin.get(
        f"/families/{fid}/dashboard", query={"month": "2026-10", "payment_method": "credit_card"}
    )
    assert d["filters"] == {"payment_method": "credit_card"} and d["entry_count"] == 6


def test_auth07_cross_family_via_api(
    family: dict[str, Any], runtime: Runtime, admin_api: tuple[Api, Account]
) -> None:
    fid, member = family["fid"], family["member"]
    e = new_expense(member, fid, note="私人")
    admin, _ = admin_api
    outsider_acct = create_member(admin, f"o{time.time_ns() % 10**9}")
    outsider = Api(runtime, ip="192.0.2.220")
    complete_first_login(outsider, outsider_acct, "Outsider-pass-1")
    for method, path, body in [
        ("GET", f"/families/{fid}/entries/{e['entry_id']}", None),
        ("GET", f"/families/{fid}/dashboard?month=2026-10", None),
        ("PATCH", f"/families/{fid}/entries/{e['entry_id']}", {"expected_version": 1, "note": "x"}),
        (
            "POST",
            f"/families/{fid}/entries",
            {
                "business_date": "2026-10-02",
                "type": "expense",
                "amount": "1",
                "currency": "NZD",
                "leaf_category_id": "expense-01-01",
                "payment_method": "cash",
            },
        ),
    ]:
        path, _, qs = path.partition("?")
        query = dict([qs.split("=")]) if qs else None
        status, err = outsider.call(method, path, body, query=query)
        assert status == 403, (method, path, err)
        assert "私人" not in str(err)
    # 请求体中夹带 family_id／created_by 被契约拒绝
    status, err = member.post(
        f"/families/{fid}/entries",
        {
            "business_date": "2026-10-02",
            "type": "expense",
            "amount": "1",
            "currency": "NZD",
            "leaf_category_id": "expense-01-01",
            "payment_method": "cash",
            "created_by": "x",
        },
    )
    assert status == 422


def test_ops01_idempotent_create_and_action_lookup(family: dict[str, Any]) -> None:
    fid, admin = family["fid"], family["admin"]
    body = {
        "business_date": "2026-10-02",
        "type": "income",
        "amount": "3200",
        "currency": "NZD",
        "leaf_category_id": "income-01-01",
    }
    s1, a = admin.post(f"/families/{fid}/entries", body, key="http-idem-key-00000001")
    s2, b = admin.post(f"/families/{fid}/entries", body, key="http-idem-key-00000001")
    assert s1 == s2 == 201 and a["entry_id"] == b["entry_id"]
    status, r = admin.get(f"/families/{fid}/actions/http-idem-key-00000001")
    assert status == 200 and r["results"][0]["object_id"] == a["entry_id"]
    status, err = admin.post(
        f"/families/{fid}/entries", {**body, "amount": "1"}, key="http-idem-key-00000001"
    )
    assert status == 409 and err["error"]["code"] == "idempotency_key_reused"
    assert admin.get(f"/families/{fid}/actions/never-used-key-000001")[0] == 404


def test_categories_and_rates_via_api(family: dict[str, Any]) -> None:
    fid, admin, member = family["fid"], family["admin"], family["member"]
    _, tree = member.get(f"/families/{fid}/categories")
    kinds = {g["kind"] for g in tree["groups"]}
    assert kinds == {
        "expense",
        "income",
        "internal_transfer",
        "exchange",
        "card_repayment",
        "receivable",
    }
    status, c = admin.post(f"/families/{fid}/categories", {"kind": "expense", "name": "宠物"})
    assert status == 201
    status, leaf = admin.post(
        f"/families/{fid}/categories",
        {"kind": "expense", "name": "猫粮", "parent_id": c["category_id"]},
    )
    assert status == 201
    new_expense(member, fid, leaf_category_id=leaf["category_id"])
    status, err = admin.delete(
        f"/families/{fid}/categories/{leaf['category_id']}",
        query={"expected_version": leaf["version"]},
    )
    assert status == 409 and err["error"]["code"] == "category_in_use"
    status, rates = member.get(f"/families/{fid}/rates", query={"date": "2026-10-04"})
    assert status == 200 and rates["effective_date"] == "2026-10-02"
    _, cur = member.get("/currencies")
    assert {c["code"] for c in cur["items"]} >= {"NZD", "CNY", "USD", "AUD", "EUR"}
    # 新家庭默认启用全部全局币种；FJD 尚未启用时才需要启用
    _, enabled = member.get(f"/families/{fid}/currencies")
    if "FJD" not in {c["code"] for c in enabled["items"]}:
        status, _ = admin.post(f"/families/{fid}/currencies", {"code": "FJD"})
        assert status == 201
    status, err = member.post(
        f"/families/{fid}/entries",
        {
            "business_date": "2026-10-02",
            "type": "expense",
            "amount": "10",
            "currency": "FJD",
            "leaf_category_id": "expense-01-01",
            "payment_method": "cash",
        },
    )
    assert status == 422 and err["error"]["code"] == "rate_pending"
    status, mr = admin.post(
        f"/families/{fid}/rates/manual",
        {"date": "2026-10-02", "currency": "FJD", "usd_value": "0.45", "reason": "补录"},
    )
    assert status == 201 and mr["revision"] == 1
    _, lst = member.get(
        f"/families/{fid}/rates/manual", query={"from": "2026-10-01", "to": "2026-10-31"}
    )
    assert [r["currency"] for r in lst["items"]] == ["FJD"]


def test_costs_and_alerts_admin_only(
    admin_api: tuple[Api, Account], family: dict[str, Any]
) -> None:
    admin, _ = admin_api
    status, c = admin.get("/admin/costs")
    assert status == 200 and c["budget_nzd"] == "15.00"
    assert admin.get("/admin/alerts")[0] == 200
    assert family["member"].get("/admin/costs")[0] == 403

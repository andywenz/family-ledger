"""OPS-05／06：费用估算、账单、80%／100% 去重告警；候选过期清理（离线）。"""

from __future__ import annotations

from pathlib import Path

from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.store import Tx
from ledger.ai.fake import FakeModel
from ledger.application import candidates, costs, maintenance, recognition
from ledger.local.blobs import LocalBlobStore
from ledger.local.feishu import FakeFeishu

from .conftest import World, new_key


def usage(w: World, month: str, model: str, tin: int | None, tout: int | None) -> None:
    tx = Tx(w.ctx.store.table)
    tx.put(
        {
            "PK": f"COST#{month}",
            "SK": f"USAGE#{new_key()}",
            "type": "usage",
            "model_id": model,
            "job_id": "jtest",
            "family_id": w.fid,
            "unknown": tin is None,
            "input_tokens": tin,
            "output_tokens": tout,
        }
    )
    w.ctx.store.commit(tx)


def test_estimate_marks_unknown_and_never_zero_fills(world: World) -> None:
    w = world
    month = w.clock.now.strftime("%Y-%m")
    before = costs.month_estimate(w.ctx, month)
    usage(w, month, "amazon.nova-lite-v1:0", 1_000_000, 1_000_000)  # 0.063 + 0.252 USD
    usage(w, month, "amazon.nova-lite-v1:0", None, None)
    after = costs.month_estimate(w.ctx, month)
    assert after["usd"] - before["usd"] == costs.Decimal("0.315")
    assert after["unknown"] == before["unknown"] + 1
    out = costs.get_costs(w.ctx, w.admin, month)
    assert out["estimated"]["unknown_usage_calls"] >= 1 and out["billed"] is None
    assert out["thresholds"] == [
        {"percent": 80, "amount_nzd": "12.00"},
        {"percent": 100, "amount_nzd": "15.00"},
    ]


def test_ops05_alert_dedupe_and_feishu_delivery(world: World) -> None:
    w = world
    # 系统管理员绑定飞书（直接写绑定项）
    tx = Tx(w.ctx.store.table)
    tx.put(
        {
            "PK": keys.user(w.admin.user_id),
            "SK": "FEISHU",
            "type": "feishu_binding",
            "tenant_key": "tenant-local",
            "open_id": f"ou_ops_{w.fid[-6:]}",
            "default_family_id": w.fid,
            "status": "active",
            "version": 1,
        }
    )
    w.ctx.store.commit(tx)
    w.clock.advance(days=40)  # 用一个新月份，避免与其他测试共享告警项
    month = w.clock.now.strftime("%Y-%m")
    costs.record_bill(
        w.ctx,
        month=month,
        amount="12.40",
        currency="NZD",
        as_of=w.clock.now,
        tag_coverage="complete",
    )
    first = costs.check_budget(w.ctx)
    assert first == [80]
    assert costs.check_budget(w.ctx) == []  # 同月同阈值不重复
    alerts = costs.list_alerts(w.ctx, w.admin, month)
    assert [(a["threshold_percent"], a["basis"]) for a in alerts] == [(80, "billed")]
    api = FakeFeishu()
    from ledger.application import feishu

    feishu.deliver_due(w.ctx, api, only_open_ids={f"ou_ops_{w.fid[-6:]}"})
    texts = [m["content"] for m in api.sent]
    assert any("80%" in t and "不会自动暂停" in t for t in texts)
    assert all("测试之家" not in t for t in texts)  # 告警不含家庭账目内容
    delivery = costs.list_alerts(w.ctx, w.admin, month)[0]["delivery"]
    assert {"channel": "feishu", "status": "sent", "attempts": 1} in delivery


def test_ops06_no_channel_is_visible_not_reported_sent(world: World) -> None:
    w = world
    w.clock.advance(days=80)
    month = w.clock.now.strftime("%Y-%m")
    costs.record_bill(
        w.ctx,
        month=month,
        amount="15.10",
        currency="NZD",
        as_of=w.clock.now,
        tag_coverage="partial",
    )
    costs.check_budget(w.ctx)
    alerts = costs.list_alerts(w.ctx, w.admin, month)
    assert {a["threshold_percent"] for a in alerts} >= {80, 100}
    # 本测试的系统管理员未绑定飞书：显示交付失败，而不是“已通知”
    for a in alerts:
        assert any(d["channel"] == "admin_page" for d in a["delivery"])


def test_expire_batches_clears_candidate_content(world: World, tmp_path: Path) -> None:
    w = world
    blobs = LocalBlobStore(tmp_path / "b", b"k" * 32, "", "ops")
    job = recognition.create_job(
        w.ctx, w.member, w.fid, new_key(), text="超市45纽币，停车8纽币", attachment_id=None
    )
    deps = recognition.WorkerDeps(model=FakeModel(), blobs=blobs, sleep=lambda s: None)
    recognition.process_pending(w.ctx, deps, only_family=w.fid)
    j = recognition.get_job(w.ctx, w.member, w.fid, job["job_id"])
    b = candidates.get_batch(w.ctx, w.member, w.fid, j["batch_id"])
    c0 = b["candidates"][0]
    candidates.confirm(
        w.ctx,
        w.member,
        w.fid,
        b["batch_id"],
        new_key(),
        [
            {
                "candidate_id": c0["candidate_id"],
                "version": c0["version"],
                "content_digest": c0["content_digest"],
            }
        ],
    )
    w.clock.advance(days=8)
    assert maintenance.expire_batches(w.ctx) >= 1
    items = list(w.ctx.store.query_all(keys.family(w.fid), f"BATCH#{b['batch_id']}#CAND#"))
    statuses = {i["candidate_id"]: i for i in items}
    confirmed = statuses[c0["candidate_id"]]
    assert confirmed["status"] == "confirmed" and confirmed["entry_id"]
    assert all("note" not in i and "amount" not in i for i in items)  # 正文已清除
    other = next(i for i in items if i["candidate_id"] != c0["candidate_id"])
    assert other["status"] == "expired"
    batch = w.ctx.store.get(keys.family(w.fid), f"BATCH#{b['batch_id']}")
    assert batch is not None and batch["status"] == "expired" and "GSI1PK" not in batch


def test_maintenance_skips_feishu_delivery_when_unconfigured(world: World) -> None:
    out = maintenance.run(world.ctx, blobs=None, feishu_api=None, tasks=("outbox",))
    assert "feishu_delivered" not in out  # 消息保持待发送，配置后再投递

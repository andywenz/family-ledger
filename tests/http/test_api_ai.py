"""AI 接口经 HTTP 全链路（离线：替身模型）。响应均按 OpenAPI 校验。"""

from __future__ import annotations

from typing import Any

from ledger.application import recognition
from ledger.http.app import Runtime


def test_recognition_to_confirm_via_api(family: dict[str, Any], runtime: Runtime) -> None:
    fid, m, admin = family["fid"], family["member"], family["admin"]
    status, job = m.post(f"/families/{fid}/recognition-jobs", {"text": "超市45纽币，停车8纽币"})
    assert status == 202 and job["status"] == "queued"
    deps = recognition.WorkerDeps(model=runtime.services["model"], blobs=runtime.services["blobs"])
    recognition.process_pending(runtime.ctx, deps, only_family=fid)
    status, j = m.get(f"/families/{fid}/recognition-jobs/{job['job_id']}")
    assert status == 200 and j["status"] == "candidate_ready"
    status, lst = m.get(f"/families/{fid}/batches", query={"status": "open"})
    assert [b["batch_id"] for b in lst["items"]] == [j["batch_id"]]
    status, batch = m.get(f"/families/{fid}/batches/{j['batch_id']}")
    assert status == 200 and len(batch["candidates"]) == 2
    c0, c1 = batch["candidates"]
    status, c0b = m.patch(
        f"/families/{fid}/batches/{j['batch_id']}/candidates/{c0['candidate_id']}",
        {"expected_version": 1, "note": "周末采购"},
    )
    assert status == 200 and c0b["version"] == 2
    # 他人（即使是家庭管理员）不能查看或确认
    assert admin.get(f"/families/{fid}/batches/{j['batch_id']}")[0] == 403
    status, err = m.post(
        f"/families/{fid}/batches/{j['batch_id']}/confirm",
        {
            "items": [
                {
                    "candidate_id": c0["candidate_id"],
                    "version": 1,
                    "content_digest": c0["content_digest"],
                }
            ]
        },
    )
    assert status == 409 and err["error"]["code"] == "candidate_stale"
    status, out = m.post(
        f"/families/{fid}/batches/{j['batch_id']}/confirm",
        {
            "items": [
                {
                    "candidate_id": c["candidate_id"],
                    "version": c["version"],
                    "content_digest": c["content_digest"],
                }
                for c in (c0b, c1)
            ]
        },
    )
    assert status == 200 and len(out["entries"]) == 2
    _, d = m.get(f"/families/{fid}/dashboard", query={"month": c1["business_date"][:7]})
    assert d["entry_count"] == 2


def test_more_than_ten_rejected_by_contract(family: dict[str, Any]) -> None:
    fid, m = family["fid"], family["member"]
    items = [{"candidate_id": f"c{i}", "version": 1, "content_digest": "0" * 64} for i in range(11)]
    status, _ = m.post(f"/families/{fid}/batches/bx/confirm", {"items": items})
    assert status == 422

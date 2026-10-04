"""识别任务（需求 §6.2；storage-design T12–T14；ADR-0008／0011；ISE-016／017／018）。

接收 → Job＋outbox（同事务）→ relay 派发 → Worker 条件认领（租约＋fencing）→ 复核权限 →
调用模型（每 Job 至多 2 次）→ 严格解析与语义校验 → 发布候选（fencing＋当前权限条件）。
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from PIL import Image

from ledger.adapters.blobs import BlobStore
from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.store import Tx
from ledger.ai.normalize import Draft, normalize
from ledger.ai.parser import parse
from ledger.ai.ports import CategoryOption, ModelError, ModelRequest, RecognitionModel
from ledger.ai.prompt import PROMPT_VERSION, SCHEMA_VERSION
from ledger.domain.authz import Actor, require_member
from ledger.domain.errors import DomainError, Forbidden, NotFound, StaleRead, ValidationFailed

from . import actions, repo
from .actions import Built, Scope
from .context import AppContext
from .guard import guard_member, load_membership

LEASE_S = 120
MAX_CLAIMS = 3
MAX_MODEL_CALLS = 2
CALL_TIMEOUT_S = 40.0
WORKER_DEADLINE_S = 90.0
# 账户按需配额按分钟计（Sydney Nova Micro／Lite 10 RPM，2026-10-04 读取）；限流后短等无效
RATE_LIMIT_BACKOFF_S = 8.0
BATCH_TTL_DAYS = 7
OUTBOX_SHARDS = 4
MODEL_IMAGE_MAX_SIDE = 1600
CONFIG_VERSION = "ai-v1"


def job_id_for(actor_uid: str, key: str) -> str:
    return "j" + hashlib.sha256(f"web|{actor_uid}|{key}".encode()).hexdigest()[:25]


def batch_id_for(job_id: str) -> str:
    return "b" + job_id[1:]


def _outbox_gsi(oid: str, due: datetime) -> dict[str, str]:
    shard = int(hashlib.sha256(oid.encode()).hexdigest(), 16) % OUTBOX_SHARDS
    return {"GSI1PK": keys.work("outbox", shard), "GSI1SK": f"{keys.ts(due)}#{oid}"}


def job_view(item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "job_id": item["job_id"],
        "status": item["status"],
        "created_at": item["created_at"],
    }
    if item.get("batch_id"):
        out["batch_id"] = item["batch_id"]
    if item["status"] == "failed":
        out["failure"] = {
            "class": item.get("error_class", "internal"),
            "message": FAILURE_MESSAGE.get(item.get("error_class", ""), "识别失败"),
        }
    return out


FAILURE_MESSAGE = {
    "input_rejected": "输入不符合要求",
    "permission_revoked": "已没有该家庭的访问权限",
    "protocol_error": "识别结果格式异常，请重试或手工录入",
    "schema_invalid": "识别结果格式异常，请重试或手工录入",
    "semantic_invalid": "识别结果无法使用，请手工录入",
    "rate_limited": "识别服务繁忙，请稍后再试",
    "transport_error": "识别服务暂不可用",
    "timeout": "识别超时，请稍后再试",
    "attempts_exhausted": "多次尝试仍未成功，请手工录入",
    "auth_error": "识别服务配置异常",
    "internal": "识别失败",
}


# ── 接收（T14 网站版） ──


def new_job_items(
    fid: str,
    actor_uid: str,
    jid: str,
    *,
    source: str,
    business_key: str,
    text: str | None,
    attachment_id: str | None,
    received_date: str,
    now: datetime,
    feishu: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Job 与识别 outbox（同一事务写入）。feishu：{"open_id","message_id","image_key"}。"""
    oid = "o" + jid[1:]
    job: dict[str, Any] = {
        "PK": keys.family(fid),
        "SK": f"JOB#{jid}",
        "type": "job",
        "job_id": jid,
        "family_id": fid,
        "actor_uid": actor_uid,
        "source": source,
        "business_key": business_key,
        "text": text,
        "attachment_id": attachment_id,
        "received_date": received_date,
        "status": "queued",
        "attempt": 0,
        "fencing_token": 0,
        "model_calls": 0,
        "usage_input": 0,
        "usage_output": 0,
        "unknown_usage_calls": 0,
        "outcome": {
            "request_returned": False,
            "schema_valid": False,
            "semantic_valid": False,
            "published": False,
        },
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "config_version": CONFIG_VERSION,
        "created_at": keys.ts(now),
    }
    if feishu:
        job["feishu"] = feishu
    outbox = {
        "PK": keys.family(fid),
        "SK": f"OUTBOX#{oid}",
        "type": "outbox",
        "outbox_id": oid,
        "kind": "recognize",
        "job_id": jid,
        "status": "pending",
        "attempts": 0,
        **_outbox_gsi(oid, now),
    }
    return job, outbox


def feishu_outbox_item(
    fid: str, kind: str, uuid: str, open_id: str, now: datetime, **extra: Any
) -> dict[str, Any]:
    """飞书交付意图（卡片／文字）。uuid 稳定，重试时平台在 1 小时内去重（ADR-0013 第 8 条）。"""
    oid = "f" + hashlib.sha256(uuid.encode()).hexdigest()[:25]
    return {
        "PK": keys.family(fid),
        "SK": f"OUTBOX#{oid}",
        "type": "outbox",
        "outbox_id": oid,
        "kind": kind,
        "uuid": uuid,
        "open_id": open_id,
        "status": "pending",
        "delivery": "pending",
        "attempts": 0,
        "first_attempt_at": None,
        **_outbox_gsi(oid, now),
        **extra,
    }


def create_job(
    ctx: AppContext,
    actor: Actor,
    fid: str,
    key: str,
    *,
    text: str | None,
    attachment_id: str | None,
) -> dict[str, Any]:
    if not text and not attachment_id:
        raise ValidationFailed("请填写文字或附上一张照片")
    if text is not None and not 1 <= len(text.strip()) <= 300:
        raise ValidationFailed("文字需为 1–300 个字符")
    body = {"text": text, "attachment_id": attachment_id}

    def build() -> Built[dict[str, Any]]:
        m = require_member(load_membership(ctx, actor, fid))
        cfg = repo.family_config(ctx, fid)
        now = ctx.clock()
        if attachment_id:
            att = ctx.store.get(keys.family(fid), f"ATT#{attachment_id}")
            if att is None or att["status"] != "ready":
                raise DomainError("照片不存在或尚未通过校验", code="upload_invalid")
            if att["uploader"] != actor.user_id:
                raise Forbidden("只能识别自己上传的照片")
        jid = job_id_for(actor.user_id, key)
        job, outbox = new_job_items(
            fid,
            actor.user_id,
            jid,
            source="web_ai",
            business_key=f"{actor.user_id}|{key}",
            text=text,
            attachment_id=attachment_id,
            received_date=repo.family_today(cfg, now).isoformat(),
            now=now,
        )
        tx = ctx.store.tx()
        guard_member(tx, actor, m)
        tx.put_new(job)
        tx.put_new(outbox)
        return Built(tx, job_view(job), [{"object_type": "job", "object_id": jid}])

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        return get_job(ctx, actor, fid, r["results"][0]["object_id"])

    return actions.run(
        ctx, actor, Scope.family(fid, actor, key), key, "recognition.create", body, build, replay
    )


def get_job(ctx: AppContext, actor: Actor, fid: str, jid: str) -> dict[str, Any]:
    require_member(load_membership(ctx, actor, fid))
    item = ctx.store.get(keys.family(fid), f"JOB#{jid}")
    if item is None:
        raise NotFound("任务不存在")
    if item["actor_uid"] != actor.user_id:
        raise Forbidden("只能查看自己发起的识别")
    return job_view(item)


# ── 派发（outbox relay） ──


def due_outbox(ctx: AppContext, now: datetime) -> list[tuple[str, str]]:
    out = []
    for shard in range(OUTBOX_SHARDS):
        r = ctx.store.client.query(
            TableName=ctx.store.table,
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND GSI1SK <= :now",
            ExpressionAttributeValues={
                ":pk": {"S": keys.work("outbox", shard)},
                ":now": {"S": keys.ts(now) + "~"},
            },
        )
        out += [(i["PK"]["S"], i["SK"]["S"]) for i in r.get("Items", [])]
    return out


def relay_once(
    ctx: AppContext, dispatch: Any, now: datetime | None = None, only_family: str | None = None
) -> int:
    """把到期 outbox 交给 dispatch(job_ref)。派发成功后条件回写 sent；回写失败会重复派发，
    Worker 以 Job 状态去重（ISE-018）。"""
    now = now or ctx.clock()
    n = 0
    for pk, sk in due_outbox(ctx, now):
        if only_family is not None and pk != keys.family(only_family):
            continue
        item = ctx.store.get(pk, sk)
        if item is None or item.get("status") != "pending" or item.get("kind") != "recognize":
            continue  # 飞书交付由 feishu.deliver_due 处理
        dispatch({"family_id": pk.removeprefix("FAMILY#"), "job_id": item["job_id"]})
        try:
            ctx.store.client.update_item(
                TableName=ctx.store.table,
                Key={"PK": {"S": pk}, "SK": {"S": sk}},
                UpdateExpression="SET #s = :sent, attempts = attempts + :one REMOVE GSI1PK, GSI1SK",
                ConditionExpression="#s = :pending",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":sent": {"S": "sent"},
                    ":pending": {"S": "pending"},
                    ":one": {"N": "1"},
                },
            )
        except ctx.store.client.exceptions.ConditionalCheckFailedException:
            pass
        n += 1
    return n


# ── Worker ──


@dataclass
class WorkerDeps:
    model: RecognitionModel
    blobs: BlobStore
    clock: Any = time.monotonic
    sleep: Any = time.sleep
    hooks: dict[str, Any] = field(default_factory=dict)  # 测试注入点：before_publish 等
    feishu: Any = None  # FeishuApi：下载飞书消息图片


def _claim(ctx: AppContext, fid: str, jid: str, now: datetime) -> dict[str, Any] | None:
    """条件认领；返回认领后的 Job（含新 fencing token），不可认领返回 None。"""
    try:
        r = ctx.store.client.update_item(
            TableName=ctx.store.table,
            Key={"PK": {"S": keys.family(fid)}, "SK": {"S": f"JOB#{jid}"}},
            UpdateExpression="SET #s = :running, attempt = attempt + :one, "
            "fencing_token = fencing_token + :one, lease_until = :lease",
            ConditionExpression="attempt < :max AND (#s IN (:queued, :received) OR "
            "(#s = :running AND lease_until < :now))",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":running": {"S": "running"},
                ":queued": {"S": "queued"},
                ":received": {"S": "received"},
                ":one": {"N": "1"},
                ":max": {"N": str(MAX_CLAIMS)},
                ":now": {"S": keys.ts(now)},
                ":lease": {"S": keys.ts(now + timedelta(seconds=LEASE_S))},
            },
            ReturnValues="ALL_NEW",
        )
    except ctx.store.client.exceptions.ConditionalCheckFailedException:
        return None
    from ledger.adapters.dynamo.store import deserialize

    return deserialize(r["Attributes"])


def _finish_failed(
    ctx: AppContext, job: dict[str, Any], error_class: str, extra: dict[str, Any] | None = None
) -> bool:
    """以 fencing 条件把 Job 标为失败。旧 Worker 迟到时条件失败，返回 False。"""
    values = {
        ":f": {"S": "failed"},
        ":e": {"S": error_class},
        ":running": {"S": "running"},
        ":tok": {"N": str(job["fencing_token"])},
    }
    sets = "#s = :f, error_class = :e"
    for k, v in (extra or {}).items():
        sets += f", {k} = :{k}"
        values[f":{k}"] = v
    try:
        ctx.store.client.update_item(
            TableName=ctx.store.table,
            Key={"PK": {"S": job["PK"]}, "SK": {"S": job["SK"]}},
            UpdateExpression=f"SET {sets} REMOVE lease_until",
            ConditionExpression="#s = :running AND fencing_token = :tok",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=values,
        )
        return True
    except ctx.store.client.exceptions.ConditionalCheckFailedException:
        return False


def _fail(
    ctx: AppContext, job: dict[str, Any], error_class: str, extra: dict[str, Any] | None = None
) -> None:
    if _finish_failed(ctx, job, error_class, extra) and job.get("feishu"):
        msg = FAILURE_MESSAGE.get(error_class, "识别失败")
        item = feishu_outbox_item(
            job["family_id"],
            "feishu_text",
            f"fail-{job['job_id']}",
            job["feishu"]["open_id"],
            ctx.clock(),
            text=f"这条消息没有识别成功：{msg}。可以重新发送，或在网站手工记一笔。",
        )
        tx = Tx(ctx.store.table)
        tx.put(item, condition="attribute_not_exists(PK)")
        try:
            ctx.store.commit(tx)
        except DomainError:
            pass  # 已有同一通知


def _record_usage(
    ctx: AppContext,
    job: dict[str, Any],
    model_id: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> None:
    """费用记录只含模型、tokens 与关联 ID，不含输入正文（ISE-026）。"""
    now = ctx.clock()
    item = {
        "PK": f"COST#{now.strftime('%Y-%m')}",
        "SK": f"USAGE#{keys.ts(now)}#{ctx.ids()}",
        "type": "usage",
        "model_id": model_id,
        "job_id": job["job_id"],
        "family_id": job["family_id"],
        "unknown": input_tokens is None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    tx = Tx(ctx.store.table)
    tx.put(item)
    ctx.store.commit(tx)


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """生成模型输入副本：转 RGB、限制最长边、重新编码 JPEG（去除 EXIF／GPS 等元数据）。"""
    with Image.open(io.BytesIO(data)) as src:
        rgb = src.convert("RGB")
    rgb.thumbnail((MODEL_IMAGE_MAX_SIDE, MODEL_IMAGE_MAX_SIDE))
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "jpeg"


def run_job(ctx: AppContext, deps: WorkerDeps, fid: str, jid: str) -> str:
    """处理一个 Job，返回结果状态。可被重复调用（重复投递安全）。"""
    started = deps.clock()
    job = _claim(ctx, fid, jid, ctx.clock())
    if job is None:
        current = ctx.store.get(keys.family(fid), f"JOB#{jid}")
        if current is None:
            return "missing"
        if current["status"] in ("queued", "running") and int(current["attempt"]) >= MAX_CLAIMS:
            # 认领次数耗尽且仍未完成：标记失败（以当前 fencing 为条件）
            _finish_failed(ctx, {**current, "status": "running"}, "attempts_exhausted")
            return "failed"
        return str(current["status"])

    # 复核发起人当前身份与成员关系（ISE-011）：撤权后不调用模型
    actor_uid = job["actor_uid"]
    profile = ctx.store.get(keys.user(actor_uid), keys.PROFILE)
    member = ctx.store.get(keys.family(fid), keys.member(actor_uid))
    if (
        profile is None
        or profile.get("status") != "active"
        or member is None
        or member.get("status") != "active"
    ):
        _fail(ctx, job, "permission_revoked")
        return "failed"

    cfg = repo.family_config(ctx, fid)
    catalog = repo.catalog(ctx, fid)
    currencies = repo.family_currencies(ctx, fid)
    image = image_format = None
    if job.get("feishu", {}).get("image_key") and not job.get("attachment_id"):
        aid = _store_feishu_image(ctx, deps, job)
        if aid is None:
            _fail(ctx, job, "input_rejected")
            return "failed"
        job["attachment_id"] = aid
    if job.get("attachment_id"):
        att = ctx.store.get(keys.family(fid), f"ATT#{job['attachment_id']}")
        got = deps.blobs.read(att["s3_key"], 10 * 1024 * 1024) if att else None
        if att is None or att["status"] != "ready" or got is None:
            _fail(ctx, job, "input_rejected")
            return "failed"
        if getattr(deps.model, "wants_raw_image", False):
            image, image_format = got[0], att["content_type"].split("/")[1]
        else:
            image, image_format = prepare_image(got[0])
    options = tuple(
        CategoryOption(c.category_id, c.kind, catalog.get(c.parent_id).name, c.name)
        for c in sorted(catalog.by_id.values(), key=lambda x: (x.kind, x.parent_id or "", x.sort))
        if c.is_leaf and c.status == "active" and c.redirect_to is None and c.parent_id
    )
    request = ModelRequest(
        job.get("text"),
        image,
        image_format,
        options,
        tuple(sorted(currencies)),
        job["received_date"],
    )

    calls = int(job["model_calls"])
    parsed: dict[str, Any] | None = None
    last_error = "internal"
    outcome = dict(job.get("outcome") or {})
    while calls < MAX_MODEL_CALLS:
        if deps.clock() - started > WORKER_DEADLINE_S:
            last_error = "timeout"
            break
        calls += 1
        _bump_calls(ctx, job, calls)
        try:
            resp = deps.model.invoke(request, timeout_s=CALL_TIMEOUT_S)
        except ModelError as e:
            # 被拒绝的请求（限流、校验失败等）确定未计费，记 0；超时等可能已计费的记未知
            known = 0 if e.usage_known else None
            _record_usage(ctx, job, deps.model.model_id, known, known)
            last_error = e.error_class
            if e.retryable and calls < MAX_MODEL_CALLS:
                wait = RATE_LIMIT_BACKOFF_S if e.error_class == "rate_limited" else 0.5 * calls
                remaining = WORKER_DEADLINE_S - CALL_TIMEOUT_S - (deps.clock() - started)
                deps.sleep(max(0.0, min(wait, remaining)))
                continue
            break
        outcome["request_returned"] = True
        if resp.usage is None:
            _record_usage(ctx, job, deps.model.model_id, None, None)
        else:
            _record_usage(
                ctx, job, deps.model.model_id, resp.usage.input_tokens, resp.usage.output_tokens
            )
        try:
            parsed = parse(resp)
            outcome["schema_valid"] = True
        except ModelError as e:
            last_error = e.error_class
            break  # 格式错误不付费重试（ADR-0011）
        break

    if parsed is None:
        if calls >= MAX_MODEL_CALLS and last_error in (
            "rate_limited",
            "transport_error",
            "timeout",
        ):
            last_error = "attempts_exhausted" if last_error != "timeout" else "timeout"
        _fail(ctx, job, last_error, {"outcome": _ddb_map(outcome)})
        return "failed"

    for _ in range(2):
        drafts, issues = normalize(
            parsed,
            catalog=catalog,
            currencies=currencies,
            received=datetime.fromisoformat(job["received_date"]).date(),
            default_currency=cfg["default_currency"],
            default_method=cfg["default_payment_method"],
            max_major=ctx.limits.max_major,
        )
        outcome["semantic_valid"] = True
        if "before_publish" in deps.hooks:
            deps.hooks["before_publish"]()
        result = _publish(ctx, job, catalog.manifest_version, drafts, issues, outcome)
        if result != "manifest_changed":
            return result
        catalog = repo.catalog(ctx, fid)
    _fail(ctx, job, "semantic_invalid")
    return "failed"


def _store_feishu_image(ctx: AppContext, deps: WorkerDeps, job: dict[str, Any]) -> str | None:
    """经平台资源接口下载图片，按网站同样规则校验后存为照片凭证（ADR-0013 第 6 条）。"""
    from . import attachments

    fs = job["feishu"]
    if deps.feishu is None:
        return None
    try:
        data = deps.feishu.download_image(fs["message_id"], fs["image_key"])
    except Exception:
        return None
    sha = hashlib.sha256(data).hexdigest()
    reason, width, height = attachments._inspect(data, _sniff_type(data), sha)
    if reason:
        return None
    aid = "a" + str(job["job_id"])[1:]
    fid = str(job["family_id"])
    key = attachments.blob_key(fid, aid)
    version = deps.blobs.put(key, data, _sniff_type(data))
    now = ctx.clock()
    tx = Tx(ctx.store.table)
    tx.put(
        {
            "PK": keys.family(fid),
            "SK": f"ATT#{aid}",
            "type": "attachment",
            "attachment_id": aid,
            "family_id": fid,
            "uploader": job["actor_uid"],
            "s3_key": key,
            "s3_version_id": version,
            "content_type": _sniff_type(data),
            "bytes": len(data),
            "sha256": sha,
            "status": "ready",
            "width": width,
            "height": height,
            "ref_count": 0,
            "created_at": keys.ts(now),
            **attachments._orphan_gsi(aid, fid, now + timedelta(days=attachments.ORPHAN_DAYS)),
        },
        condition="attribute_not_exists(PK)",
    )
    try:
        ctx.store.commit(tx)
    except DomainError:
        pass  # 重复执行：照片已存在
    ctx.store.client.update_item(
        TableName=ctx.store.table,
        Key={"PK": {"S": job["PK"]}, "SK": {"S": job["SK"]}},
        UpdateExpression="SET attachment_id = :a",
        ConditionExpression="fencing_token = :tok",
        ExpressionAttributeValues={":a": {"S": aid}, ":tok": {"N": str(job["fencing_token"])}},
    )
    return aid


def _sniff_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _bump_calls(ctx: AppContext, job: dict[str, Any], calls: int) -> None:
    """调用前先持久化尝试计数，进程崩溃后不会超过 MAX_MODEL_CALLS。"""
    ctx.store.client.update_item(
        TableName=ctx.store.table,
        Key={"PK": {"S": job["PK"]}, "SK": {"S": job["SK"]}},
        UpdateExpression="SET model_calls = :c",
        ConditionExpression="fencing_token = :tok",
        ExpressionAttributeValues={
            ":c": {"N": str(calls)},
            ":tok": {"N": str(job["fencing_token"])},
        },
    )


def _ddb_map(d: dict[str, bool]) -> dict[str, Any]:
    return {"M": {k: {"BOOL": bool(v)} for k, v in d.items()}}


class _ManifestChanged(StaleRead):
    pass


class _PermissionChanged(StaleRead):
    pass


def _publish(
    ctx: AppContext,
    job: dict[str, Any],
    manifest: int,
    drafts: list[Draft],
    issues: list[str],
    outcome: dict[str, bool],
) -> str:
    from . import candidates

    fid, actor_uid = job["family_id"], job["actor_uid"]
    now = ctx.clock()
    bid = batch_id_for(job["job_id"])
    member = ctx.store.get(keys.family(fid), keys.member(actor_uid))
    profile = ctx.store.get(keys.user(actor_uid), keys.PROFILE)
    if member is None or profile is None or member.get("status") != "active":
        _fail(ctx, job, "permission_revoked")
        return "failed"
    tx = ctx.store.tx()
    # 发布时复核：发起人仍有效（ISE-011）、分类目录未变、本 Worker 仍持有租约（ISE-017）
    tx.check(
        keys.user(actor_uid),
        keys.PROFILE,
        "#s = :active AND session_epoch = :e",
        names={"#s": "status"},
        values={":active": "active", ":e": int(profile.get("session_epoch", 0))},
        on_fail=lambda _: _PermissionChanged("发起人状态已变化"),
    )
    tx.check(
        keys.family(fid),
        keys.member(actor_uid),
        "#s = :active AND version = :v",
        names={"#s": "status"},
        values={":active": "active", ":v": int(member["version"])},
        on_fail=lambda _: _PermissionChanged("成员关系已变化"),
    )
    tx.check(
        keys.family(fid),
        keys.CONFIG,
        "category_manifest_version = :m",
        values={":m": manifest},
        on_fail=lambda _: _ManifestChanged("分类目录已变化"),
    )
    outcome = {**outcome, "published": True}
    tx.update(
        keys.family(fid),
        job["SK"],
        "SET #s = :ready, batch_id = :b, outcome = :o REMOVE lease_until",
        condition="#s = :running AND fencing_token = :tok AND lease_until > :now",
        names={"#s": "status"},
        values={
            ":ready": "candidate_ready",
            ":b": bid,
            ":o": outcome,
            ":running": "running",
            ":tok": int(job["fencing_token"]),
            ":now": keys.ts(now),
        },
        on_fail=lambda _: DomainError("租约已失效", code="internal"),
    )
    tx.put_new(
        {
            "PK": keys.family(fid),
            "SK": f"BATCH#{bid}",
            "type": "batch",
            "batch_id": bid,
            "actor_uid": actor_uid,
            "source": job["source"],
            "job_id": job["job_id"],
            "status": "open",
            "version": 1,
            "input_issues": issues,
            "attachment_id": job.get("attachment_id"),
            "created_at": keys.ts(now),
            "expires_at": keys.ts(now + timedelta(days=BATCH_TTL_DAYS)),
            # 到期工作索引：维护任务据此清除过期候选正文（ADR-0008 第 4 条）
            "GSI1PK": keys.work("candidate_expiry", int(bid[-1], 36) % OUTBOX_SHARDS),
            "GSI1SK": f"{keys.ts(now + timedelta(days=BATCH_TTL_DAYS))}#{fid}#{bid}",
        }
    )
    receipt_group = ("g" + bid[1:]) if job.get("attachment_id") else None
    for i, d in enumerate(drafts[:10]):
        tx.put_new(
            candidates.new_candidate_item(
                ctx,
                fid,
                bid,
                f"{bid}c{i:02d}",
                d,
                manifest,
                job.get("attachment_id"),
                receipt_group,
            )
        )
    if job.get("feishu"):
        tx.put_new(
            feishu_outbox_item(
                fid, "feishu_card", f"card-{bid}", job["feishu"]["open_id"], now, batch_id=bid
            )
        )
    try:
        ctx.store.commit(tx)
    except _PermissionChanged:
        _fail(ctx, job, "permission_revoked")
        return "failed"
    except _ManifestChanged:
        return "manifest_changed"  # 调用方用最新目录重新校验后再发布（不再调用模型）
    except DomainError:
        return "stale"  # 旧 Worker 迟到或租约失效：不得发布
    return "candidate_ready"


def process_pending(ctx: AppContext, deps: WorkerDeps, only_family: str | None = None) -> int:
    """本地开发与测试：派发并同步执行到期任务。生产由 Streams→relay→SQS→Worker 完成。"""
    done: list[dict[str, str]] = []
    relay_once(ctx, done.append, only_family=only_family)
    for ref in done:
        run_job(ctx, deps, ref["family_id"], ref["job_id"])
    return len(done)


def stable_digest(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()

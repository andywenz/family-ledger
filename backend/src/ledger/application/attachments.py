"""照片凭证（需求 §8、架构 §2／§4.2／§8；OPS-04、AUTH-07／09、OPS-11）。

上传：意图（限定键、类型、大小）→ 浏览器直传 → 完成校验（真实格式、像素、摘要）→ ready。
引用：账目事务内对 ATT 项 ref_count 条件增减；未引用照片 7 天后清理，删除账目清理时一并检查。
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timedelta
from typing import Any

from PIL import Image, UnidentifiedImageError

from ledger.adapters.blobs import BlobStore
from ledger.adapters.dynamo import keys
from ledger.adapters.dynamo.store import Tx
from ledger.domain.authz import Actor, require_member
from ledger.domain.errors import DomainError, Forbidden, NotFound, StaleRead

from . import actions
from .actions import Built, Scope
from .context import AppContext
from .guard import guard_member, load_membership

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
UPLOAD_TTL_S = 600
DOWNLOAD_TTL_S = 60
ORPHAN_DAYS = 7
ORPHAN_SHARDS = 4
FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def blob_key(fid: str, aid: str) -> str:
    return f"families/{fid}/attachments/{aid}"


def _orphan_gsi(aid: str, fid: str, due: datetime) -> dict[str, str]:
    shard = int(hashlib.sha256(aid.encode()).hexdigest(), 16) % ORPHAN_SHARDS
    return {
        "GSI1PK": keys.work("attachment_orphan", shard),
        "GSI1SK": f"{keys.ts(due)}#{fid}#{aid}",
    }


def _view(item: dict[str, Any]) -> dict[str, Any]:
    out = {
        "attachment_id": item["attachment_id"],
        "status": item["status"],
        "content_type": item["content_type"],
        "bytes": int(item["bytes"]),
    }
    for k in ("width", "height", "reject_reason"):
        if item.get(k) is not None:
            out[k] = item[k]
    return out


def create_upload_intent(
    ctx: AppContext,
    blobs: BlobStore,
    actor: Actor,
    fid: str,
    key: str,
    *,
    content_type: str,
    size: int,
    sha256: str,
) -> dict[str, Any]:
    if content_type not in FORMATS:
        raise DomainError("仅支持 JPEG、PNG、WebP；HEIC 请改为 JPEG", code="unsupported_media_type")
    if not 0 < size <= MAX_BYTES:
        raise DomainError("照片不能超过 10 MB", code="upload_invalid")
    body = {"ct": content_type, "bytes": size, "sha256": sha256}

    def build() -> Built[dict[str, Any]]:
        m = require_member(load_membership(ctx, actor, fid))
        aid = ctx.ids()
        now = ctx.clock()
        item = {
            "PK": keys.family(fid),
            "SK": f"ATT#{aid}",
            "type": "attachment",
            "attachment_id": aid,
            "family_id": fid,
            "uploader": actor.user_id,
            "s3_key": blob_key(fid, aid),
            "content_type": content_type,
            "bytes": size,
            "sha256": sha256,
            "status": "pending",
            "ref_count": 0,
            "created_at": keys.ts(now),
            **_orphan_gsi(aid, fid, now + timedelta(days=ORPHAN_DAYS)),
        }
        tx = ctx.store.tx()
        guard_member(tx, actor, m)
        tx.put_new(item)
        return Built(
            tx,
            {"attachment_id": aid},
            [{"object_type": "attachment", "object_id": aid, "version": 1}],
        )

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        return {"attachment_id": r["results"][0]["object_id"]}

    out = actions.run(
        ctx, actor, Scope.family(fid, actor, key), key, "attachment.intent", body, build, replay
    )
    aid = out["attachment_id"]
    form = blobs.presign_upload(blob_key(fid, aid), content_type, size, UPLOAD_TTL_S)
    expires = ctx.clock() + timedelta(seconds=UPLOAD_TTL_S)
    return {
        "attachment_id": aid,
        "upload_url": form.url,
        "fields": form.fields,
        "expires_at": keys.ts(expires),
    }


def _inspect(data: bytes, declared_type: str, declared_sha: str) -> tuple[str | None, int, int]:
    """返回 (拒绝原因, 宽, 高)。先核对摘要与大小，再用 Pillow 读取真实格式与像素。"""
    if len(data) > MAX_BYTES:
        return "too_large", 0, 0
    if hashlib.sha256(data).hexdigest() != declared_sha:
        return "digest_mismatch", 0, 0
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # 超过即 DecompressionBombError（防压缩炸弹）
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            width, height = img.size
            if width * height > MAX_PIXELS:
                return "too_many_pixels", width, height
            img.verify()
    except Image.DecompressionBombError:
        return "too_many_pixels", 0, 0
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return "unreadable", 0, 0
    if fmt != FORMATS[declared_type]:
        return "format_mismatch", width, height
    return None, width, height


def complete_upload(
    ctx: AppContext, blobs: BlobStore, actor: Actor, fid: str, aid: str, key: str
) -> dict[str, Any]:
    def build() -> Built[dict[str, Any]]:
        m = require_member(load_membership(ctx, actor, fid))
        item = ctx.store.get(keys.family(fid), f"ATT#{aid}")
        if item is None:
            raise NotFound("照片不存在")
        if item["uploader"] != actor.user_id:
            raise Forbidden("只能确认自己上传的照片")
        if item["status"] != "pending":
            return Built(
                ctx.store.tx(), _view(item), [{"object_type": "attachment", "object_id": aid}]
            )
        try:
            got = blobs.read(item["s3_key"], MAX_BYTES)
        except ValueError:
            got = None
            reason: str | None = "too_large"
            width = height = 0
            version = ""
        else:
            if got is None:
                raise DomainError("尚未收到上传的文件", code="upload_invalid")
            reason, width, height = _inspect(got[0], item["content_type"], item["sha256"])
            version = got[1]
        status = "rejected" if reason else "ready"
        tx = ctx.store.tx()
        guard_member(tx, actor, m)
        values: dict[str, Any] = {
            ":s": status,
            ":w": width,
            ":h": height,
            ":v": version,
            ":pending": "pending",
        }
        expr = "SET #s = :s, width = :w, height = :h, s3_version_id = :v"
        if reason:
            expr += ", reject_reason = :r"
            values[":r"] = reason
        tx.update(
            keys.family(fid),
            f"ATT#{aid}",
            expr,
            condition="#s = :pending",
            names={"#s": "status"},
            values=values,
            on_fail=lambda _: StaleRead("照片状态已变化"),
        )
        view = {**_view(item), "status": status, "width": width, "height": height}
        if reason:
            view["reject_reason"] = reason
        return Built(tx, view, [{"object_type": "attachment", "object_id": aid}])

    def replay(r: dict[str, Any]) -> dict[str, Any]:
        item = ctx.store.get(keys.family(fid), f"ATT#{aid}")
        assert item is not None
        return _view(item)

    return actions.run(
        ctx,
        actor,
        Scope.family(fid, actor, key),
        key,
        "attachment.complete",
        {"aid": aid},
        build,
        replay,
    )


def download_url(
    ctx: AppContext, blobs: BlobStore, actor: Actor, fid: str, aid: str
) -> dict[str, Any]:
    """每次签发前检查当前成员关系；链接 60 秒有效（AUTH-09：不承诺撤回已签发链接）。"""
    require_member(load_membership(ctx, actor, fid))
    item = ctx.store.get(keys.family(fid), f"ATT#{aid}")
    if item is None or item["status"] != "ready":
        raise NotFound("照片不存在")
    url = blobs.presign_download(item["s3_key"], item["s3_version_id"], DOWNLOAD_TTL_S)
    return {"url": url, "expires_at": keys.ts(ctx.clock() + timedelta(seconds=DOWNLOAD_TTL_S))}


# ── 账目引用（在账目事务中调用） ──


def apply_refs(
    ctx: AppContext,
    tx: Tx,
    fid: str,
    before: tuple[str, ...],
    after: tuple[str, ...],
    now: datetime,
    *,
    orphan_delay: timedelta = timedelta(days=ORPHAN_DAYS),
) -> None:
    """按新旧引用集合增减 ref_count；新增引用必须是本家庭 ready 照片。"""
    for aid in sorted(set(after) - set(before)):
        item = ctx.store.get(keys.family(fid), f"ATT#{aid}")
        if item is None or item["status"] != "ready":
            raise DomainError("照片不存在或尚未通过校验", code="upload_invalid")
        tx.update(
            keys.family(fid),
            f"ATT#{aid}",
            "SET ref_count = ref_count + :one REMOVE GSI1PK, GSI1SK",
            condition="#s = :ready AND ref_count = :rc",
            names={"#s": "status"},
            values={":one": 1, ":ready": "ready", ":rc": int(item["ref_count"])},
            on_fail=lambda _: StaleRead("照片引用已变化"),
        )
    for aid in sorted(set(before) - set(after)):
        item = ctx.store.get(keys.family(fid), f"ATT#{aid}")
        if item is None:
            continue
        rc = int(item["ref_count"])
        if rc <= 1:
            g = _orphan_gsi(aid, fid, now + orphan_delay)
            tx.update(
                keys.family(fid),
                f"ATT#{aid}",
                "SET ref_count = :zero, GSI1PK = :pk, GSI1SK = :sk",
                condition="ref_count = :rc",
                values={":zero": 0, ":pk": g["GSI1PK"], ":sk": g["GSI1SK"], ":rc": rc},
                on_fail=lambda _: StaleRead("照片引用已变化"),
            )
        else:
            tx.update(
                keys.family(fid),
                f"ATT#{aid}",
                "SET ref_count = ref_count - :one",
                condition="ref_count = :rc",
                values={":one": 1, ":rc": rc},
                on_fail=lambda _: StaleRead("照片引用已变化"),
            )


def purge_orphans(ctx: AppContext, blobs: BlobStore, now: datetime | None = None) -> int:
    """清理 7 天未被引用的照片：删除全部对象版本，ATT 项标记 deleted（OPS-11）。"""
    now = now or ctx.clock()
    n = 0
    for shard in range(ORPHAN_SHARDS):
        r = ctx.store.client.query(
            TableName=ctx.store.table,
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND GSI1SK <= :now",
            ExpressionAttributeValues={
                ":pk": {"S": keys.work("attachment_orphan", shard)},
                ":now": {"S": keys.ts(now) + "~"},
            },
        )
        for raw in r.get("Items", []):
            item = ctx.store.get(raw["PK"]["S"], raw["SK"]["S"])
            if item is None or int(item.get("ref_count", 0)) > 0 or item["status"] == "deleted":
                continue
            pk, sk = raw["PK"]["S"], raw["SK"]["S"]
            if item["status"] != "deleting":
                # 先标记 deleting（新引用要求 ready，因此不会再被关联），保留 GSI 以便失败后重试
                tx = ctx.store.tx()
                tx.update(
                    pk,
                    sk,
                    "SET #s = :d",
                    condition="ref_count = :zero AND GSI1SK = :sk",
                    names={"#s": "status"},
                    values={":d": "deleting", ":zero": 0, ":sk": item["GSI1SK"]},
                )
                try:
                    ctx.store.commit(tx)
                except DomainError:
                    continue  # 期间被引用：保留
            blobs.delete_all_versions(item["s3_key"])
            tx = ctx.store.tx()
            tx.update(
                pk,
                sk,
                "SET #s = :d REMOVE GSI1PK, GSI1SK",
                names={"#s": "status"},
                values={":d": "deleted"},
            )
            ctx.store.commit(tx)
            n += 1
    return n

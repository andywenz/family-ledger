"""对象存储接口与 S3 实现（私有桶，版本化；架构 §2、§8）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class UploadForm:
    url: str
    fields: dict[str, str]


class BlobStore(Protocol):
    def presign_upload(
        self, key: str, content_type: str, max_bytes: int, expires_s: int
    ) -> UploadForm: ...

    def read(self, key: str, max_bytes: int) -> tuple[bytes, str] | None:
        """返回 (内容, version_id)；不存在返回 None；超过 max_bytes 抛 ValueError。"""
        ...

    def put(self, key: str, data: bytes, content_type: str) -> str: ...

    def presign_download(
        self, key: str, version_id: str, expires_s: int, filename: str | None = None
    ) -> str: ...

    def delete_all_versions(self, key: str) -> int: ...


class S3BlobStore:  # pragma: no cover - 真实 S3 在 D5 经授权验收
    def __init__(self, client: Any, bucket: str) -> None:
        self.c = client
        self.bucket = bucket

    def presign_upload(
        self, key: str, content_type: str, max_bytes: int, expires_s: int
    ) -> UploadForm:
        out = self.c.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, max_bytes],
                {"key": key},
            ],
            ExpiresIn=expires_s,
        )
        return UploadForm(out["url"], dict(out["fields"]))

    def read(self, key: str, max_bytes: int) -> tuple[bytes, str] | None:
        try:
            head = self.c.head_object(Bucket=self.bucket, Key=key)
        except self.c.exceptions.ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return None
            raise
        if int(head["ContentLength"]) > max_bytes:
            raise ValueError("对象过大")
        version = str(head.get("VersionId") or "")
        args = {"Bucket": self.bucket, "Key": key} | ({"VersionId": version} if version else {})
        obj = self.c.get_object(**args)
        return obj["Body"].read(max_bytes + 1), version

    def put(self, key: str, data: bytes, content_type: str) -> str:
        """返回版本 ID；未开启版本控制的桶（导出桶）返回空字符串（2026-10-05 生产发现）。"""
        out = self.c.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return str(out.get("VersionId") or "")

    def presign_download(
        self, key: str, version_id: str, expires_s: int, filename: str | None = None
    ) -> str:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if version_id:  # 照片桶按版本签名；导出桶无版本
            params["VersionId"] = version_id
        if filename:
            params["ResponseContentDisposition"] = f"attachment; filename*=UTF-8''{filename}"
        return str(self.c.generate_presigned_url("get_object", Params=params, ExpiresIn=expires_s))

    def delete_all_versions(self, key: str) -> int:
        """删除所有版本与删除标记（delete marker 不等于永久删除，架构 §8）。"""
        n = 0
        pages = self.c.get_paginator("list_object_versions").paginate(
            Bucket=self.bucket, Prefix=key
        )
        for page in pages:
            objs = [
                {"Key": v["Key"], "VersionId": v["VersionId"]}
                for v in page.get("Versions", []) + page.get("DeleteMarkers", [])
                if v["Key"] == key
            ]
            if objs:
                self.c.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})
                n += len(objs)
        return n

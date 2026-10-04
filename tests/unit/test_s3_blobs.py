"""S3 适配器：版本控制开启与否（离线，botocore Stubber；不代表真实 S3 行为的全部细节）。"""

from __future__ import annotations

import boto3
from botocore.stub import ANY, Stubber

from ledger.adapters.blobs import S3BlobStore


def _store() -> tuple[S3BlobStore, Stubber]:
    c = boto3.client(
        "s3", region_name="ap-southeast-2", aws_access_key_id="x", aws_secret_access_key="x"
    )  # noqa: S106
    return S3BlobStore(c, "exports-bucket"), Stubber(c)


def test_put_without_versioning_returns_empty_and_presigns_without_version() -> None:
    """导出桶未开启版本控制：put 不返回 VersionId 时不能报错（生产曾因此导出 500）。"""
    store, st = _store()
    st.add_response(
        "put_object",
        {"ETag": '"e"'},
        {"Bucket": "exports-bucket", "Key": "k", "Body": ANY, "ContentType": "text/csv"},
    )
    with st:
        assert store.put("k", b"a,b", "text/csv") == ""
    url = store.presign_download("k", "", 300, "x.csv")
    assert "versionId" not in url and "exports-bucket" in url


def test_put_with_versioning_keeps_version() -> None:
    store, st = _store()
    st.add_response(
        "put_object",
        {"ETag": '"e"', "VersionId": "v1"},
        {"Bucket": "exports-bucket", "Key": "k", "Body": ANY, "ContentType": "image/jpeg"},
    )
    with st:
        assert store.put("k", b"x", "image/jpeg") == "v1"
    assert "versionId=v1" in store.presign_download("k", "v1", 300)

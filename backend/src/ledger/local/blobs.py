"""本地对象存储替身：文件目录＋HMAC 签名的上传表单与下载链接（模拟 S3 预签名语义）。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ledger.adapters.blobs import UploadForm

_STORES: dict[str, LocalBlobStore] = {}


def _b64(d: bytes) -> str:
    return urlsafe_b64encode(d).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return urlsafe_b64decode(s + "=" * (-len(s) % 4))


class LocalBlobStore:
    def __init__(self, root: Path, secret: bytes, base_url: str, name: str = "default") -> None:
        self.root = root
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self.name = name
        _STORES[name] = self

    def _sign(self, payload: dict[str, object]) -> str:
        body = _b64(json.dumps(payload, sort_keys=True).encode())
        sig = hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def verify(self, token: str) -> dict[str, object]:
        body, _, sig = token.rpartition(".")
        if not hmac.compare_digest(
            sig, hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
        ):
            raise PermissionError("签名无效")
        data: dict[str, object] = json.loads(_unb64(body))
        if float(str(data["exp"])) < time.time():
            raise PermissionError("链接已过期")
        return data

    def _dir(self, key: str) -> Path:
        if ".." in key or key.startswith("/"):
            raise ValueError("非法对象键")
        return self.root / key

    def presign_upload(
        self, key: str, content_type: str, max_bytes: int, expires_s: int
    ) -> UploadForm:
        policy = self._sign(
            {
                "k": key,
                "ct": content_type,
                "max": max_bytes,
                "exp": time.time() + expires_s,
                "s": self.name,
            }
        )
        return UploadForm(
            f"{self.base_url}/local-blobs/upload",
            {"key": key, "Content-Type": content_type, "policy": policy},
        )

    def store_upload(self, key: str, data: bytes) -> str:
        version = f"v{time.time_ns()}{secrets.token_hex(2)}"
        d = self._dir(key)
        d.mkdir(parents=True, exist_ok=True)
        (d / version).write_bytes(data)
        return version

    def read(self, key: str, max_bytes: int) -> tuple[bytes, str] | None:
        d = self._dir(key)
        if not d.exists():
            return None
        versions = sorted(p.name for p in d.iterdir())
        if not versions:
            return None
        path = d / versions[-1]
        if path.stat().st_size > max_bytes:
            raise ValueError("对象过大")
        return path.read_bytes(), versions[-1]

    def put(self, key: str, data: bytes, content_type: str) -> str:
        return self.store_upload(key, data)

    def presign_download(
        self, key: str, version_id: str, expires_s: int, filename: str | None = None
    ) -> str:
        token = self._sign(
            {
                "k": key,
                "v": version_id,
                "exp": time.time() + expires_s,
                "f": filename or "",
                "s": self.name,
            }
        )
        return f"{self.base_url}/local-blobs/get?t={token}"

    def delete_all_versions(self, key: str) -> int:
        d = self._dir(key)
        if not d.exists():
            return 0
        n = 0
        for p in d.iterdir():
            p.unlink()
            n += 1
        d.rmdir()
        return n


async def upload(request: Request) -> Response:
    form = await request.form()
    policy = str(form.get("policy", ""))
    try:
        store = next(iter(_STORES.values()))
        data = store.verify(policy)
        store = _STORES[str(data["s"])]
    except (PermissionError, StopIteration, KeyError, ValueError):
        return Response("签名无效或已过期", status_code=403)
    if form.get("key") != data["k"] or form.get("Content-Type") != data["ct"]:
        return Response("表单字段与签名不符", status_code=403)
    file = form.get("file")
    if file is None or isinstance(file, str):
        return Response("缺少文件", status_code=400)
    content = await file.read(int(str(data["max"])) + 1)
    if not content or len(content) > int(str(data["max"])):
        return Response("文件大小不符", status_code=400)
    store.store_upload(str(data["k"]), content)
    return Response(status_code=204)


async def download(request: Request) -> Response:
    try:
        store = next(iter(_STORES.values()))
        data = store.verify(request.query_params.get("t", ""))
        store = _STORES[str(data["s"])]
    except (PermissionError, StopIteration, KeyError, ValueError):
        return Response("链接无效或已过期", status_code=403)
    path = store._dir(str(data["k"])) / str(data["v"])
    if not path.exists():
        return Response(status_code=404)
    headers = {"cache-control": "private, no-store"}
    if data.get("f"):
        headers["content-disposition"] = f"attachment; filename*=UTF-8''{data['f']}"
    return Response(path.read_bytes(), headers=headers)


# 由 server.create_app 直接读取，不经 server 模块的全局列表：
# 以 python -m 启动时 server 会被加载两份，登记到另一份里导致本地上传／下载 404（2026-10-05 发现）
ROUTES: list[Route] = []
ROUTES.extend(
    [
        Route("/local-blobs/upload", upload, methods=["POST"]),
        Route("/local-blobs/get", download, methods=["GET"]),
    ]
)

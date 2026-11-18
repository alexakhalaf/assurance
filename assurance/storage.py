"""Content-addressed object storage for raw command output and full Genie results.

Objects are gzip-compressed and keyed by the SHA-256 of their content, so identical outputs across
snapshots (most of the fleet, most of the time) are stored once.
"""
from __future__ import annotations

import gzip
import hashlib
import logging
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from .config import get_settings

log = logging.getLogger(__name__)


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def ping(self) -> None: ...
    def delete(self, key: str) -> None: ...
    def keys(self, prefix: str) -> Iterator[tuple[str, float]]: ...  # (key, last modified epoch)


class FilesystemStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("invalid object key")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def ping(self) -> None:
        if not self.root.is_dir():
            raise RuntimeError(f"object store path {self.root} missing")

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def keys(self, prefix: str) -> Iterator[tuple[str, float]]:
        base = self.root / prefix
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file() and not p.name.endswith(".tmp"):
                    yield p.relative_to(self.root).as_posix(), p.stat().st_mtime


class S3Store:
    def __init__(self, bucket: str, endpoint_url: str | None, access_key: str | None,
                 secret_key: str | None, region: str):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3", endpoint_url=endpoint_url, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, region_name=region,
            config=Config(retries={"max_attempts": 5, "mode": "standard"}, signature_version="s3v4"),
        )
        self._bucket_checked = False

    def _ensure_bucket(self) -> None:
        if self._bucket_checked:
            return
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            log.info("creating bucket %s", self.bucket)
            self.client.create_bucket(Bucket=self.bucket)
        self._bucket_checked = True

    def put(self, key: str, data: bytes) -> None:
        self._ensure_bucket()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def ping(self) -> None:
        self._ensure_bucket()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def keys(self, prefix: str) -> Iterator[tuple[str, float]]:
        self._ensure_bucket()
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix + "/"):
            for o in page.get("Contents", []):
                yield o["Key"], o["LastModified"].timestamp()


@lru_cache
def get_store() -> ObjectStore:
    s = get_settings()
    if s.object_store == "filesystem":
        return FilesystemStore(s.object_store_path)
    return S3Store(s.s3_bucket, s.s3_endpoint_url, s.s3_access_key, s.s3_secret_key, s.s3_region)


def put_text(text: str, namespace: str = "raw") -> tuple[str, str, int]:
    """Store text; returns (object_key, sha256, size)."""
    data = text.encode()
    digest = hashlib.sha256(data).hexdigest()
    key = f"{namespace}/{digest[:2]}/{digest}.gz"
    store = get_store()
    if not store.exists(key):
        store.put(key, gzip.compress(data, mtime=0))
    return key, digest, len(data)


NAMESPACES = ("raw", "genie")


def get_text(key: str) -> str:
    return gzip.decompress(get_store().get(key)).decode()

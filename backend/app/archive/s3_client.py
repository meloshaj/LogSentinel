"""Provider-neutral S3 client for Hot/Cold Storage Architecture."""

import abc
import asyncio
import functools
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import BinaryIO

from botocore.config import Config
from botocore.exceptions import ClientError

from ..core.settings import get_archive_settings

_IO_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="storage-io")
_IO_SLOTS = threading.BoundedSemaphore(4)


async def run_storage_io(function, *args, **kwargs):
    """Bound running AND queued work; cancellation never frees a running slot."""
    while not _IO_SLOTS.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        future = _IO_POOL.submit(functools.partial(function, *args, **kwargs))
    except BaseException:
        _IO_SLOTS.release()
        raise
    future.add_done_callback(lambda _: _IO_SLOTS.release())
    return await asyncio.shield(asyncio.wrap_future(future))


def read_object(client, key, maximum_bytes):
    stream = client.get_stream(key)
    if stream is None:
        raise FileNotFoundError(key)
    try:
        chunks = []
        size = 0
        while True:
            chunk = stream.read(min(1024 * 1024, maximum_bytes - size + 1))
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > maximum_bytes:
                raise ValueError("object exceeds byte limit")
            chunks.append(chunk)
    finally:
        stream.close()


class S3StorageClient(abc.ABC):
    @abc.abstractmethod
    def put_if_absent(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> bool:
        """Uploads data if the object key does not already exist."""

    @abc.abstractmethod
    def head(self, object_key: str) -> dict | None:
        """Returns metadata for the object, or None if it doesn't exist."""

    @abc.abstractmethod
    def get_stream(self, object_key: str) -> BinaryIO | None:
        """Returns a readable binary stream for the object, or None if it doesn't exist."""

    @abc.abstractmethod
    def delete(self, object_key: str) -> bool:
        """Deletes the object."""


class Boto3StorageClient(S3StorageClient):
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None,
        region: str,
        access_key: str | None,
        secret_key: str | None,
    ):
        import boto3

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                connect_timeout=5,
                read_timeout=15,
                retries={"mode": "standard", "total_max_attempts": 3},
                max_pool_connections=4,
            ),
        )

    def put_if_absent(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> bool:
        for attempt in range(3):
            try:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=object_key,
                    Body=data,
                    ContentType=content_type,
                    ServerSideEncryption="AES256",
                    IfNoneMatch="*",
                )
                return True
            except ClientError as e:
                code = str(e.response["Error"]["Code"])
                if code in {"412", "PreconditionFailed"}:
                    return False
                if code in {"409", "ConditionalRequestConflict"} and attempt < 2:
                    time.sleep(0.05 * (2**attempt))
                    continue
                raise
        raise RuntimeError("S3 put_if_absent exhausted without a result")

    def head(self, object_key: str) -> dict | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=object_key)
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return None
            raise

    def get_stream(self, object_key: str) -> BinaryIO | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=object_key)
            return response["Body"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise

    def delete(self, object_key: str) -> bool:
        self.client.delete_object(Bucket=self.bucket, Key=object_key)
        return True


class LocalMockStorageClient(S3StorageClient):
    """Local filesystem mockup for isolated tests without AWS dependencies."""

    def __init__(self, base_dir: str | None = None):
        requested_dir = base_dir or os.path.join(
            tempfile.gettempdir(), "logsentinel_archive_mock"
        )
        # A number of legacy tests use the POSIX ``/tmp`` spelling.  Map that
        # spelling to the host temp directory on Windows so the mock remains
        # provider-neutral and does not attempt to create a root-level path.
        if os.name == "nt" and requested_dir.replace("\\", "/").startswith("/tmp/"):
            requested_dir = os.path.join(
                tempfile.gettempdir(), requested_dir.replace("\\", "/")[5:]
            )
        self.base_dir = requested_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self.lock = threading.Lock()

    def _get_path(self, object_key: str) -> str:
        return os.path.join(self.base_dir, object_key.replace("/", "_"))

    def put_if_absent(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> bool:
        path = self._get_path(object_key)
        with self.lock:
            try:
                with open(path, "xb") as f:
                    f.write(data)
            except FileExistsError:
                return False
            return True

    def head(self, object_key: str) -> dict | None:
        path = self._get_path(object_key)
        with self.lock:
            if os.path.exists(path):
                return {"ContentLength": os.path.getsize(path)}
            return None

    def get_stream(self, object_key: str) -> BinaryIO | None:
        path = self._get_path(object_key)
        with self.lock:
            if not os.path.exists(path):
                return None
            return open(path, "rb")

    def delete(self, object_key: str) -> bool:
        path = self._get_path(object_key)
        with self.lock:
            if os.path.exists(path):
                os.remove(path)
                return True
            return False


def get_s3_client() -> S3StorageClient:
    """Factory method to get the correct S3 client based on environment."""
    if os.getenv("USE_MOCK_S3", "false").lower() == "true":
        return LocalMockStorageClient()

    settings = get_archive_settings()
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    if not settings.s3_access_key_id or not settings.s3_secret_access_key:
        if environment in {"development", "test"}:
            # Keep local imports/startup deterministic and never let boto3
            # probe instance metadata when cloud credentials are absent.
            return LocalMockStorageClient()
        raise RuntimeError(
            "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY are required outside "
            "development/test environments"
        )
    return Boto3StorageClient(
        bucket=settings.s3_bucket_name,
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key=settings.s3_access_key_id,
        secret_key=settings.s3_secret_access_key,
    )

"""Provider-neutral S3 client for Hot/Cold Storage Architecture."""

import abc
import os
import threading
from typing import BinaryIO

from botocore.exceptions import ClientError

from backend.app.core.settings import get_archive_settings


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
        )

    def put_if_absent(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=object_key)
            return False  # Already exists
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=object_key,
                    Body=data,
                    ContentType=content_type,
                )
                return True
            raise

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

    def __init__(self, base_dir: str = "/tmp/logsentinel_archive_mock"):
        self.base_dir = base_dir
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
            if os.path.exists(path):
                return False
            with open(path, "wb") as f:
                f.write(data)
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
    return Boto3StorageClient(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
    )

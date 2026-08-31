"""Verifier for Hot/Cold Storage Architecture."""

import hashlib
import io
import logging

import pyarrow.parquet as pq

from .s3_client import S3StorageClient

logger = logging.getLogger("logsentinel.archive.verifier")


class ArchiveVerifier:
    def __init__(self, s3_client: S3StorageClient):
        self.s3_client = s3_client

    def verify_archive(self, manifest_record: dict) -> bool:
        """Verifies a stored archive against the manifest record."""
        object_key = manifest_record["object_key"]
        expected_sha256 = manifest_record.get("sha256")
        expected_row_count = manifest_record.get("row_count")

        stream = self.s3_client.get_stream(object_key)
        if not stream:
            logger.error(
                "Archive verification failed: Object %s not found in S3", object_key
            )
            return False

        raw_bytes = stream.read()

        # Verify checksum
        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            logger.error(
                "Archive verification failed: Checksum mismatch for %s. Expected %s, got %s",
                object_key,
                expected_sha256,
                actual_sha256,
            )
            return False

        # Verify row count via Parquet footer
        try:
            buf = io.BytesIO(raw_bytes)
            parquet_file = pq.ParquetFile(buf)
            actual_row_count = parquet_file.metadata.num_rows
        except Exception as e:
            logger.error(
                "Archive verification failed: Could not parse Parquet file %s: %s",
                object_key,
                e,
            )
            return False

        if actual_row_count != expected_row_count:
            logger.error(
                "Archive verification failed: Row count mismatch for %s. Expected %s, got %s",
                object_key,
                expected_row_count,
                actual_row_count,
            )
            return False

        return True

    async def async_verify_archive(self, manifest_record: dict) -> bool:
        """Asynchronous wrapper for verify_archive using asyncio.to_thread."""
        import asyncio

        return await asyncio.to_thread(self.verify_archive, manifest_record)

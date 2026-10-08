"""Verifier for Hot/Cold Storage Architecture."""

import hashlib
import logging
import tempfile

import pyarrow.parquet as pq

from ..core.settings import get_archive_settings
from .s3_client import S3StorageClient, run_storage_io

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

        configured_maximum = get_archive_settings().max_archive_bytes
        declared_size = manifest_record.get("object_size") or manifest_record.get(
            "compressed_bytes"
        )
        maximum_bytes = min(
            configured_maximum,
            int(declared_size) if declared_size else configured_maximum,
        )
        try:
            digest = hashlib.sha256()
            total_bytes = 0
            with tempfile.SpooledTemporaryFile(
                max_size=8 * 1024 * 1024, mode="w+b"
            ) as temp:
                while True:
                    chunk = stream.read(
                        min(1024 * 1024, maximum_bytes - total_bytes + 1)
                    )
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > maximum_bytes:
                        logger.error(
                            "Archive verification failed: object exceeds configured byte limit for %s",
                            object_key,
                        )
                        return False
                    digest.update(chunk)
                    temp.write(chunk)

                actual_sha256 = digest.hexdigest()
                if actual_sha256 != expected_sha256:
                    logger.error(
                        "Archive verification failed: Checksum mismatch for %s",
                        object_key,
                    )
                    return False

                temp.seek(0)
                parquet_file = pq.ParquetFile(temp)
                actual_row_count = parquet_file.metadata.num_rows
        except Exception as e:
            logger.error(
                "Archive verification failed: Could not parse Parquet file %s: %s",
                object_key,
                type(e).__name__,
            )
            return False
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

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
        return await run_storage_io(self.verify_archive, manifest_record)

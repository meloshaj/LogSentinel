"""Centralized limits and bounded decoding for untrusted ingestion payloads."""

from __future__ import annotations

import os
import zlib

from fastapi import HTTPException, Request, status


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


MAX_HTTP_BODY_BYTES = _positive_int("MAX_HTTP_BODY_BYTES", 10 * 1024 * 1024)
MAX_COMPRESSED_BODY_BYTES = _positive_int("MAX_COMPRESSED_BODY_BYTES", 10 * 1024 * 1024)
MAX_DECOMPRESSED_BODY_BYTES = _positive_int(
    "MAX_DECOMPRESSED_BODY_BYTES", 50 * 1024 * 1024
)
MAX_OTLP_BODY_BYTES = _positive_int("MAX_OTLP_BODY_BYTES", 10 * 1024 * 1024)
MAX_RECORDS_PER_BATCH = _positive_int("MAX_RECORDS_PER_BATCH", 5000)
MAX_LINE_LENGTH = _positive_int("MAX_LINE_LENGTH", 1024 * 1024)
MAX_METADATA_DEPTH = _positive_int("MAX_METADATA_DEPTH", 16)
MAX_STRING_LENGTH = _positive_int("MAX_STRING_LENGTH", 65536)


def validate_bounded_structure(
    value: object,
    *,
    max_depth: int = MAX_METADATA_DEPTH,
    max_string_length: int = MAX_STRING_LENGTH,
    _depth: int = 0,
) -> None:
    """Reject recursively oversized JSON/OTLP structures before model parsing.

    Pydantic validates types, but it does not by itself bound arbitrary nested
    dictionaries or strings.  This guard runs on the decoded representation so
    both JSON and protobuf OTLP requests have the same resource limits.
    """
    if _depth > max_depth:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Metadata nesting exceeds configured limit",
        )
    if isinstance(value, str):
        if len(value) > max_string_length:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="String value exceeds configured limit",
            )
        return
    if isinstance(value, dict):
        for key, child in value.items():
            validate_bounded_structure(
                key,
                max_depth=max_depth,
                max_string_length=max_string_length,
                _depth=_depth + 1,
            )
            validate_bounded_structure(
                child,
                max_depth=max_depth,
                max_string_length=max_string_length,
                _depth=_depth + 1,
            )
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            validate_bounded_structure(
                child,
                max_depth=max_depth,
                max_string_length=max_string_length,
                _depth=_depth + 1,
            )


async def read_limited_body(
    request: Request,
    *,
    maximum_bytes: int = MAX_HTTP_BODY_BYTES,
) -> bytes:
    """Read a request body with a hard compressed/raw byte limit."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > maximum_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Payload too large",
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid Content-Length"
            ) from exc

    body = await request.body()
    if len(body) > maximum_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload too large",
        )
    return body


def bounded_gzip_decompress(
    body: bytes,
    *,
    maximum_bytes: int = MAX_DECOMPRESSED_BODY_BYTES,
    maximum_compressed_bytes: int = MAX_COMPRESSED_BODY_BYTES,
) -> bytes:
    """Decompress gzip data without allowing decompressed-size expansion."""
    if len(body) > maximum_compressed_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Compressed payload too large",
        )
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    output = bytearray()
    try:
        for offset in range(0, len(body), 64 * 1024):
            chunk = decoder.decompress(
                body[offset : offset + 64 * 1024], maximum_bytes - len(output) + 1
            )
            output.extend(chunk)
            if len(output) > maximum_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Decompressed payload too large",
                )
        output.extend(decoder.flush(maximum_bytes - len(output) + 1))
    except HTTPException:
        raise
    except zlib.error as exc:
        raise HTTPException(status_code=400, detail="Invalid gzip payload") from exc
    if len(output) > maximum_bytes or not decoder.eof:
        raise HTTPException(status_code=400, detail="Invalid or oversized gzip payload")
    return bytes(output)

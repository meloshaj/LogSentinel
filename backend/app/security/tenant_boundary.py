"""Tenant trust-boundary helpers shared by ingestion and workers."""

from __future__ import annotations

import re
from typing import Any


TENANT_SHAPED_KEYS = frozenset(
    {
        "tenant",
        "tenant_id",
        "organization",
        "organization_id",
        "workspace",
        "workspace_id",
        "owner",
        "owner_id",
        "owner_user_id",
        "user_id",
    }
)


class UntrustedTenantMetadataError(ValueError):
    """Raised when a user payload attempts to supply tenant authority."""


class TenantBoundaryViolation(ValueError):
    """Raised when a downstream object disagrees with trusted transport state."""


def _canonical_key(key: Any) -> str:
    value = str(key).strip()
    value = re.sub(r"(?<!^)(?=[A-Z])", "_", value)
    return value.replace("-", "_").casefold()


def reject_untrusted_tenant_fields(value: Any, *, path: str = "payload") -> None:
    """Reject tenant- or owner-shaped keys in an untrusted ingestion payload.

    Rejection is the documented ingestion contract.  It is deliberately
    recursive so a nested ``metadata.tenantId`` cannot become authority in a
    later adapter or worker.
    """

    if isinstance(value, dict):
        for key, child in value.items():
            canonical = _canonical_key(key)
            if canonical in TENANT_SHAPED_KEYS:
                raise UntrustedTenantMetadataError(
                    f"untrusted tenant metadata is not accepted at {path}.{key}"
                )
            reject_untrusted_tenant_fields(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            reject_untrusted_tenant_fields(child, path=f"{path}[{index}]")


def assert_tenant_identity(
    trusted_tenant_id: str,
    actual_tenant_id: str,
    *,
    boundary: str,
) -> None:
    """Fail closed when an object crosses a persistence/publication boundary."""

    trusted = str(trusted_tenant_id or "").strip()
    actual = str(actual_tenant_id or "").strip()
    if not trusted or trusted == "default" or actual != trusted:
        raise TenantBoundaryViolation(f"tenant identity mismatch at {boundary}")

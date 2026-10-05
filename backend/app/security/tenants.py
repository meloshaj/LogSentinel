"""Database authority for tenant provisioning and membership."""

from __future__ import annotations

import os
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from ..core.orm import ProviderTenantMappingRecord, TenantMembershipRecord, TenantRecord


async def resolve_membership(db, user):
    """Resolve the current membership for the user's authoritative tenant."""
    tenant_id = getattr(user, "tenant_id", None)
    user_id = getattr(user, "id", None)
    if (
        not tenant_id
        or tenant_id == "default"
        or user_id is None
        or user.status != "active"
    ):
        raise HTTPException(403, "inactive_tenant_membership")

    tenant = await db.scalar(select(TenantRecord).where(TenantRecord.id == tenant_id))
    member = await db.scalar(
        select(TenantMembershipRecord).where(
            TenantMembershipRecord.tenant_id == tenant_id,
            TenantMembershipRecord.user_id == user_id,
        )
    )
    if (
        tenant is None
        or tenant.status != "active"
        or member is None
        or member.status != "active"
        or member.role not in {"viewer", "operator", "admin"}
    ):
        raise HTTPException(403, "inactive_tenant_membership")
    return member


def _unmapped_provider_policy() -> str:
    """Return the explicit policy for providers without verified tenant data."""
    policy = os.getenv("SSO_UNMAPPED_PROVIDER_POLICY", "deny").strip().lower()
    if policy not in {"deny", "personal"}:
        raise RuntimeError("SSO_UNMAPPED_PROVIDER_POLICY must be 'deny' or 'personal'")
    return policy


async def _provision_personal_tenant(db) -> tuple[str, str]:
    tenant = TenantRecord(id=uuid4().hex, name="Personal workspace", status="active")
    db.add(tenant)
    await db.flush()
    return tenant.id, "admin"


async def provision_external_identity(
    db,
    *,
    provider: str,
    issuer: str,
    provider_subject: str,
    provider_tenant_id: str | None,
    verified_email: str,
) -> tuple[str, str]:
    """Provision a verified provider identity into its mapped application tenant.

    The provider tuple and tenant claim are supplied by the cryptographic
    provider verifier. Email is informational and never selects a tenant.
    Unknown or disabled verified enterprise tenants fail closed.
    """
    provider = provider.strip().lower()
    issuer = issuer.strip()
    provider_subject = provider_subject.strip()
    provider_tenant_id = provider_tenant_id.strip() if provider_tenant_id else None
    if not provider or not issuer or not provider_subject or not verified_email.strip():
        raise HTTPException(403, "verified_provider_identity_required")

    if provider_tenant_id is None:
        if _unmapped_provider_policy() == "personal":
            return await _provision_personal_tenant(db)
        raise HTTPException(403, "provider_tenant_mapping_required")

    return await resolve_provider_tenant_mapping(
        db,
        provider=provider,
        issuer=issuer,
        provider_tenant_id=provider_tenant_id,
    )


async def resolve_provider_tenant_mapping(
    db,
    *,
    provider: str,
    issuer: str,
    provider_tenant_id: str,
) -> tuple[str, str]:
    """Validate an existing provider identity against current mapping state."""
    mapping = await db.scalar(
        select(ProviderTenantMappingRecord).where(
            ProviderTenantMappingRecord.provider == provider,
            ProviderTenantMappingRecord.issuer == issuer,
            ProviderTenantMappingRecord.provider_tenant_id == provider_tenant_id,
        )
    )
    if mapping is None:
        raise HTTPException(403, "provider_tenant_unmapped")
    tenant = await db.get(TenantRecord, mapping.tenant_id)
    if not mapping.enabled or tenant is None or tenant.status != "active":
        raise HTTPException(403, "provider_tenant_disabled")
    return tenant.id, mapping.default_role


async def provision_tenant(
    db,
    *,
    provider=None,
    issuer=None,
    provider_tenant_id=None,
):
    """Compatibility wrapper for the canonical provider provisioning entry point."""
    if provider is None:
        return await _provision_personal_tenant(db)
    return await provision_external_identity(
        db,
        provider=provider,
        issuer=issuer or "",
        provider_subject="repository-provisioning",
        provider_tenant_id=provider_tenant_id,
        verified_email="repository-provisioning@invalid",
    )

"""Focused tenant-boundary and authorization regressions for Remediation 05A."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.core.orm import TenantMembershipRecord
from backend.app.models import FeatureVector
from backend.app.schemas.stream import StreamEnvelope
from backend.app.security.ingest_guard import (
    REQUIRED_INGESTION_SCOPE,
    has_required_ingestion_scope,
)
from backend.app.security.tenant_boundary import (
    TenantBoundaryViolation,
    UntrustedTenantMetadataError,
    assert_tenant_identity,
    reject_untrusted_tenant_fields,
)
from backend.app.security.tenant_context import (
    PERMISSIONS,
    TenantContext,
    check_permission,
)
from backend.app.security.tenants import provision_external_identity, resolve_membership
from backend.app.workers.drain_worker import DrainWorker


@pytest.mark.parametrize(
    "field",
    [
        "tenant",
        "tenant_id",
        "tenantId",
        "organization",
        "organization_id",
        "workspace",
        "workspace_id",
    ],
)
def test_all_tenant_shaped_payload_fields_are_rejected(field: str) -> None:
    with pytest.raises(UntrustedTenantMetadataError):
        reject_untrusted_tenant_fields({"metadata": [{field: "tenant-b"}]})


def test_envelope_is_the_only_worker_tenant_authority() -> None:
    envelope = StreamEnvelope(
        event_id="event-a",
        tenant_id="tenant-a",
        owner_user_id=7,
        payload={"message": "error", "metadata": {"request_id": "r1"}},
    )
    assert envelope.tenant_id == "tenant-a"
    assert envelope.payload["metadata"]["request_id"] == "r1"


def test_parser_boundary_rejects_nested_tenant_injection_before_side_effects() -> None:
    class Batch:
        def __init__(self) -> None:
            self.items = []

        async def add(self, value) -> None:
            self.items.append(value)

    worker = DrainWorker(
        log_buffer=None,
        parser=SimpleNamespace(),
        batch_manager=Batch(),
    )
    payload = {"message": "error", "metadata": {"tenant_id": "tenant-b"}}

    with pytest.raises(UntrustedTenantMetadataError):
        worker._extract_log_messages(payload, trusted_tenant_id="tenant-a")
    assert worker.batch_manager.items == []


def test_tenant_identity_boundaries_fail_closed() -> None:
    assert_tenant_identity("tenant-a", "tenant-a", boundary="raw-persistence")
    with pytest.raises(TenantBoundaryViolation):
        assert_tenant_identity("tenant-a", "tenant-b", boundary="incident-alert")
    with pytest.raises(TenantBoundaryViolation):
        assert_tenant_identity("tenant-a", "default", boundary="feature-persistence")


class _MembershipDB:
    def __init__(
        self, tenant_status: str = "active", membership_status: str = "active"
    ) -> None:
        self.tenant = SimpleNamespace(id="tenant-a", status=tenant_status)
        self.member = SimpleNamespace(
            tenant_id="tenant-a",
            user_id=7,
            role="admin",
            status=membership_status,
        )

    async def scalar(self, statement):
        entity = getattr(statement, "column_descriptions", [{}])[0].get("entity")
        if entity is None:
            return self.tenant
        if entity is TenantMembershipRecord:
            return self.member
        return self.tenant


def _user(**changes):
    values = {"id": 7, "tenant_id": "tenant-a", "status": "active", "role": "viewer"}
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_suspended_membership_denies_an_existing_session() -> None:
    with pytest.raises(HTTPException) as exc:
        await resolve_membership(_MembershipDB(membership_status="suspended"), _user())
    assert exc.value.status_code == 403


def test_membership_role_is_authoritative_after_downgrade() -> None:
    principal = TenantContext(
        tenant_id="tenant-a",
        user_id=7,
        membership_id="tenant-a:7",
        membership_role="viewer",
        membership_status="active",
        user_status="active",
        tenant_status="active",
        user=_user(role="admin"),
    )
    assert "settings:write" not in PERMISSIONS[principal.role]
    with pytest.raises(HTTPException):
        check_permission(principal, "settings:write")


@pytest.mark.asyncio
async def test_suspended_tenant_denies_tenant_scoped_authority() -> None:
    with pytest.raises(HTTPException) as exc:
        await resolve_membership(_MembershipDB(tenant_status="suspended"), _user())
    assert exc.value.status_code == 403


@pytest.mark.parametrize("scopes", [None, [], ["*"], ["logs:read"], "logs:ingest"])
def test_ingestion_scope_fails_closed(scopes) -> None:
    assert not has_required_ingestion_scope(scopes)
    assert REQUIRED_INGESTION_SCOPE == "logs:ingest"


def test_ingestion_scope_accepts_only_canonical_scope() -> None:
    assert has_required_ingestion_scope([REQUIRED_INGESTION_SCOPE])


class _ProviderDB:
    def __init__(self, *, mapping=None, tenant=None) -> None:
        self.mapping = mapping
        self.tenant = tenant

    async def scalar(self, statement):
        return self.mapping

    async def get(self, model, identifier):
        return self.tenant


@pytest.mark.asyncio
async def test_provider_mapping_selects_application_tenant(monkeypatch) -> None:
    monkeypatch.setenv("SSO_UNMAPPED_PROVIDER_POLICY", "deny")
    mapping = SimpleNamespace(
        tenant_id="tenant-a",
        enabled=True,
        default_role="operator",
    )
    result = await provision_external_identity(
        _ProviderDB(
            mapping=mapping, tenant=SimpleNamespace(id="tenant-a", status="active")
        ),
        provider="microsoft",
        issuer="issuer-a",
        provider_subject="subject-a",
        provider_tenant_id="provider-tenant-a",
        verified_email="a@example.test",
    )
    assert result == ("tenant-a", "operator")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mapping",
    [None, SimpleNamespace(tenant_id="tenant-a", enabled=False, default_role="viewer")],
)
async def test_unknown_or_disabled_provider_mapping_fails_closed(
    mapping, monkeypatch
) -> None:
    monkeypatch.setenv("SSO_UNMAPPED_PROVIDER_POLICY", "deny")
    db = _ProviderDB(
        mapping=mapping, tenant=SimpleNamespace(id="tenant-a", status="active")
    )
    with pytest.raises(HTTPException) as exc:
        await provision_external_identity(
            db,
            provider="microsoft",
            issuer="issuer-a",
            provider_subject="subject-a",
            provider_tenant_id="provider-tenant-a",
            verified_email="a@example.test",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_provider_without_verified_tenant_claim_is_denied_by_default(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SSO_UNMAPPED_PROVIDER_POLICY", "deny")
    with pytest.raises(HTTPException) as exc:
        await provision_external_identity(
            _ProviderDB(),
            provider="google",
            issuer="issuer-a",
            provider_subject="subject-a",
            provider_tenant_id=None,
            verified_email="a@example.test",
        )
    assert exc.value.detail == "provider_tenant_mapping_required"


def test_downstream_feature_identity_mismatch_is_not_reconciled() -> None:
    vector = FeatureVector(
        window_id="window-a",
        timestamp=datetime.now(timezone.utc),
        tenant_id="tenant-b",
        log_count=1,
        unique_templates=1,
    )
    from backend.app.repositories.feature_repository import FeatureRepository

    with pytest.raises(TenantBoundaryViolation):
        # The repository check runs before any engine interaction.
        import asyncio

        asyncio.run(
            FeatureRepository(engine=None).persist_feature_vector("tenant-a", vector)
        )

"""Authoritative scope for user-owned operational data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DataScope:
    """The indivisible tenant-and-owner authorization boundary.

    Repositories accept this value instead of independent tenant/user
    arguments so callers cannot accidentally apply only half of the scope.
    """

    tenant_id: str
    owner_user_id: int

    def __post_init__(self) -> None:
        tenant_id = str(self.tenant_id).strip()
        if not tenant_id or tenant_id == "default":
            raise ValueError("an explicit non-default tenant is required")
        if isinstance(self.owner_user_id, bool) or int(self.owner_user_id) <= 0:
            raise ValueError("a positive authoritative owner user ID is required")
        object.__setattr__(self, "tenant_id", tenant_id)
        object.__setattr__(self, "owner_user_id", int(self.owner_user_id))

    @classmethod
    def from_principal(cls, principal: Any) -> DataScope:
        return cls(
            tenant_id=str(principal.tenant_id),
            owner_user_id=int(principal.user_id),
        )

"""User account status constants.

Centralises the status string values used in the ``users.status`` column
so that router, repository, and test code never hardcode raw strings.
"""

from __future__ import annotations

PENDING_VERIFICATION: str = "pending_verification"
"""User has registered but has not yet confirmed their email address."""

ACTIVE: str = "active"
"""User has a fully verified and operational account."""

SUSPENDED: str = "suspended"
"""Account has been administratively suspended."""

ALL_STATUSES: frozenset[str] = frozenset({PENDING_VERIFICATION, ACTIVE, SUSPENDED})

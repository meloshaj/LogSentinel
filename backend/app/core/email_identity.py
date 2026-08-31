"""Canonical email identity handling for local authentication."""

from __future__ import annotations


def canonicalize_email(value: str) -> str:
    """Return the one canonical representation used by auth persistence.

    The application deliberately treats email addresses case-insensitively and
    removes surrounding whitespace at every authentication boundary.
    """

    return value.strip().lower()

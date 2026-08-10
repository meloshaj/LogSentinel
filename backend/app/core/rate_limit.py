"""Shared rate-limiter instance for the LogSentinel API.

Separated into its own module to avoid circular imports between
``main.py`` (which registers the limiter on the app) and router
modules that apply ``@limiter.limit()`` decorators.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

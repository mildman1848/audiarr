"""`app.providers` package — metadata providers for Audiarr.

Importing this package registers the built-in providers (audible, audnexus)
with the canonical ProviderRegistry.
"""
from __future__ import annotations

from app.providers.audible import AudibleProvider
from app.providers.audnexus import AudnexusProvider
from app.providers.registry import ProviderRegistry

# Register built-in providers. Idempotent: re-imports are safe.
ProviderRegistry.register(AudibleProvider, priority=10)
ProviderRegistry.register(AudnexusProvider, priority=20)

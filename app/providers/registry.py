"""Canonical provider registry.

This is the single source of truth for provider registration. The
duplicate registry that used to live in ``app.providers.base`` was
removed; import ProviderRegistry from here.
"""
from __future__ import annotations

import logging

from app.providers.base import BaseMetadataProvider

log = logging.getLogger("audiarr.providers.registry")


class ProviderRegistry:
    """
    In-memory registry of provider classes.

    Design notes:
    - Re-registering the same class is a no-op (idempotent), so tests and
      app bootstrap can both register without ordering concerns.
    - Registering a *different* class under an already-used name replaces
      the old entry (last write wins) with a warning.
    - Priority is bookkeeping metadata for UI sorting; collisions are
      tolerated instead of raising, because two providers sharing a
      priority is not a conflict worth crashing bootstrap over.
    """

    _lookup_by_name: dict[str, type[BaseMetadataProvider]] = {}
    _lookup_by_provider_url: dict[str, type[BaseMetadataProvider]] = {}
    _priority_index: dict[int, type[BaseMetadataProvider]] = {}

    @classmethod
    def register(
        cls,
        provider_cls: type[BaseMetadataProvider],
        priority: int = 0,
    ) -> None:
        instance = provider_cls()
        name = instance.provider_name

        existing = cls._lookup_by_name.get(name)
        if existing is provider_cls:
            return  # idempotent re-registration

        if existing is not None:
            log.warning(
                "provider name %r re-registered: replacing %s with %s",
                name,
                existing.__name__,
                provider_cls.__name__,
            )

        cls._lookup_by_name[name] = provider_cls
        if instance.provider_url:
            cls._lookup_by_provider_url[instance.provider_url] = provider_cls

        if priority in cls._priority_index and cls._priority_index[priority] is not provider_cls:
            log.debug(
                "provider priority %d shared by %s and %s (tolerated)",
                priority,
                cls._priority_index[priority].__name__,
                provider_cls.__name__,
            )
        cls._priority_index[priority] = provider_cls

    @classmethod
    def by_name(cls, name: str, **ctor_kwargs) -> BaseMetadataProvider:
        provider_cls = cls._lookup_by_name.get(name)
        if provider_cls is None:
            raise KeyError(f"No provider registered under {name!r}")
        return provider_cls(**ctor_kwargs)

    @classmethod
    def by_provider_url(cls, url: str) -> BaseMetadataProvider:
        provider_cls = cls._lookup_by_provider_url.get(url)
        if provider_cls is None:
            raise KeyError(f"No provider registered for provider_url={url!r}")
        return provider_cls()

    @classmethod
    def has_provider(cls, name: str) -> bool:
        return name in cls._lookup_by_name

    @classmethod
    def registered_names(cls) -> list[str]:
        return sorted(cls._lookup_by_name)

    @classmethod
    def all_registrations(cls) -> list[tuple[int, type[BaseMetadataProvider]]]:
        return sorted(cls._priority_index.items())

    @classmethod
    def reset(cls) -> None:
        """Clear all registrations. Intended for test isolation only."""
        cls._lookup_by_name.clear()
        cls._lookup_by_provider_url.clear()
        cls._priority_index.clear()

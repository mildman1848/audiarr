"""Audible metadata provider (stub).

Audible has no official public search API; community tooling typically
scrapes the (undocumented) internal API used by the Audible apps, which
requires a device-registered auth token per marketplace. That is out of
scope for Audiarr's MVP and is not implemented here.

Instead, this stub demonstrates the provider interface and locale/
marketplace mapping so:
  - the provider chain, settings, and UI have a real Audible-shaped
    provider to wire up now,
  - a future real implementation only needs to replace ``search()``'s
    body with an authenticated HTTP call, keeping the same return shape.

Locale -> marketplace mapping mirrors Audible's regional "zones" (e.g.
German users map to the "de" zone / audible.de).
"""

from __future__ import annotations

import logging

from app.metadata.base import BookMetadata, MetadataProvider

log = logging.getLogger("audiarr.metadata.audible")

# Audible marketplace ("zone") domains, keyed by the locale codes used in
# app/models/settings.py::AudibleLocale.
AUDIBLE_MARKETPLACES: dict[str, str] = {
    "us": "audible.com",
    "uk": "audible.co.uk",
    "de": "audible.de",
    "fr": "audible.fr",
    "ca": "audible.ca",
    "au": "audible.com.au",
    "it": "audible.it",
    "es": "audible.es",
    "jp": "audible.co.jp",
    "in": "audible.in",
}


class AudibleProvider(MetadataProvider):
    """Stub Audible provider, no network calls, no credentials required."""

    name = "audible"

    def marketplace_for(self, locale: str) -> str:
        return AUDIBLE_MARKETPLACES.get(locale, AUDIBLE_MARKETPLACES["us"])

    async def search(self, query: str, locale: str) -> list[BookMetadata]:
        marketplace = self.marketplace_for(locale)
        log.info(
            "Audible stub search for %r on marketplace %s (locale=%s)",
            query,
            marketplace,
            locale,
        )
        if not query.strip():
            return []

        # Deterministic mocked result so tests and the UI have something
        # real to render without hitting the network or needing an
        # Audible account. Replace with a real authenticated client later.
        return [
            BookMetadata(
                source=self.name,
                source_id=f"stub-asin-{abs(hash(query.lower())) % 100000}",
                title=query.strip(),
                authors=["Unknown Author"],
                narrators=["Unknown Narrator"],
                asin=f"STUB{abs(hash(query.lower())) % 100000:05d}",
                language=locale,
                description=(
                    "Mocked Audible result (no live Audible API integration yet). "
                    f"Marketplace: {marketplace}."
                ),
            )
        ]

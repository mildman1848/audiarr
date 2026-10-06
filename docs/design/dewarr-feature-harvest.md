# Dewarr feature harvest for Audiarr 1.1.x

**Status:** planning review for Audiarr #78; documentation only
**Reviewed:** 2026-10-06
**Dewarr snapshot:** `logabell/dewarr` main at [`8c9eefbd82ba5b13fb82a69a33948d737f4b93be`](https://github.com/logabell/dewarr/commit/8c9eefbd82ba5b13fb82a69a33948d737f4b93be), README reports v0.3.8.

## Purpose and guardrails

Dewarr is a useful audiobook-domain comparator, not an architectural target. Harvest small user-value ideas while preserving Audiarr's Python/Servarr automation model, Starr-style UI conventions, existing import pipeline, and optional/offline-safe integration defaults. A discovery or follow must not silently become a monitored book, request, or download.

This review is based on Dewarr's public README and feature documentation at the pinned repository snapshot. It does not claim an audit of every implementation path or guarantee that upstream behavior is bug-free.

## Adopt, adapt, defer, or skip

| Dewarr capability | Decision for Audiarr | Rationale and planned home |
|---|---|---|
| Goodreads shelves and StoryGraph lists/tags | **Adapt** | Reuse Audiarr's import-list and provider-matching groundwork in [#79](https://github.com/mildman1848/audiarr/issues/79). Start with maintainable public feeds/exports (Goodreads shelf RSS and CSV). StoryGraph session cookies represent full account access; do not request/store them merely to reproduce an integration. Add StoryGraph only through a stable, authorized interface or user-provided export; otherwise explicitly defer it. Goodreads RSS is partial observation, so absence from a later feed must not remove a local entry. Dewarr documents both source behavior and the StoryGraph credential model in its [reading-account guide](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/READING-ACCOUNTS.md). |
| Hardcover lists, metadata and reading state | **Adapt; keep separate from list imports** | Existing Audiarr issue [#66](https://github.com/mildman1848/audiarr/issues/66) remains the optional metadata-enrichment slice; #79 must not duplicate it. Prefer the smallest read-only token scope and user-initiated/on-demand enrichment with caching. Do not add Hardcover list write-back just because Dewarr supports it. Revisit ordering when #80's provider requirements are scoped. Dewarr's [Hardcover account notes](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/READING-ACCOUNTS.md) describe broader scopes, including list writes; that breadth is not required by Audiarr #66. |
| Author and series follows, including back-catalog selection | **Adapt with explicit review boundaries** | Track future additions first in [#80](https://github.com/mildman1848/audiarr/issues/80). A follow should establish a baseline and present owned/missing/excluded results; following alone must not create downloads. Future-only is the safe default. Back-catalog additions require preview, per-item selection/exclusions, and idempotent monitored-book creation. Dewarr's [follow workflow](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/READING-ACCOUNTS.md) likewise separates follow, policy preview, and acquisition. |
| Curated discovery shelves and awards | **Adopt selectively as a later discovery idea** | Versioned, attributed collections can help discovery, but are not a substitute for the library, Wanted queue, or import pipeline. Any future slice should identify original sources and coverage, distinguish work from recording/edition, and keep following separate from requesting. Do not expand #78 into an awards catalog or start scraping as part of #79–#83. Dewarr describes source-specific discovery and coverage in its [metadata-source guide](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/METADATA-SOURCES.md). |
| Native audiobook search sources and torrent clients | **Evaluate; stay Prowlarr-first** | Preserve the current Prowlarr + SABnzbd path. [#81](https://github.com/mildman1848/audiarr/issues/81) correctly separates search sources from download clients: a qBittorrent adapter does not itself provide search results. Require an explicit capability/safety matrix, completed-path mapping, and the existing import/quality pipeline before adoption. Native sources are optional only when lawful, maintainable, and independently testable; do not bake source credentials or brittle site scraping into core. Dewarr's [download-client guide](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/DOWNLOAD-CLIENTS.md) makes the source/client distinction and path checks explicit. |
| Discovery digests and richer notifications | **Adapt through Connect** | Extend the existing Connect/webhook foundation in [#82](https://github.com/mildman1848/audiarr/issues/82), not with a parallel delivery subsystem. Define event ownership and stable idempotency keys first; keep payloads credential-free and avoid private-list/user data in installation-wide destinations. Delivery failures must not fail imports/downloads, and retry semantics must acknowledge uncertain external delivery. Dewarr records these concerns in its [notification contract](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/NOTIFICATIONS.md). |
| Multiple users, roles, and per-library access | **Defer implementation; decide model first** | [#83](https://github.com/mildman1848/audiarr/issues/83) is a decision issue, not authorization work. First document whether Audiarr remains a single-household administrator tool and what ownership, approvals, and library isolation would mean. Do not add Plex/OIDC sign-in before defining those boundaries. Dewarr's separate [OIDC](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/OIDC.md) and [Plex access](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/PLEX.md) guides illustrate that sign-in and library authorization are distinct concerns. |
| Dewarr's full product architecture and ebook workflows | **Skip** | Audiarr is a Starr-style audiobook manager with an existing provider, wanted, download, import, and conversion pipeline. Replacing that with Dewarr's broader reading-account, ebook/Bookdrop, household-user, or automatic-acquisition model would be a rewrite and would blur Audiarr's scope. |

## 1.1.x roadmap disposition

The existing issues #79–#83 already cover the useful feature areas; this pass creates no duplicate issues and does not change their acceptance criteria. Keep their current sequence in `ROADMAP.md`: reading-list imports (#79), follows (#80), source/client evaluation (#81), Connect notifications (#82), then the shared-access decision (#83). Each remains independently scoped and subject to its own safety review.

Hardcover enrichment (#66) remains a distinct open follow-up: it is not part of Goodreads/StoryGraph import work, and its place relative to author/series follows should be reconsidered when #80 is prepared. Do not silently fold Hardcover list write-back, auto-download policies, OIDC/Plex, or broad discovery scraping into the existing issues.

## Sources

All Dewarr references above are pinned to commit [`8c9eefbd82ba5b13fb82a69a33948d737f4b93be`](https://github.com/logabell/dewarr/commit/8c9eefbd82ba5b13fb82a69a33948d737f4b93be), retrieved 2026-10-06:

- [README](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/README.md)
- [Reading accounts](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/READING-ACCOUNTS.md)
- [Metadata and discovery sources](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/METADATA-SOURCES.md)
- [Download clients](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/DOWNLOAD-CLIENTS.md)
- [Notifications](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/NOTIFICATIONS.md)
- [OIDC](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/OIDC.md)
- [Plex sign-in](https://github.com/logabell/dewarr/blob/8c9eefbd82ba5b13fb82a69a33948d737f4b93be/docs/PLEX.md)

"""Metadata backfill unit tests: fake provider chain, no network calls."""

from __future__ import annotations

import pytest

from app.db import get_conn, migrate, set_db_path_override
from app.library import BookCreate, create_book
from app.metadata.backfill import run_backfill_batch
from app.providers.base import BaseMetadataProvider, BookDetailInfo, BookQuickInfo, SearchResponse
from app.providers.chain import ProviderChain, ProviderChainConfig


class FakeProvider(BaseMetadataProvider):
    """In-memory provider: search/get_detail keyed by title, no HTTP at all."""

    provider_name = "fake"

    def __init__(self, hits: dict[str, tuple[str, str]] | None = None) -> None:
        super().__init__()
        self._hits = hits or {}  # title -> (asin, release_date)

    async def search(self, query: str, **kwargs) -> SearchResponse:
        if query not in self._hits:
            return SearchResponse(results=[], query_used=query)
        asin, _ = self._hits[query]
        return SearchResponse(
            results=[
                BookQuickInfo(
                    provider_uid=asin, provider_name=self.provider_name, title=query, asin=asin
                )
            ],
            query_used=query,
        )

    async def get_detail(self, external_id: str, **kwargs) -> BookDetailInfo | None:
        for title, (asin, release_date) in self._hits.items():
            if asin == external_id:
                return BookDetailInfo(
                    provider_uid=asin,
                    provider_name=self.provider_name,
                    provider_external_id=asin,
                    title=title,
                    release_date=release_date,
                )
        return None


class ExplodingChain:
    """Duck-typed stand-in for ProviderChain whose search() raises directly.

    A real ProviderChain already swallows per-provider exceptions itself
    (see ProviderChain.search/get_detail), so this is the only way to
    exercise run_backfill_batch's own try/except around an unexpected,
    non-provider failure.
    """

    async def search(self, query: str, **kwargs):
        raise RuntimeError("unexpected failure")

    async def get_detail(self, provider_name: str, external_id: str, **kwargs):
        raise AssertionError("should not be reached")


def _chain(provider: FakeProvider) -> ProviderChain:
    return ProviderChain(
        config=ProviderChainConfig(provider_order=["fake"]),
        provider_overrides={"fake": provider},
    )


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "lib.db"
    set_db_path_override(path)
    migrate(path)
    yield path
    set_db_path_override(None)


@pytest.mark.asyncio
async def test_backfill_updates_matched_book(db_path):
    with get_conn() as conn:
        book_id = create_book(conn, BookCreate(title="Matched Book", authors=["Author A"]))

    chain = _chain(FakeProvider(hits={"Matched Book": ("B000MATCH1", "2030-01-15")}))
    result = await run_backfill_batch(chain, batch_size=10)

    assert result.updated == 1
    assert result.failed == 0
    assert result.remaining == 0

    with get_conn() as conn:
        row = conn.execute(
            "SELECT asin, release_date FROM books WHERE id = ?", (book_id,)
        ).fetchone()
    assert row["asin"] == "B000MATCH1"
    assert row["release_date"] == "2030-01-15"


@pytest.mark.asyncio
async def test_backfill_counts_provider_miss_as_failed_and_leaves_book_null(db_path):
    with get_conn() as conn:
        book_id = create_book(conn, BookCreate(title="No Match Book"))

    chain = _chain(FakeProvider(hits={}))
    result = await run_backfill_batch(chain, batch_size=10)

    assert result.updated == 0
    assert result.failed == 1
    assert result.remaining == 1

    with get_conn() as conn:
        row = conn.execute(
            "SELECT asin, release_date FROM books WHERE id = ?", (book_id,)
        ).fetchone()
    assert row["asin"] is None
    assert row["release_date"] is None


@pytest.mark.asyncio
async def test_backfill_skips_unexpected_exception_without_crashing(db_path):
    with get_conn() as conn:
        book_id = create_book(conn, BookCreate(title="Boom Book"))

    result = await run_backfill_batch(ExplodingChain(), batch_size=10)  # type: ignore[arg-type]

    assert result.updated == 0
    assert result.failed == 1
    assert result.remaining == 1

    with get_conn() as conn:
        row = conn.execute(
            "SELECT asin, release_date FROM books WHERE id = ?", (book_id,)
        ).fetchone()
    assert row["asin"] is None
    assert row["release_date"] is None


@pytest.mark.asyncio
async def test_backfill_respects_batch_size(db_path):
    with get_conn() as conn:
        for i in range(3):
            create_book(conn, BookCreate(title=f"Book {i}"))

    chain = _chain(FakeProvider(hits={}))
    result = await run_backfill_batch(chain, batch_size=2)

    # Only 2 of the 3 books were attempted this batch; since none matched,
    # none left the "both NULL" candidate pool, so all 3 remain.
    assert result.updated + result.failed == 2
    assert result.remaining == 3


@pytest.mark.asyncio
async def test_backfill_ignores_books_that_already_have_metadata(db_path):
    with get_conn() as conn:
        create_book(
            conn,
            BookCreate(title="Already Tagged", release_date="2020-01-01"),
        )

    chain = _chain(FakeProvider(hits={"Already Tagged": ("SHOULD_NOT_BE_USED", "2099-01-01")}))
    result = await run_backfill_batch(chain, batch_size=10)

    assert result.updated == 0
    assert result.failed == 0
    assert result.remaining == 0

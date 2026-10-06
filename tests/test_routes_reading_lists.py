"""Route/domain tests for reading-list imports (issue #79).

Everything is stubbed: metadata comes from an in-process ``StubProvider``
injected via the ``build_provider_chain`` dependency override, and the
Goodreads feed from an ``httpx.MockTransport`` client injected via
``get_goodreads_client``. Nothing here can reach Goodreads, Audible or
StoryGraph.
"""

from __future__ import annotations

import io
import json
import logging

import httpx
import pytest

from app.api.routes_import_lists import get_goodreads_client
from app.api.routes_metadata import build_provider_chain
from app.db import get_conn
from app.providers.base import BaseMetadataProvider, BookDetailInfo, BookQuickInfo, SearchResponse
from app.providers.chain import ProviderChain, ProviderChainConfig

FEED_URL = "https://www.goodreads.com/review/list_rss/12345?shelf=to-read"
SECRET = "s3cr3tK3y"


class StubProvider(BaseMetadataProvider):
    """Deterministic provider: canned search hits and details, call log."""

    provider_name = "stub"

    def __init__(self) -> None:
        super().__init__()
        self.hits: dict[str, list[BookQuickInfo]] = {}
        self.details: dict[str, BookDetailInfo | None] = {}
        self.search_calls: list[tuple[str, dict]] = []
        self.detail_calls: list[str] = []

    async def search(self, query, **kwargs):
        self.search_calls.append((query, kwargs))
        return SearchResponse(results=list(self.hits.get(query, [])), query_used=query)

    async def get_detail(self, external_id, **kwargs):
        self.detail_calls.append(external_id)
        return self.details.get(external_id)


def hit(uid, title, authors=("Ann Author",), isbn=None, subtitle="") -> BookQuickInfo:
    return BookQuickInfo(
        provider_uid=uid, provider_name="stub", title=title, subtitle=subtitle, authors=list(authors),
        narrators=["Nora Narrator"], asin=uid, isbn=isbn, locale="us", series="Saga", series_position=2,
    )


def detail(uid, title, authors=("Ann Author",), **over) -> BookDetailInfo:
    fields = {
        "provider_uid": uid, "provider_name": "stub", "provider_external_id": uid, "title": title,
        "subtitle": "Sub", "description": "Desc", "authors": list(authors), "narrators": ["Nora Narrator"],
        "series": "Saga", "series_position": 2, "language": "english", "duration_seconds": 3600,
        "release_date": "2020-05-01", "cover_url": "https://img.example/c.jpg", "asin": uid,
        "publishers": ["Pub House"], "audible_locale": "us",
    }
    fields.update(over)
    return BookDetailInfo(**fields)


@pytest.fixture()
def stub(app_client) -> StubProvider:
    provider = StubProvider()
    app_client.app.dependency_overrides[build_provider_chain] = lambda: ProviderChain(
        config=ProviderChainConfig(provider_order=["stub"]), provider_overrides={"stub": provider}
    )
    return provider


class Feed:
    """Mutable Goodreads feed behind a MockTransport; records requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.body = b""
        self.status = 200
        self.headers: dict[str, str] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, content=self.body, headers=self.headers)


def rss(*items: tuple[str, str, str, str]) -> bytes:
    """items: (title, book_id, author, isbn13)."""
    body = "".join(
        f"<item><title><![CDATA[{t}]]></title><book_id>{b}</book_id><author_name>{a}</author_name>"
        f"<isbn></isbn><isbn13>{i}</isbn13><user_shelves>to-read</user_shelves></item>"
        for t, b, a, i in items
    )
    head = '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0">'
    return f"{head}<channel>{body}</channel></rss>".encode()


@pytest.fixture()
def feed(app_client) -> Feed:
    f = Feed()
    client = httpx.AsyncClient(transport=httpx.MockTransport(f.handler))
    app_client.app.dependency_overrides[get_goodreads_client] = lambda: client
    return f


def _book_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]


def _settings_bytes(app_client) -> str:
    return json.dumps(app_client.get("/api/v1/settings").json(), sort_keys=True)


def _save_source(app_client, url=FEED_URL, **extra) -> dict:
    resp = app_client.post("/api/v1/import-lists/goodreads/sources", json={"feed_url": url, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


# -- feed URL validation + saved sources ---------------------------------------------


def test_validate_feed_endpoint_accepts_and_decomposes(app_client):
    resp = app_client.post("/api/v1/import-lists/goodreads/validate-feed", json={"feed_url": FEED_URL})
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "goodreads_user_id": "12345", "shelf": "to-read"}


@pytest.mark.parametrize(
    ("url", "code"),
    [
        (f"http://www.goodreads.com/review/list_rss/1?shelf=read&token={SECRET}", "feed_url_not_https"),
        (f"https://www.goodreads.com/review/list_rss/1?shelf=read&key={SECRET}", "feed_url_private_key"),
        (f"https://evil.example/review/list_rss/1?shelf=read&x={SECRET}", "feed_url_host"),
        (f"https://u:{SECRET}@www.goodreads.com/review/list_rss/1?shelf=read", "feed_url_userinfo"),
    ],
)
def test_rejected_feed_urls_never_echo_input_or_get_saved(app_client, caplog, url, code):
    caplog.set_level(logging.DEBUG)
    before = _settings_bytes(app_client)
    for path in ("validate-feed", "sources"):
        resp = app_client.post(f"/api/v1/import-lists/goodreads/{path}", json={"feed_url": url})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == code
        assert SECRET not in resp.text and "evil" not in resp.text
    assert _settings_bytes(app_client) == before
    assert SECRET not in caplog.text


def test_source_crud_stores_only_public_metadata(app_client):
    source = _save_source(app_client, name="  Mein  Regal ")
    assert source["id"].startswith("goodreads-") and source["name"] == "Mein Regal"
    assert (source["goodreads_user_id"], source["shelf"]) == ("12345", "to-read")
    assert source["type"] == "goodreads_rss"
    assert source["status"] == "unknown" and source["enabled"] is True

    stored = app_client.get("/api/v1/settings").json()["import_lists"]["reading_list_sources"]
    assert stored[0]["goodreads_user_id"] == "12345"
    assert "feed_url" not in stored[0] and "goodreads.com" not in json.dumps(stored)

    dup = app_client.post("/api/v1/import-lists/goodreads/sources", json={"feed_url": FEED_URL})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "source_exists"

    assert app_client.get("/api/v1/import-lists/goodreads/sources").json() == [source]
    patched = app_client.patch(
        f"/api/v1/import-lists/goodreads/sources/{source['id']}", json={"enabled": False, "name": "Neu"}
    )
    assert patched.status_code == 200
    assert (patched.json()["enabled"], patched.json()["name"]) == (False, "Neu")

    assert app_client.delete(f"/api/v1/import-lists/goodreads/sources/{source['id']}").status_code == 204
    assert app_client.delete(f"/api/v1/import-lists/goodreads/sources/{source['id']}").status_code == 404
    assert app_client.get("/api/v1/import-lists/goodreads/sources").json() == []


def test_generic_import_list_api_unchanged_for_liberatarr_and_lists_saved_sources(app_client):
    before = app_client.get("/api/v1/import-lists").json()
    assert [s["id"] for s in before] == ["liberatarr"]

    source = _save_source(app_client)
    after = app_client.get("/api/v1/import-lists").json()
    assert [s["id"] for s in after] == ["liberatarr", source["id"]]
    assert after[0] == before[0]
    assert after[1]["type"] == "goodreads_rss" and after[1]["status"] == "unknown"

    # Reading lists never bulk-sync: the generic sync route says so and creates nothing.
    resp = app_client.post(f"/api/v1/import-lists/{source['id']}/sync")
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "selection_required"
    assert _book_count() == 0
    assert app_client.post("/api/v1/import-lists/nope/sync").status_code == 404


# -- feed preview ----------------------------------------------------------------------


def test_feed_preview_matches_through_provider_chain_and_writes_nothing(app_client, stub, feed):
    source = _save_source(app_client)
    feed.body = rss(
        ("Die Känguru-Chroniken (Känguru, #1)", "101", "Marc-Uwe Kling", "9780306406157"),
        ("Nothing Known", "102", "Nobody", ""),
        ("Ambiguous Book", "103", "Ann Author", ""),
    )
    stub.hits["Die Känguru-Chroniken"] = [
        hit("B0KANGURU1", "Die Känguru-Chroniken", ("Marc-Uwe Kling",), isbn="9780306406157"),
        hit("B0OTHER001", "Völlig anderes Buch", ("Someone",)),
    ]
    stub.hits["Ambiguous Book"] = [hit("B0AMBIG001", "Ambiguous Booklet Deluxe", ("Other Person",))]
    books_before, settings_before = _book_count(), _settings_bytes(app_client)

    resp = app_client.post(f"/api/v1/import-lists/goodreads/sources/{source['id']}/preview", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Request went to the fixed host/path only, without any credentials.
    assert [str(r.url.host) for r in feed.requests] == ["www.goodreads.com"]
    assert dict(feed.requests[0].url.params) == {"shelf": "to-read", "page": "1", "per_page": "100"}

    assert body["source_type"] == "goodreads_rss" and body["total_entries"] == 3
    matched, unmatched, ambiguous = body["entries"]
    assert matched["entry"]["entry_key"] == "gr:101"
    assert matched["entry"]["search_title"] == "Die Känguru-Chroniken"
    assert matched["status"] == "matched"
    top = matched["candidates"][0]
    assert (top["provider_name"], top["provider_uid"], top["asin"]) == ("stub", "B0KANGURU1", "B0KANGURU1")
    assert top["score"] == 1.0 and "isbn" in top["match_reasons"] and top["existing_book_id"] is None
    assert [c["provider_uid"] for c in matched["candidates"]] == ["B0KANGURU1", "B0OTHER001"]
    assert unmatched["status"] == "unmatched" and unmatched["candidates"] == []
    assert ambiguous["status"] == "ambiguous" and ambiguous["candidates"][0]["score"] < 0.85

    # Partial-observation semantics are explicit; preview wrote nothing and only searched.
    assert body["observation"] == {"kind": "goodreads_rss_page", "page": 1, "complete": False}
    assert "partial_observation" in body["notes"] and "absence_never_removes" in body["notes"]
    assert stub.detail_calls == []
    assert _book_count() == books_before and _settings_bytes(app_client) == settings_before


def test_feed_preview_paging_window_and_next_page_hint(app_client, stub, feed):
    source = _save_source(app_client)
    feed.body = rss(*[(f"Book {i}", str(i), "A", "") for i in range(1, 101)])
    resp = app_client.post(
        f"/api/v1/import-lists/goodreads/sources/{source['id']}/preview", json={"limit": 2, "page": 3}
    )
    body = resp.json()
    assert dict(feed.requests[0].url.params)["page"] == "3"
    assert len(body["entries"]) == 2 and body["total_entries"] == 100
    assert body["next_offset"] == 2 and body["next_page"] == 4
    assert {"feed_may_have_more_pages", "preview_window_truncated"} <= set(body["notes"])
    assert len(stub.search_calls) <= 4  # bounded work: window only (2 entries x up to 2 searches)


def test_feed_preview_uses_author_search_then_falls_back_to_title_only(app_client, stub, feed):
    source = _save_source(app_client)
    feed.body = rss(("Solo", "1", "Ann Author", ""))
    app_client.post(f"/api/v1/import-lists/goodreads/sources/{source['id']}/preview", json={})
    assert [(q, kw.get("author")) for q, kw in stub.search_calls] == [("Solo", "Ann Author"), ("Solo", None)]


def test_preview_marks_candidates_already_in_library(app_client, stub, feed):
    source = _save_source(app_client)
    feed.body = rss(("Known Book", "1", "Ann Author", ""))
    stub.hits["Known Book"] = [hit("B0KNOWN001", "Known Book")]
    stub.details["B0KNOWN001"] = detail("B0KNOWN001", "Known Book")
    imported = app_client.post(
        f"/api/v1/import-lists/goodreads/sources/{source['id']}/import",
        json={"selections": [{"entry_key": "gr:1", "provider_name": "stub", "provider_uid": "B0KNOWN001"}]},
    ).json()
    book_id = imported["items"][0]["book_id"]

    body = app_client.post(
        f"/api/v1/import-lists/goodreads/sources/{source['id']}/preview", json={}
    ).json()
    assert body["entries"][0]["existing_book_id"] == book_id
    assert body["entries"][0]["candidates"][0]["existing_book_id"] == book_id


def test_feed_preview_errors_are_safe(app_client, stub, feed, caplog):
    caplog.set_level(logging.DEBUG)
    source = _save_source(app_client)
    url = f"/api/v1/import-lists/goodreads/sources/{source['id']}/preview"

    feed.status, feed.headers = 302, {"location": f"https://evil.example/?key={SECRET}"}
    resp = app_client.post(url, json={})
    assert resp.status_code == 502 and resp.json()["detail"]["code"] == "feed_redirect"
    assert len(feed.requests) == 1  # redirect not followed

    feed.status, feed.headers, feed.body = 200, {}, b"<!DOCTYPE rss [<!ENTITY a 'b'>]><rss/>"
    assert app_client.post(url, json={}).json()["detail"]["code"] == "xml_forbidden"
    feed.body = b"<rss><channel><item>"
    assert app_client.post(url, json={}).json()["detail"]["code"] == "xml_malformed"

    last = app_client.post(url, json={})
    assert "goodreads.com" not in last.text and SECRET not in last.text
    assert "list_rss" not in caplog.text and SECRET not in caplog.text

    assert app_client.post(url.replace(source["id"], "goodreads-missing"), json={}).status_code == 404
    assert app_client.post(url, json={"page": 0}).status_code == 422
    assert app_client.post(url, json={"limit": 500}).status_code == 422
    feed.body = rss(("Solo", "1", "A", ""))
    bad_locale = app_client.post(url, json={"locale": "zz"})
    assert bad_locale.status_code == 422 and bad_locale.json()["detail"]["code"] == "locale_invalid"


def test_disabled_source_cannot_preview_or_import(app_client, stub, feed):
    source = _save_source(app_client, enabled=False)
    base = f"/api/v1/import-lists/goodreads/sources/{source['id']}"
    for path, body in (("preview", {}), ("import", {"selections": []})):
        resp = app_client.post(f"{base}/{path}", json=body)
        assert resp.status_code == 409 and resp.json()["detail"]["code"] == "source_disabled"
    assert feed.requests == []


def test_adhoc_feed_preview_validates_before_fetching(app_client, stub, feed):
    bad = app_client.post(
        "/api/v1/import-lists/goodreads/preview",
        json={"feed_url": f"https://www.goodreads.com/review/list_rss/1?shelf=read&key={SECRET}"},
    )
    assert bad.status_code == 422 and SECRET not in bad.text and feed.requests == []

    feed.body = rss(("Solo", "1", "A", ""))
    ok = app_client.post("/api/v1/import-lists/goodreads/preview", json={"feed_url": FEED_URL})
    assert ok.status_code == 200 and len(ok.json()["entries"]) == 1
    assert app_client.get("/api/v1/import-lists/goodreads/sources").json() == []  # nothing saved


# -- CSV preview -------------------------------------------------------------------------

GR_CSV = (
    "Book Id,Title,Author,Additional Authors,ISBN,ISBN13,Bookshelves,Exclusive Shelf\n"
    '7,"Hôtel &amp; Café (Nord, #1)","Eugène Dabit","",="",="9780306406157","audible",to-read\n'
    '8,"Read Already","Ann Author","","","","",read\n'
)
SG_CSV = "Title,Authors,Contributors,ISBN/UID,Read Status\nStory Book,Ann Author,,,to-read\n"


def _csv_upload(app_client, content: str | bytes, **form):
    data = content.encode() if isinstance(content, str) else content
    return app_client.post(
        "/api/v1/import-lists/csv/preview",
        files={"file": ("export.csv", io.BytesIO(data), "text/csv")},
        data={k: str(v) for k, v in form.items()},
    )


def test_goodreads_csv_preview_is_read_only_and_filters_by_shelf(app_client, stub):
    stub.hits["Hôtel & Café"] = [hit("B0HOTEL001", "Hôtel & Café", ("Eugène Dabit",))]
    before = (_book_count(), _settings_bytes(app_client))

    resp = _csv_upload(app_client, GR_CSV, shelf="to-read")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_type"] == "goodreads_csv" and body["total_entries"] == 1
    assert body["observation"] == {"kind": "csv_upload", "page": None, "complete": True}
    entry = body["entries"][0]
    assert entry["entry"]["title"] == "Hôtel & Café (Nord, #1)"
    assert entry["entry"]["isbn13"] == "9780306406157" and entry["entry"]["source_book_id"] == "7"
    assert entry["status"] == "matched" and entry["candidates"][0]["provider_uid"] == "B0HOTEL001"

    unfiltered = _csv_upload(app_client, GR_CSV).json()
    assert unfiltered["total_entries"] == 2 and "partial_observation" not in unfiltered["notes"]
    assert (_book_count(), _settings_bytes(app_client)) == before


def test_storygraph_csv_preview_and_window(app_client, stub):
    stub.hits["Story Book"] = [hit("B0STORY001", "Story Book")]
    body = _csv_upload(app_client, SG_CSV).json()
    assert body["source_type"] == "storygraph_csv"
    assert body["entries"][0]["entry"]["source"] == "storygraph"
    assert body["entries"][0]["status"] == "matched"

    many = "Title,Author\n" + "".join(f"T{i},A\n" for i in range(5))
    windowed = _csv_upload(app_client, many, offset=2, limit=2).json()
    assert [e["entry"]["title"] for e in windowed["entries"]] == ["T2", "T3"]
    assert (windowed["next_offset"], windowed["total_entries"]) == (4, 5)
    assert "preview_window_truncated" in windowed["notes"]


def test_csv_truncation_is_reported(app_client, stub, monkeypatch):
    import app.reading_lists as rl

    monkeypatch.setattr(rl, "MAX_PARSED_ROWS", 2)
    body = _csv_upload(app_client, "Title,Author\nA,x\nB,x\nC,x\n").json()
    assert body["observation"]["complete"] is False and "input_row_cap_reached" in body["notes"]


@pytest.mark.parametrize(
    ("content", "status", "code"),
    [
        (b"", 422, "csv_empty"),
        (b"Foo,Bar\n1,2\n", 422, "csv_format_unrecognized"),
        ("Title,Author\nCaf\xe9,Y\n".encode("latin-1"), 422, "csv_encoding"),
        (b"Title,Author\nX\x00,Y\n", 422, "csv_invalid"),
    ],
)
def test_csv_preview_malformed_uploads(app_client, stub, content, status, code):
    resp = _csv_upload(app_client, content)
    assert resp.status_code == status and resp.json()["detail"]["code"] == code
    assert "Caf" not in resp.text and stub.search_calls == []


def test_csv_preview_rejects_oversized_upload(app_client, stub, monkeypatch):
    import app.api.routes_import_lists as routes
    import app.reading_lists as rl

    monkeypatch.setattr(routes, "MAX_CSV_BYTES", 200)
    monkeypatch.setattr(rl, "MAX_CSV_BYTES", 200)
    resp = _csv_upload(app_client, "Title,Author\n" + "Title,Author\n" * 50_000)
    assert resp.status_code == 413 and resp.json()["detail"]["code"] == "csv_too_large"
    assert stub.search_calls == []


def test_csv_preview_validates_form_bounds(app_client, stub):
    assert _csv_upload(app_client, SG_CSV, limit=500).status_code == 422
    assert _csv_upload(app_client, SG_CSV, offset=-1).status_code == 422
    assert _csv_upload(app_client, SG_CSV, format="bogus").status_code == 422


def test_provider_failure_marks_entry_lookup_failed_not_whole_preview(app_client, stub, monkeypatch):
    async def boom(query, **kwargs):
        raise RuntimeError(f"upstream exploded {SECRET}")

    monkeypatch.setattr(stub, "search", boom)
    # chain.search swallows provider errors itself; force the exception past it too.
    async def chain_boom(self, query, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(ProviderChain, "search", chain_boom)
    resp = _csv_upload(app_client, SG_CSV)
    assert resp.status_code == 200
    assert resp.json()["entries"][0]["status"] == "lookup_failed"
    assert SECRET not in resp.text


# -- selected import -----------------------------------------------------------------------


def _import(app_client, selections, source_id=None):
    if source_id:
        return app_client.post(
            f"/api/v1/import-lists/goodreads/sources/{source_id}/import", json={"selections": selections}
        )
    return app_client.post("/api/v1/import-lists/csv/import", json={"selections": selections})


def test_import_creates_monitored_book_from_provider_detail_not_client_fields(app_client, stub):
    stub.details["B0REAL0001"] = detail("B0REAL0001", "The Real Title", ("Real Author",))
    resp = _import(
        app_client,
        [{
            "entry_key": "gr:1", "provider_name": "stub", "provider_uid": "B0REAL0001",
            "title": "HACKED", "authors": ["Mallory"], "asin": "B0FAKE0000",
        }],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["created"], body["skipped_existing"], body["skipped"], body["errors"]) == (1, 0, 0, 0)
    item = body["items"][0]
    assert item["status"] == "created" and item["book_id"]

    book = app_client.get(f"/api/v1/library/books/{item['book_id']}").json()
    assert book["title"] == "The Real Title" and book["monitored"] is True
    assert [a["name"] if isinstance(a, dict) else a for a in book["authors"]] == ["Real Author"]
    assert {"provider": "stub", "provider_id": "B0REAL0001", "locale": "us"} in book["provider_ids"]
    assert book["release_date"] == "2020-05-01" and book["series_position"] == 2
    assert stub.detail_calls == ["B0REAL0001"]
    assert "B0FAKE0000" not in json.dumps(book)


def test_import_is_idempotent_and_dedupes_across_providers(app_client, stub):
    stub.details["B0DUPE0001"] = detail("B0DUPE0001", "Dupe")
    selection = {"entry_key": "gr:1", "provider_name": "stub", "provider_uid": "B0DUPE0001"}
    first = _import(app_client, [selection]).json()
    second = _import(app_client, [selection, {**selection, "entry_key": "gr:2"}]).json()
    assert first["created"] == 1
    assert (second["created"], second["skipped_existing"]) == (0, 2)
    assert second["items"][0]["reason"] == "already_in_library"
    assert second["items"][0]["book_id"] == first["items"][0]["book_id"]
    assert _book_count() == 1
    assert stub.detail_calls == ["B0DUPE0001"]  # known ids short-circuit before any provider call

    # A book created earlier by another path (Liberatarr stores ASINs under provider "audible")
    # is recognized by its ASIN, whatever provider the selection names.
    with get_conn() as conn:
        from app.library import BookCreate, create_book

        existing = create_book(conn, BookCreate(title="Owned", provider="audible", provider_id="B0OWNED001"))
    stub.details["B0OWNED001"] = detail("B0OWNED001", "Owned")
    owned = {"entry_key": "gr:3", "provider_name": "stub", "provider_uid": "B0OWNED001"}
    third = _import(app_client, [owned]).json()
    assert third["skipped_existing"] == 1 and third["items"][0]["book_id"] == existing
    assert _book_count() == 2


def test_import_skips_unmatched_unverifiable_and_invalid_selections(app_client, stub):
    stub.details["B0MISMATCH"] = detail("B0OTHERONE", "Different Book")  # provider answers with another id
    stub.details["B0NOTITLE1"] = detail("B0NOTITLE1", "   ")
    stub.details["B0GONE0001"] = None
    sels = [
        {"entry_key": "e1"},
        {"entry_key": "e2", "provider_name": "stub", "provider_uid": "B0MISMATCH"},
        {"entry_key": "e3", "provider_name": "stub", "provider_uid": "B0NOTITLE1"},
        {"entry_key": "e4", "provider_name": "stub", "provider_uid": "B0GONE0001"},
        {"entry_key": "e5", "provider_name": "goodreads", "provider_uid": "123"},
        {"entry_key": "e6", "provider_name": "stub", "provider_uid": "../../etc/passwd"},
        {"entry_key": "e7", "provider_name": "stub", "provider_uid": "B0OK000001", "locale": "zz"},
    ]
    body = _import(app_client, sels).json()
    assert [(i["entry_key"], i["status"], i["reason"]) for i in body["items"]] == [
        ("e1", "skipped_unmatched", "no_selection"),
        ("e2", "skipped_unverifiable", "identity_mismatch"),
        ("e3", "skipped_unverifiable", "provider_lookup_failed"),
        ("e4", "skipped_unverifiable", "provider_lookup_failed"),
        ("e5", "skipped_invalid", "invalid_provider"),
        ("e6", "skipped_invalid", "invalid_provider_id"),
        ("e7", "skipped_invalid", "invalid_locale"),
    ]
    assert (body["created"], body["skipped"]) == (0, 7) and all(i["message"] for i in body["items"])
    assert _book_count() == 0
    assert "../../etc/passwd" not in stub.detail_calls  # path-like ids never reach the provider


def test_import_enforces_selection_bounds_and_dedupes_entry_keys(app_client, stub):
    too_many = [{"entry_key": f"e{i}"} for i in range(51)]
    assert _import(app_client, too_many).status_code == 422
    body = _import(app_client, [{"entry_key": "same"}, {"entry_key": "same"}]).json()
    assert len(body["items"]) == 1


def test_one_failing_create_does_not_abort_the_batch(app_client, stub, monkeypatch):
    import app.reading_list_import as rli

    stub.details["B0FIRST001"] = detail("B0FIRST001", "First")
    stub.details["B0SECOND01"] = detail("B0SECOND01", "Second")
    real = rli.create_book

    def flaky(conn, data):
        if data.title == "First":
            raise RuntimeError(f"db exploded {SECRET}")
        return real(conn, data)

    monkeypatch.setattr(rli, "create_book", flaky)
    resp = _import(
        app_client,
        [
            {"entry_key": "a", "provider_name": "stub", "provider_uid": "B0FIRST001"},
            {"entry_key": "b", "provider_name": "stub", "provider_uid": "B0SECOND01"},
        ],
    )
    body = resp.json()
    assert (body["created"], body["errors"]) == (1, 1) and body["items"][0]["reason"] == "create_failed"
    assert SECRET not in resp.text


def test_get_detail_exceptions_do_not_abort_batch_or_leak(app_client, stub, monkeypatch):
    # Provider-level raise: ProviderChain.get_detail already degrades it to "no detail".
    async def provider_boom(external_id, **kwargs):
        if external_id == "B0BOOM0001":
            raise RuntimeError(f"provider exploded {SECRET}")
        return detail(external_id, "Fine")

    monkeypatch.setattr(stub, "get_detail", provider_boom)

    # Chain-level raise (e.g. provider construction): the import must still guard it.
    real = ProviderChain.get_detail

    async def chain_boom(self, provider_name, external_id, **kwargs):
        if external_id == "B0CHAIN001":
            raise RuntimeError(f"chain exploded {SECRET}")
        return await real(self, provider_name, external_id, **kwargs)

    monkeypatch.setattr(ProviderChain, "get_detail", chain_boom)
    sels = [
        {"entry_key": f"k{i}", "provider_name": "stub", "provider_uid": uid}
        for i, uid in enumerate(["B0BOOM0001", "B0CHAIN001", "B0GOOD0001"])
    ]
    resp = _import(app_client, sels)
    body = resp.json()
    assert [(i["status"], i["reason"]) for i in body["items"]] == [
        ("skipped_unverifiable", "provider_lookup_failed"),
        ("skipped_unverifiable", "provider_lookup_failed"),
        ("created", ""),
    ]
    assert (body["created"], body["skipped"]) == (1, 2)
    assert SECRET not in resp.text and _book_count() == 1


def test_saved_source_import_records_status_and_absence_never_removes(app_client, stub, feed, caplog):
    caplog.set_level(logging.DEBUG)
    source = _save_source(app_client)
    stub.details["B0SHELF001"] = detail("B0SHELF001", "On Shelf")
    stub.hits["On Shelf"] = [hit("B0SHELF001", "On Shelf")]
    feed.body = rss(("On Shelf", "1", "Ann Author", ""))
    base = f"/api/v1/import-lists/goodreads/sources/{source['id']}"

    result = app_client.post(
        f"{base}/import",
        json={"selections": [{"entry_key": "gr:1", "provider_name": "stub", "provider_uid": "B0SHELF001"}]},
    ).json()
    book_id = result["items"][0]["book_id"]
    listed = app_client.get("/api/v1/import-lists").json()[1]
    assert listed["status"] == "ok" and listed["last_created"] == 1 and listed["last_sync_at"]

    # A later observation of the shelf no longer contains the book.
    feed.body = rss()
    preview = app_client.post(f"{base}/preview", json={}).json()
    assert preview["entries"] == [] and "absence_never_removes" in preview["notes"]
    book = app_client.get(f"/api/v1/library/books/{book_id}").json()
    assert book["monitored"] is True and _book_count() == 1

    again = app_client.post(
        f"{base}/import",
        json={"selections": [{"entry_key": "gr:1", "provider_name": "stub", "provider_uid": "B0SHELF001"}]},
    ).json()
    assert again["skipped_existing"] == 1
    assert app_client.get("/api/v1/import-lists").json()[1]["last_skipped"] == 1
    assert "list_rss" not in caplog.text and "On Shelf" not in caplog.text


def test_import_never_triggers_downloads(app_client, stub, monkeypatch):
    import app.api.routes_wanted as wanted

    called: list[str] = []
    for name in dir(wanted):
        if name.startswith(("search_", "grab_")) and callable(getattr(wanted, name)):
            monkeypatch.setattr(wanted, name, lambda *a, **k: called.append("x"))
    stub.details["B0QUIET001"] = detail("B0QUIET001", "Quiet")
    _import(app_client, [{"entry_key": "q", "provider_name": "stub", "provider_uid": "B0QUIET001"}])
    assert called == []


def test_csv_import_endpoint_accepts_source_type(app_client, stub):
    stub.details["B0SG000001"] = detail("B0SG000001", "From SG")
    resp = app_client.post(
        "/api/v1/import-lists/csv/import",
        json={
            "source_type": "storygraph_csv",
            "selections": [{"entry_key": "h:abc", "provider_name": "stub", "provider_uid": "B0SG000001"}],
        },
    )
    assert resp.status_code == 200 and resp.json()["created"] == 1
    bad = app_client.post("/api/v1/import-lists/csv/import", json={"source_type": "x", "selections": []})
    assert bad.status_code == 422

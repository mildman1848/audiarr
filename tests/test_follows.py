"""Author/series follows API tests (#80). The provider chain is mocked via a
mock-transport Audible provider injected through dependency_overrides."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.api.routes_metadata import build_provider_chain
from app.db import get_conn
from app.providers.audible import AudibleProvider, api_host_for
from app.providers.chain import ProviderChain, ProviderChainConfig

FUTURE = "2999-01-01"
PAST = "2010-11-08"


def _product(asin: str, title: str, release_date: str = PAST, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "asin": asin,
        "title": title,
        "authors": [{"name": "Andy Weir"}],
        "release_date": release_date,
        "series": [{"title": "Artemis Saga", "sequence": "1"}],
    }
    base.update(overrides)
    return base


@pytest.fixture()
def provider_products(app_client) -> list[dict[str, Any]]:
    """Mutable product list served by the mocked Audible provider; recorded
    request params are exposed as ``app_client.follow_requests``."""
    products: list[dict[str, Any]] = []
    requests: list[dict[str, str]] = []
    # Test knobs: force an HTTP error status / a larger provider-side total.
    behavior: dict[str, Any] = {"status": 200, "total": None}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        if behavior["status"] != 200:
            return httpx.Response(behavior["status"], json={"message": "boom"})
        total = behavior["total"] if behavior["total"] is not None else len(products)
        return httpx.Response(200, json={"products": products, "total_results": total})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=api_host_for("us"))
    chain = ProviderChain(
        config=ProviderChainConfig(provider_order=["audible"], audible_locale="us"),
        provider_overrides={"audible": AudibleProvider(client=client, region="us")},
    )
    app_client.app.dependency_overrides[build_provider_chain] = lambda: chain
    app_client.follow_requests = requests  # for param assertions
    app_client.follow_behavior = behavior
    return products


def _follow(app_client, kind="author", name="Andy Weir") -> dict:
    resp = app_client.post("/api/v1/follows", json={"kind": kind, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _candidates(app_client, follow_id: int) -> dict[str, dict]:
    resp = app_client.get(f"/api/v1/follows/{follow_id}/candidates")
    assert resp.status_code == 200
    return {c["provider_book_id"]: c for c in resp.json()}


def _books(app_client) -> list[dict]:
    return app_client.get("/api/v1/library/books").json()


# -- persistence --------------------------------------------------------------


def test_follow_create_list_delete(app_client):
    follow = _follow(app_client)
    assert follow["kind"] == "author"
    assert follow["last_refreshed_at"] is None
    assert follow["candidate_counts"] == {"future": 0, "backlog": 0, "excluded": 0, "added": 0}

    listed = app_client.get("/api/v1/follows").json()
    assert [f["name"] for f in listed] == ["Andy Weir"]

    assert app_client.delete(f"/api/v1/follows/{follow['id']}").status_code == 204
    assert app_client.get("/api/v1/follows").json() == []
    assert app_client.delete(f"/api/v1/follows/{follow['id']}").status_code == 404


def test_follow_duplicate_is_409_case_insensitive(app_client):
    _follow(app_client)
    resp = app_client.post("/api/v1/follows", json={"kind": "author", "name": "andy weir"})
    assert resp.status_code == 409
    # Same name as a series is a distinct follow.
    assert app_client.post(
        "/api/v1/follows", json={"kind": "series", "name": "Andy Weir"}
    ).status_code == 201


def test_follow_rejects_bad_kind_and_empty_name(app_client):
    assert app_client.post("/api/v1/follows", json={"kind": "publisher", "name": "x"}).status_code == 422
    assert app_client.post("/api/v1/follows", json={"kind": "author", "name": "  "}).status_code == 422


# -- refresh ------------------------------------------------------------------


def test_refresh_future_only_default_creates_no_backlog_books(app_client, provider_products):
    provider_products += [
        _product("B0FUTURE01", "Future Book", FUTURE),
        _product("B0BACKLOG1", "Old Book", PAST),
        _product("B0UNDATED1", "Undated Book", ""),
    ]
    follow = _follow(app_client)

    resp = app_client.post(f"/api/v1/follows/{follow['id']}/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert (body["found"], body["new"], body["future_created"]) == (3, 3, 1)

    cands = _candidates(app_client, follow["id"])
    assert cands["B0FUTURE01"]["status"] == "added"
    assert cands["B0BACKLOG1"]["status"] == "backlog"
    assert cands["B0UNDATED1"]["status"] == "backlog"

    books = _books(app_client)
    assert [b["title"] for b in books] == ["Future Book"]
    assert books[0]["monitored"] is True
    assert books[0]["release_date"] == FUTURE
    # Author kwarg was forwarded to the provider (blank keywords).
    assert app_client.follow_requests[-1].get("author") == "Andy Weir"
    assert "keywords" not in app_client.follow_requests[-1]

    follows = app_client.get("/api/v1/follows").json()
    assert follows[0]["last_refreshed_at"]
    assert follows[0]["last_result"]["future_created"] == 1
    assert follows[0]["candidate_counts"]["backlog"] == 2


def test_refresh_is_idempotent_and_never_resets_status(app_client, provider_products):
    provider_products += [
        _product("B0FUTURE01", "Future Book", FUTURE),
        _product("B0BACKLOG1", "Old Book", PAST),
        _product("B0BACKLOG2", "Other Old Book", PAST),
    ]
    follow = _follow(app_client)
    fid = follow["id"]
    app_client.post(f"/api/v1/follows/{fid}/refresh")
    cands = _candidates(app_client, fid)
    excluded_id = cands["B0BACKLOG1"]["id"]
    app_client.post(f"/api/v1/follows/{fid}/candidates/exclude", json={"ids": [excluded_id]})
    added_id = cands["B0BACKLOG2"]["id"]
    app_client.post(f"/api/v1/follows/{fid}/candidates/add", json={"ids": [added_id]})
    before = _candidates(app_client, fid)
    book_count = len(_books(app_client))

    second = app_client.post(f"/api/v1/follows/{fid}/refresh").json()
    assert (second["new"], second["future_created"]) == (0, 0)

    after = _candidates(app_client, fid)
    assert after == before
    assert after["B0BACKLOG1"]["status"] == "excluded"
    assert after["B0BACKLOG2"]["status"] == "added"
    assert after["B0FUTURE01"]["status"] == "added"
    assert len(_books(app_client)) == book_count == 2
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM follow_candidates").fetchone()[0] == 3


def test_refresh_reclassifies_pending_candidates_but_keeps_exclusions(app_client, provider_products):
    provider_products += [
        _product("B0SLIP0001", "Slipping Book", PAST),
        _product("B0EXCL0001", "Excluded Book", PAST),
    ]
    follow = _follow(app_client)
    fid = follow["id"]
    app_client.post(f"/api/v1/follows/{fid}/refresh")
    cands = _candidates(app_client, fid)
    app_client.post(
        f"/api/v1/follows/{fid}/candidates/exclude", json={"ids": [cands["B0EXCL0001"]["id"]]}
    )

    # Provider moves both release dates into the future.
    for p in provider_products:
        p["release_date"] = FUTURE
    app_client.post(f"/api/v1/follows/{fid}/refresh")

    after = _candidates(app_client, fid)
    assert after["B0SLIP0001"]["status"] == "added"  # became future -> monitored
    assert after["B0EXCL0001"]["status"] == "excluded"
    assert after["B0EXCL0001"]["release_date"] == FUTURE  # metadata still updated
    assert [b["title"] for b in _books(app_client)] == ["Slipping Book"]


def test_owned_books_are_flagged_and_not_added(app_client, provider_products):
    # Already in the library under the same provider id, different locale.
    resp = app_client.post(
        "/api/v1/library/books",
        json={"title": "Owned Future", "provider": "audible", "provider_id": "B0OWNED001", "locale": "de"},
    )
    assert resp.status_code == 201
    owned_book_id = resp.json()["id"]
    provider_products += [
        _product("B0OWNED001", "Owned Future", FUTURE),
        _product("B0OWNED002", "Owned Backlog", PAST),
        _product("B0MISSING1", "Missing Backlog", PAST),
    ]
    # Second one is only known via books.asin (backfill-style).
    app_client.post("/api/v1/library/books", json={"title": "Owned Backlog"})
    with get_conn() as conn:
        conn.execute("UPDATE books SET asin = 'B0OWNED002' WHERE title = 'Owned Backlog'")

    follow = _follow(app_client)
    fid = follow["id"]
    body = app_client.post(f"/api/v1/follows/{fid}/refresh").json()
    assert body["future_created"] == 0
    assert body["owned"] == 2

    cands = _candidates(app_client, fid)
    assert cands["B0OWNED001"]["owned"] is True
    assert cands["B0OWNED001"]["book_id"] == owned_book_id
    assert cands["B0OWNED001"]["status"] == "future"  # not "added": no book created
    assert cands["B0OWNED002"]["owned"] is True
    assert cands["B0MISSING1"]["owned"] is False
    assert len(_books(app_client)) == 2

    add = app_client.post(
        f"/api/v1/follows/{fid}/candidates/add",
        json={"ids": [cands["B0OWNED002"]["id"], cands["B0MISSING1"]["id"]]},
    ).json()
    assert add["changed"] == [cands["B0MISSING1"]["id"]]
    assert add["skipped"] == [{"id": cands["B0OWNED002"]["id"], "reason": "owned"}]
    assert len(_books(app_client)) == 3


# -- exclusion / explicit add -------------------------------------------------


def test_exclude_restore_and_add_rules(app_client, provider_products):
    provider_products += [
        _product("B0FUTURE01", "Future Book", FUTURE),
        _product("B0BACKLOG1", "Old Book", PAST),
        _product("B0BACKLOG2", "Other Old Book", PAST),
    ]
    follow = _follow(app_client)
    fid = follow["id"]
    app_client.post(f"/api/v1/follows/{fid}/refresh")
    cands = _candidates(app_client, fid)
    base = f"/api/v1/follows/{fid}/candidates"

    # Added candidates cannot be excluded; unknown ids are reported.
    out = app_client.post(
        f"{base}/exclude", json={"ids": [cands["B0FUTURE01"]["id"], 99999]}
    ).json()
    assert out["changed"] == []
    assert {s["reason"] for s in out["skipped"]} == {"added", "not_found"}

    b1, b2 = cands["B0BACKLOG1"]["id"], cands["B0BACKLOG2"]["id"]
    assert app_client.post(f"{base}/exclude", json={"ids": [b1]}).json()["changed"] == [b1]

    # Excluded candidates are never added.
    out = app_client.post(f"{base}/add", json={"ids": [b1]}).json()
    assert out["changed"] == []
    assert out["skipped"] == [{"id": b1, "reason": "excluded"}]
    assert len(_books(app_client)) == 1

    # Restore returns it to backlog (still no book); then explicit add works.
    assert app_client.post(f"{base}/restore", json={"ids": [b1]}).json()["changed"] == [b1]
    assert _candidates(app_client, fid)["B0BACKLOG1"]["status"] == "backlog"
    assert len(_books(app_client)) == 1
    out = app_client.post(f"{base}/add", json={"ids": [b1, b2]}).json()
    assert sorted(out["changed"]) == sorted([b1, b2])
    assert len(_books(app_client)) == 3
    again = app_client.post(f"{base}/add", json={"ids": [b1]}).json()
    assert again["skipped"] == [{"id": b1, "reason": "added"}]
    assert len(_books(app_client)) == 3

    created = {b["title"]: b for b in _books(app_client)}["Old Book"]
    assert created["monitored"] is True
    assert created["quality_profile"] == ""  # inherits the default profile
    assert created["root_folder_id"] is None  # inherits the default root folder
    assert created["provider_ids"][0]["provider_id"] == "B0BACKLOG1"


def test_restoring_excluded_future_candidate_creates_monitored_book(app_client, provider_products):
    provider_products.append(_product("B0FUTURE01", "Future Book", FUTURE))
    follow = _follow(app_client)
    fid = follow["id"]
    # Exclude before any refresh creates the book: insert candidate via a
    # refresh against a past date, then move it to the future.
    provider_products[0]["release_date"] = PAST
    app_client.post(f"/api/v1/follows/{fid}/refresh")
    cid = _candidates(app_client, fid)["B0FUTURE01"]["id"]
    app_client.post(f"/api/v1/follows/{fid}/candidates/exclude", json={"ids": [cid]})
    provider_products[0]["release_date"] = FUTURE
    app_client.post(f"/api/v1/follows/{fid}/refresh")
    assert _books(app_client) == []  # excluded stays excluded, no book

    app_client.post(f"/api/v1/follows/{fid}/candidates/restore", json={"ids": [cid]})
    assert _candidates(app_client, fid)["B0FUTURE01"]["status"] == "added"
    assert [b["title"] for b in _books(app_client)] == ["Future Book"]


def test_candidate_actions_are_scoped_to_their_follow(app_client, provider_products):
    provider_products.append(_product("B0BACKLOG1", "Old Book", PAST))
    first = _follow(app_client)
    other = _follow(app_client, kind="series", name="Artemis Saga")
    app_client.post(f"/api/v1/follows/{first['id']}/refresh")
    cid = _candidates(app_client, first["id"])["B0BACKLOG1"]["id"]

    out = app_client.post(
        f"/api/v1/follows/{other['id']}/candidates/add", json={"ids": [cid]}
    ).json()
    assert out["changed"] == []
    assert out["skipped"] == [{"id": cid, "reason": "not_found"}]
    assert _books(app_client) == []


# -- series follows -----------------------------------------------------------


def test_series_follow_filters_to_matching_series_name(app_client, provider_products):
    provider_products += [
        _product("B0SERIES01", "Book One", FUTURE),
        _product("B0SERIES02", "Book Two", PAST, series=[{"title": "ARTEMIS saga", "sequence": "2"}]),
        _product("B0OTHER001", "Unrelated", PAST, series=[{"title": "Other Series", "sequence": "1"}]),
        _product("B0NOSERIES", "Standalone", PAST, series=[]),
    ]
    follow = _follow(app_client, kind="series", name="Artemis Saga")
    body = app_client.post(f"/api/v1/follows/{follow['id']}/refresh").json()
    assert body["found"] == 2

    cands = _candidates(app_client, follow["id"])
    assert set(cands) == {"B0SERIES01", "B0SERIES02"}
    assert cands["B0SERIES02"]["series_position"] == 2
    assert app_client.follow_requests[-1].get("keywords") == "Artemis Saga"


def test_author_follow_filters_to_exact_author(app_client, provider_products):
    provider_products += [
        _product("B0MINE0001", "Mine", PAST),
        _product("B0OTHER001", "Other Author Book", PAST, authors=[{"name": "Andy Weirdo"}]),
    ]
    follow = _follow(app_client)
    app_client.post(f"/api/v1/follows/{follow['id']}/refresh")
    assert set(_candidates(app_client, follow["id"])) == {"B0MINE0001"}


def test_refresh_unknown_follow_is_404(app_client, provider_products):
    assert app_client.post("/api/v1/follows/999/refresh").status_code == 404
    assert app_client.get("/api/v1/follows/999/candidates").status_code == 404


def test_delete_follow_keeps_created_books(app_client, provider_products):
    provider_products.append(_product("B0FUTURE01", "Future Book", FUTURE))
    follow = _follow(app_client)
    app_client.post(f"/api/v1/follows/{follow['id']}/refresh")
    assert app_client.delete(f"/api/v1/follows/{follow['id']}").status_code == 204
    assert [b["title"] for b in _books(app_client)] == ["Future Book"]
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM follow_candidates").fetchone()[0] == 0


# -- failure reporting --------------------------------------------------------


def test_provider_failure_is_502_not_an_empty_success(app_client, provider_products):
    provider_products.append(_product("B0BACKLOG1", "Old Book", PAST))
    follow = _follow(app_client)
    assert app_client.post(f"/api/v1/follows/{follow['id']}/refresh").status_code == 200
    refreshed_at = app_client.get("/api/v1/follows").json()[0]["last_refreshed_at"]

    app_client.follow_behavior["status"] = 503
    resp = app_client.post(f"/api/v1/follows/{follow['id']}/refresh")
    assert resp.status_code == 502
    assert "Provider search failed" in resp.json()["detail"]

    listed = app_client.get("/api/v1/follows").json()[0]
    assert listed["last_result"]["status"] == "failed"
    assert "audible" in listed["last_result"]["errors"]
    assert listed["last_refreshed_at"] == refreshed_at  # no fake refresh timestamp
    # Existing candidates are untouched by the failed refresh.
    assert set(_candidates(app_client, follow["id"])) == {"B0BACKLOG1"}


def test_genuine_empty_result_is_still_ok(app_client, provider_products):
    follow = _follow(app_client)
    body = app_client.post(f"/api/v1/follows/{follow['id']}/refresh").json()
    assert body["status"] == "ok"
    assert body["found"] == 0
    assert "errors" not in body["result"]


def test_truncated_provider_page_is_reported_partial(app_client, provider_products):
    provider_products.append(_product("B0BACKLOG1", "Old Book", PAST))
    app_client.follow_behavior["total"] = 180
    follow = _follow(app_client)
    body = app_client.post(f"/api/v1/follows/{follow['id']}/refresh").json()
    assert body["status"] == "partial"
    assert body["result"]["truncated"] is True
    assert body["result"]["total_results"] == 180
    assert body["new"] == 1  # what was fetched is still stored


async def test_chain_reports_provider_errors_for_failure_and_fallback():
    from app.providers.base import BaseMetadataProvider, BookQuickInfo, SearchResponse

    class Boom(BaseMetadataProvider):
        provider_name = "boom"

        async def search(self, query, **kwargs):
            raise RuntimeError("kaput")

    class Ok(BaseMetadataProvider):
        provider_name = "ok"

        async def search(self, query, **kwargs):
            return SearchResponse(results=[BookQuickInfo(provider_uid="1", provider_name="ok", title="T")])

    chain = ProviderChain(
        config=ProviderChainConfig(provider_order=["boom", "ok"]),
        provider_overrides={"boom": Boom(), "ok": Ok()},
    )
    resp = await chain.search("x")
    assert [r.title for r in resp.results] == ["T"]
    assert resp.provider_metadata["provider_errors"]["boom"].startswith("RuntimeError")

    only_boom = ProviderChain(
        config=ProviderChainConfig(provider_order=["boom"]), provider_overrides={"boom": Boom()}
    )
    resp = await only_boom.search("x")
    assert resp.results == []
    assert "boom" in resp.provider_metadata["provider_errors"]


# -- deleted books ------------------------------------------------------------


def test_deleted_added_book_candidate_is_reviewable_again(app_client, provider_products):
    provider_products += [
        _product("B0BACKLOG1", "Old Book", PAST),
        _product("B0FUTURE01", "Future Book", FUTURE),
    ]
    follow = _follow(app_client)
    fid = follow["id"]
    app_client.post(f"/api/v1/follows/{fid}/refresh")
    cands = _candidates(app_client, fid)
    app_client.post(f"/api/v1/follows/{fid}/candidates/add", json={"ids": [cands["B0BACKLOG1"]["id"]]})
    assert {c["status"] for c in _candidates(app_client, fid).values()} == {"added"}

    for book in _books(app_client):
        assert app_client.delete(f"/api/v1/library/books/{book['id']}").status_code == 204

    # Not "owned" (stale provider_ids row must not count) and no dangling book.
    cands = _candidates(app_client, fid)
    assert cands["B0BACKLOG1"]["status"] == "backlog"
    assert cands["B0BACKLOG1"]["owned"] is False
    assert cands["B0BACKLOG1"]["book_id"] is None
    assert cands["B0FUTURE01"]["status"] == "future"
    counts = app_client.get("/api/v1/follows").json()[0]["candidate_counts"]
    assert counts["added"] == 0

    # Explicit re-add works and creates exactly one new book.
    add = app_client.post(
        f"/api/v1/follows/{fid}/candidates/add", json={"ids": [cands["B0BACKLOG1"]["id"]]}
    ).json()
    assert add["changed"] == [cands["B0BACKLOG1"]["id"]]
    assert [b["title"] for b in _books(app_client)] == ["Old Book"]
    assert _candidates(app_client, fid)["B0BACKLOG1"]["status"] == "added"

    # A refresh re-materializes the still-unreleased book only; nothing duplicates.
    body = app_client.post(f"/api/v1/follows/{fid}/refresh").json()
    assert body["future_created"] == 1
    assert sorted(b["title"] for b in _books(app_client)) == ["Future Book", "Old Book"]
    app_client.post(f"/api/v1/follows/{fid}/refresh")
    assert len(_books(app_client)) == 2


def test_deleted_book_relinks_when_another_book_owns_the_provider_id(app_client, provider_products):
    provider_products.append(_product("B0BACKLOG1", "Old Book", PAST))
    follow = _follow(app_client)
    fid = follow["id"]
    app_client.post(f"/api/v1/follows/{fid}/refresh")
    cid = _candidates(app_client, fid)["B0BACKLOG1"]["id"]
    app_client.post(f"/api/v1/follows/{fid}/candidates/add", json={"ids": [cid]})
    (book,) = _books(app_client)
    app_client.delete(f"/api/v1/library/books/{book['id']}")
    other = app_client.post(
        "/api/v1/library/books",
        json={"title": "Re-imported", "provider": "audible", "provider_id": "B0BACKLOG1"},
    ).json()

    cand = _candidates(app_client, fid)["B0BACKLOG1"]
    assert cand["status"] == "added"
    assert cand["owned"] is True
    assert cand["book_id"] == other["id"]
    assert len(_books(app_client)) == 1


# -- data integrity -----------------------------------------------------------


def test_author_names_with_commas_survive_candidate_to_book(app_client, provider_products):
    name = "Martin Luther King, Jr."
    provider_products.append(_product("B0COMMA001", "Speeches", PAST, authors=[{"name": name}]))
    follow = _follow(app_client, name=name)
    app_client.post(f"/api/v1/follows/{follow['id']}/refresh")
    cand = _candidates(app_client, follow["id"])["B0COMMA001"]
    assert cand["authors"] == [name]
    app_client.post(f"/api/v1/follows/{follow['id']}/candidates/add", json={"ids": [cand["id"]]})
    # Check the stored author rows (the library API re-splits names on ", " itself).
    with get_conn() as conn:
        names = [r["name"] for r in conn.execute("SELECT name FROM authors")]
    assert names == [name]


def test_follow_name_length_is_bounded(app_client):
    resp = app_client.post("/api/v1/follows", json={"kind": "author", "name": "x" * 201})
    assert resp.status_code == 422


def test_add_candidates_twice_creates_one_book(app_client, provider_products):
    provider_products.append(_product("B0BACKLOG1", "Old Book", PAST))
    follow = _follow(app_client)
    fid = follow["id"]
    app_client.post(f"/api/v1/follows/{fid}/refresh")
    cid = _candidates(app_client, fid)["B0BACKLOG1"]["id"]
    ids = {"ids": [cid, cid]}
    first = app_client.post(f"/api/v1/follows/{fid}/candidates/add", json=ids).json()
    second = app_client.post(f"/api/v1/follows/{fid}/candidates/add", json=ids).json()
    assert first["changed"] == [cid]
    assert second["changed"] == []
    assert len(_books(app_client)) == 1

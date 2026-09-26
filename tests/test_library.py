"""Library store tests: CRUD + provider attribution + API endpoints."""

from __future__ import annotations

import app.api.routes_library as routes_library
from app.db import get_conn, migrate
from app.library import (
    BookCreate,
    RootFolderCreate,
    create_book,
    create_root_folder,
    find_book_by_provider_id,
    get_book,
    get_root_folder,
    list_root_folders,
    provider_id_exists,
    update_book,
)
from app.providers.base import (
    BaseMetadataProvider,
    BookDetailInfo,
    BrowseResponse,
    SearchResponse,
)
from app.providers.chain import ProviderChain, ProviderChainConfig


class _StubProvider(BaseMetadataProvider):
    """Offline provider: resolves a single canned detail record, never searches."""

    provider_name = "stub"

    def __init__(self, detail: BookDetailInfo | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.detail = detail

    async def search(self, query: str, **kwargs: object) -> SearchResponse:
        return SearchResponse(results=[], query_used=query)

    async def get_detail(self, external_id: str, **kwargs: object) -> BookDetailInfo | None:
        if self.detail is not None and self.detail.provider_external_id == external_id:
            return self.detail
        return None

    async def browse(self, **kwargs: object) -> BrowseResponse:
        return BrowseResponse(results=[])


def _stub_chain(detail: BookDetailInfo | None) -> ProviderChain:
    return ProviderChain(
        config=ProviderChainConfig(provider_order=["stub"]),
        provider_overrides={"stub": _StubProvider(detail=detail)},
    )


def _make_book(**overrides: object) -> BookCreate:
    """Build a BookCreate with sane defaults; overrides win."""
    payload: dict[str, object] = dict(
        title="Der Vorleser",
        authors=["Bernhard Schlink"],
        narrators=["Hans Korte"],
        language="de",
        duration_seconds=298 * 60,
        series="",
        provider="audible",
        provider_id="B004UWRY6M",
        locale="de",
    )
    payload.update(overrides)
    return BookCreate(**payload)  # type: ignore[arg-type]


def test_create_and_get_book_with_attribution(tmp_path):
    from app.db import set_db_path_override

    set_db_path_override(tmp_path / "lib.db")
    migrate(tmp_path / "lib.db")
    try:
        with get_conn() as conn:
            book_id = create_book(conn, _make_book())
            book = get_book(conn, book_id)

        assert book is not None
        assert book["title"] == "Der Vorleser"
        assert book["authors"] == "Bernhard Schlink"
        assert book["narrators"] == "Hans Korte"

        with get_conn() as conn:
            assert provider_id_exists(conn, "audible", "B004UWRY6M", "de")
            assert find_book_by_provider_id(conn, "audible", "B004UWRY6M", "de") == book_id
    finally:
        set_db_path_override(None)


def test_series_and_position_persisted(tmp_path):
    from app.db import set_db_path_override

    set_db_path_override(tmp_path / "lib.db")
    migrate(tmp_path / "lib.db")
    try:
        with get_conn() as conn:
            book_id = create_book(
                conn,
                _make_book(
                    title="Harry Potter und der Stein der Weisen",
                    series="Harry Potter",
                    series_position=1.0,
                ),
            )
            book = get_book(conn, book_id)
        assert book["series_name"] == "Harry Potter"
        assert book["series_position"] == 1.0
    finally:
        set_db_path_override(None)


def test_book_defaults_to_monitored(tmp_path):
    from app.db import set_db_path_override

    set_db_path_override(tmp_path / "lib.db")
    migrate(tmp_path / "lib.db")
    try:
        with get_conn() as conn:
            book_id = create_book(conn, _make_book())
            book = get_book(conn, book_id)
        assert book["monitored"] == 1
    finally:
        set_db_path_override(None)


def test_book_can_be_created_unmonitored(tmp_path):
    from app.db import set_db_path_override

    set_db_path_override(tmp_path / "lib.db")
    migrate(tmp_path / "lib.db")
    try:
        with get_conn() as conn:
            book_id = create_book(conn, _make_book(monitored=False))
            book = get_book(conn, book_id)
        assert book["monitored"] == 0
    finally:
        set_db_path_override(None)


def test_update_book_monitored_field(tmp_path):
    from app.db import set_db_path_override

    set_db_path_override(tmp_path / "lib.db")
    migrate(tmp_path / "lib.db")
    try:
        with get_conn() as conn:
            book_id = create_book(conn, _make_book())
            assert update_book(conn, book_id, {"monitored": False})
            book = get_book(conn, book_id)
        assert book["monitored"] == 0
    finally:
        set_db_path_override(None)


def test_update_book_fields(tmp_path):
    from app.db import set_db_path_override

    set_db_path_override(tmp_path / "lib.db")
    migrate(tmp_path / "lib.db")
    try:
        with get_conn() as conn:
            book_id = create_book(conn, _make_book())
            assert update_book(conn, book_id, {"title": "The Reader", "language": "en"})
            book = get_book(conn, book_id)
        assert book["title"] == "The Reader"
        assert book["language"] == "en"
    finally:
        set_db_path_override(None)


def test_root_folder_crud(tmp_path):
    from app.db import set_db_path_override

    set_db_path_override(tmp_path / "lib.db")
    migrate(tmp_path / "lib.db")
    try:
        with get_conn() as conn:
            folder_id = create_root_folder(
                conn, RootFolderCreate(path="/data/audiobooks", label="Main")
            )
            folders = conn.execute("SELECT * FROM root_folders").fetchall()
        assert len(folders) == 1
        assert folders[0]["path"] == "/data/audiobooks"
        assert folder_id == folders[0]["id"]
    finally:
        set_db_path_override(None)


def test_root_folder_import_strategy_defaults_to_hardlink(tmp_path):
    from app.db import set_db_path_override

    set_db_path_override(tmp_path / "lib.db")
    migrate(tmp_path / "lib.db")
    try:
        with get_conn() as conn:
            folder_id = create_root_folder(
                conn, RootFolderCreate(path="/data/audiobooks")
            )
            row = get_root_folder(conn, folder_id)
        assert row["import_strategy"] == "hardlink"
    finally:
        set_db_path_override(None)


def test_root_folder_import_strategy_explicit_value_persists(tmp_path):
    from app.db import set_db_path_override

    set_db_path_override(tmp_path / "lib.db")
    migrate(tmp_path / "lib.db")
    try:
        with get_conn() as conn:
            folder_id = create_root_folder(
                conn, RootFolderCreate(path="/data/audiobooks", import_strategy="move")
            )
            row = get_root_folder(conn, folder_id)
            folders = list_root_folders(conn)
        assert row["import_strategy"] == "move"
        assert folders[0]["import_strategy"] == "move"
    finally:
        set_db_path_override(None)


# -- API endpoints -----------------------------------------------------------


def test_api_root_folder_lifecycle(app_client):
    # Create
    resp = app_client.post(
        "/api/v1/library/root-folders",
        json={"path": "/data/audiobooks", "label": "Main"},
    )
    assert resp.status_code == 201
    folder = resp.json()
    assert folder["path"] == "/data/audiobooks"
    # No import_strategy given -> defaults to "hardlink" (copy is the
    # automatic fallback when linking isn't possible, see #30 / hardlink-default).
    assert folder["import_strategy"] == "hardlink"

    # Duplicate -> 409
    dup = app_client.post(
        "/api/v1/library/root-folders", json={"path": "/data/audiobooks"}
    )
    assert dup.status_code == 409

    # List
    listing = app_client.get("/api/v1/library/root-folders")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["import_strategy"] == "hardlink"

    # Delete
    gone = app_client.delete(f"/api/v1/library/root-folders/{folder['id']}")
    assert gone.status_code == 204
    assert app_client.get("/api/v1/library/root-folders").json() == []


def test_api_root_folder_accepts_explicit_import_strategy(app_client):
    resp = app_client.post(
        "/api/v1/library/root-folders",
        json={"path": "/data/audiobooks", "label": "Main", "import_strategy": "hardlink"},
    )
    assert resp.status_code == 201
    folder = resp.json()
    assert folder["import_strategy"] == "hardlink"

    single = app_client.get("/api/v1/library/root-folders").json()[0]
    assert single["import_strategy"] == "hardlink"


def test_api_root_folder_rejects_invalid_import_strategy(app_client):
    resp = app_client.post(
        "/api/v1/library/root-folders",
        json={"path": "/data/audiobooks", "import_strategy": "delete"},
    )
    assert resp.status_code == 422
    assert app_client.get("/api/v1/library/root-folders").json() == []


def test_api_root_folder_strategy_update_roundtrip(app_client):
    created = app_client.post(
        "/api/v1/library/root-folders", json={"path": "/data/audiobooks"}
    ).json()
    assert created["import_strategy"] == "hardlink"

    updated = app_client.put(
        f"/api/v1/library/root-folders/{created['id']}/strategy",
        json={"import_strategy": "move"},
    )
    assert updated.status_code == 200
    assert updated.json()["import_strategy"] == "move"

    invalid = app_client.put(
        f"/api/v1/library/root-folders/{created['id']}/strategy",
        json={"import_strategy": "not-a-strategy"},
    )
    assert invalid.status_code == 422

    missing = app_client.put(
        "/api/v1/library/root-folders/9999/strategy",
        json={"import_strategy": "move"},
    )
    assert missing.status_code == 404


def test_api_root_folder_list_reports_health_for_existing_path(app_client, tmp_path):
    folder = tmp_path / "audiobooks"
    folder.mkdir()

    app_client.post("/api/v1/library/root-folders", json={"path": str(folder)})
    listing = app_client.get("/api/v1/library/root-folders").json()

    assert len(listing) == 1
    assert listing[0]["exists"] is True
    assert listing[0]["writable"] is True
    assert listing[0]["free_bytes"] > 0
    assert listing[0]["total_bytes"] > 0


def test_api_root_folder_list_reports_unhealthy_for_missing_path(app_client, tmp_path):
    missing = tmp_path / "does-not-exist"

    app_client.post("/api/v1/library/root-folders", json={"path": str(missing)})
    listing = app_client.get("/api/v1/library/root-folders").json()

    assert len(listing) == 1
    assert listing[0]["exists"] is False
    assert listing[0]["writable"] is False
    assert listing[0]["free_bytes"] is None
    assert listing[0]["total_bytes"] is None


def test_api_book_lifecycle_with_provider_attribution(app_client):
    payload = {
        "title": "Der Vorleser",
        "authors": ["Bernhard Schlink"],
        "narrators": ["Hans Korte"],
        "language": "de",
        "duration_seconds": 298 * 60,
        "provider": "audible",
        "provider_id": "B004UWRY6M",
        "locale": "de",
    }
    resp = app_client.post("/api/v1/library/books", json=payload)
    assert resp.status_code == 201
    book = resp.json()
    assert book["authors"] == ["Bernhard Schlink"]
    assert book["provider_ids"] == [
        {"provider": "audible", "provider_id": "B004UWRY6M", "locale": "de"}
    ]

    # Duplicate provider id -> 409
    dup = app_client.post("/api/v1/library/books", json=payload)
    assert dup.status_code == 409

    # Get single
    single = app_client.get(f"/api/v1/library/books/{book['id']}")
    assert single.status_code == 200
    assert single.json()["title"] == "Der Vorleser"

    # Patch
    patched = app_client.patch(
        f"/api/v1/library/books/{book['id']}", json={"title": "The Reader"}
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "The Reader"

    # List
    listing = app_client.get("/api/v1/library/books")
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    # Delete
    gone = app_client.delete(f"/api/v1/library/books/{book['id']}")
    assert gone.status_code == 204
    assert app_client.get("/api/v1/library/books").json() == []


def test_api_book_monitored_roundtrip(app_client):
    # Default: creating a book without specifying monitored -> True.
    payload = {
        "title": "Der Vorleser",
        "authors": ["Bernhard Schlink"],
        "provider": "audible",
        "provider_id": "B004UWRY6M",
        "locale": "de",
    }
    resp = app_client.post("/api/v1/library/books", json=payload)
    assert resp.status_code == 201
    book = resp.json()
    assert book["monitored"] is True

    listing = app_client.get("/api/v1/library/books").json()
    assert listing[0]["monitored"] is True

    single = app_client.get(f"/api/v1/library/books/{book['id']}").json()
    assert single["monitored"] is True

    # Patch flips it off and the change round-trips through get/list.
    patched = app_client.patch(
        f"/api/v1/library/books/{book['id']}", json={"monitored": False}
    )
    assert patched.status_code == 200
    assert patched.json()["monitored"] is False

    single_after = app_client.get(f"/api/v1/library/books/{book['id']}").json()
    assert single_after["monitored"] is False

    # Explicitly creating a book as unmonitored also round-trips.
    unmonitored_payload = {
        "title": "The Reader",
        "authors": ["Bernhard Schlink"],
        "provider": "audible",
        "provider_id": "B004UWRY6M-EN",
        "locale": "us",
        "monitored": False,
    }
    resp2 = app_client.post("/api/v1/library/books", json=unmonitored_payload)
    assert resp2.status_code == 201
    assert resp2.json()["monitored"] is False


def test_api_book_quality_profile_validation(app_client):
    payload = {
        "title": "Der Vorleser",
        "authors": ["Bernhard Schlink"],
        "provider": "audible",
        "provider_id": "B004UWRY6M",
        "locale": "de",
    }
    resp = app_client.post("/api/v1/library/books", json=payload)
    assert resp.status_code == 201
    book = resp.json()
    # New books inherit the default (empty) profile.
    assert book["quality_profile"] == ""

    # Unknown profile name -> 422.
    invalid = app_client.patch(
        f"/api/v1/library/books/{book['id']}", json={"quality_profile": "Does Not Exist"}
    )
    assert invalid.status_code == 422

    # A configured profile name -> ok and round-trips.
    valid = app_client.patch(
        f"/api/v1/library/books/{book['id']}", json={"quality_profile": "Standard"}
    )
    assert valid.status_code == 200
    assert valid.json()["quality_profile"] == "Standard"

    single = app_client.get(f"/api/v1/library/books/{book['id']}")
    assert single.json()["quality_profile"] == "Standard"

    # Empty string ("inherit default") is always valid.
    cleared = app_client.patch(
        f"/api/v1/library/books/{book['id']}", json={"quality_profile": ""}
    )
    assert cleared.status_code == 200
    assert cleared.json()["quality_profile"] == ""


def test_api_book_root_folder_id_validation(app_client, tmp_path):
    payload = {
        "title": "Der Vorleser",
        "authors": ["Bernhard Schlink"],
        "provider": "audible",
        "provider_id": "B004UWRY6M",
        "locale": "de",
    }
    resp = app_client.post("/api/v1/library/books", json=payload)
    assert resp.status_code == 201
    book = resp.json()
    # Manual creation without a root_folder_id still leaves "no preference"
    # (the Add wizard requires one at the UI level, but the API itself may
    # omit it, #50 review).
    assert book["root_folder_id"] is None

    # Unknown id -> 422.
    invalid = app_client.patch(
        f"/api/v1/library/books/{book['id']}", json={"root_folder_id": 999999}
    )
    assert invalid.status_code == 422

    folder_resp = app_client.post(
        "/api/v1/library/root-folders", json={"path": str(tmp_path / "audiobooks")}
    )
    assert folder_resp.status_code == 201
    folder_id = folder_resp.json()["id"]

    # A configured root folder id -> ok and round-trips.
    valid = app_client.patch(
        f"/api/v1/library/books/{book['id']}", json={"root_folder_id": folder_id}
    )
    assert valid.status_code == 200
    assert valid.json()["root_folder_id"] == folder_id

    single = app_client.get(f"/api/v1/library/books/{book['id']}")
    assert single.json()["root_folder_id"] == folder_id


def test_api_book_root_folder_id_can_be_cleared(app_client, tmp_path):
    """PATCH {"root_folder_id": null} clears a previously-set preference back
    to NULL ("no preference") instead of being silently ignored (#50 review:
    the data model always allowed NULL, the API just didn't expose clearing
    it)."""
    folder_resp = app_client.post(
        "/api/v1/library/root-folders", json={"path": str(tmp_path / "audiobooks")}
    )
    folder_id = folder_resp.json()["id"]

    created = app_client.post(
        "/api/v1/library/books",
        json={"title": "Der Vorleser", "root_folder_id": folder_id},
    )
    assert created.status_code == 201
    book = created.json()
    assert book["root_folder_id"] == folder_id

    cleared = app_client.patch(
        f"/api/v1/library/books/{book['id']}", json={"root_folder_id": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["root_folder_id"] is None

    single = app_client.get(f"/api/v1/library/books/{book['id']}")
    assert single.json()["root_folder_id"] is None


def test_api_book_create_with_root_folder_and_quality_profile(app_client, tmp_path):
    """The Add wizard sends root_folder_id/quality_profile as part of the
    create payload (#50 review: avoids a create-then-PATCH window where a
    failed PATCH would leave a half-configured book)."""
    folder_resp = app_client.post(
        "/api/v1/library/root-folders", json={"path": str(tmp_path / "audiobooks")}
    )
    folder_id = folder_resp.json()["id"]

    resp = app_client.post(
        "/api/v1/library/books",
        json={
            "title": "Der Vorleser",
            "authors": ["Bernhard Schlink"],
            "provider": "audible",
            "provider_id": "B004UWRY6M",
            "locale": "de",
            "root_folder_id": folder_id,
            "quality_profile": "Standard",
        },
    )
    assert resp.status_code == 201
    book = resp.json()
    assert book["root_folder_id"] == folder_id
    assert book["quality_profile"] == "Standard"

    single = app_client.get(f"/api/v1/library/books/{book['id']}")
    assert single.json()["root_folder_id"] == folder_id
    assert single.json()["quality_profile"] == "Standard"


def test_api_book_create_with_invalid_root_folder_returns_422_and_creates_nothing(app_client):
    before = app_client.get("/api/v1/library/stats").json()["book_count"]

    resp = app_client.post(
        "/api/v1/library/books",
        json={"title": "Der Vorleser", "root_folder_id": 999999},
    )
    assert resp.status_code == 422

    after = app_client.get("/api/v1/library/stats").json()["book_count"]
    assert after == before
    assert app_client.get("/api/v1/library/books").json() == []


def test_api_book_create_with_invalid_quality_profile_returns_422_and_creates_nothing(app_client):
    before = app_client.get("/api/v1/library/stats").json()["book_count"]

    resp = app_client.post(
        "/api/v1/library/books",
        json={"title": "Der Vorleser", "quality_profile": "Does Not Exist"},
    )
    assert resp.status_code == 422

    after = app_client.get("/api/v1/library/stats").json()["book_count"]
    assert after == before
    assert app_client.get("/api/v1/library/books").json() == []


def test_api_book_organize_falls_back_to_book_root_folder_preference(app_client, tmp_path):
    """Organize preview picks the book's own root_folder_id (set at add
    time, #50) over the "first configured folder" default when the request
    doesn't specify one explicitly."""
    first = app_client.post(
        "/api/v1/library/root-folders", json={"path": str(tmp_path / "first")}
    )
    preferred = app_client.post(
        "/api/v1/library/root-folders", json={"path": str(tmp_path / "preferred")}
    )
    assert first.status_code == 201 and preferred.status_code == 201
    preferred_id = preferred.json()["id"]

    book_resp = app_client.post(
        "/api/v1/library/books",
        json={
            "title": "Der Vorleser",
            "authors": ["Bernhard Schlink"],
            "provider": "audible",
            "provider_id": "B004UWRY6M",
            "locale": "de",
        },
    )
    book_id = book_resp.json()["id"]
    app_client.patch(f"/api/v1/library/books/{book_id}", json={"root_folder_id": preferred_id})

    preview = app_client.post(f"/api/v1/library/books/{book_id}/organize/preview", json={})
    assert preview.status_code == 200
    assert preview.json()["root_folder"] == str(tmp_path / "preferred")


def test_book_endpoints_include_file_stats(app_client):
    payload = {
        "title": "Der Vorleser",
        "authors": ["Bernhard Schlink"],
        "narrators": ["Hans Korte"],
        "language": "de",
        "duration_seconds": 298 * 60,
        "provider": "audible",
        "provider_id": "B004UWRY6M",
        "locale": "de",
    }
    resp = app_client.post("/api/v1/library/books", json=payload)
    assert resp.status_code == 201
    book = resp.json()

    # No editions/files yet -> zeroed-out stats.
    assert book["file_count"] == 0
    assert book["size_bytes"] == 0
    assert book["formats"] == []
    assert book["added_at"] is None

    # Attach an edition + two files directly, mirroring what the importer writes.
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO editions (book_id, format, locale) VALUES (?, 'm4b', 'de')",
            (book["id"],),
        )
        edition_id = cur.lastrowid
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) "
            "VALUES (?, ?, ?, 'm4b')",
            (edition_id, "/data/book/part1.m4b", 1000),
        )
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) "
            "VALUES (?, ?, ?, 'm4b')",
            (edition_id, "/data/book/part2.m4b", 2000),
        )

    single = app_client.get(f"/api/v1/library/books/{book['id']}")
    assert single.status_code == 200
    data = single.json()
    assert data["file_count"] == 2
    assert data["size_bytes"] == 3000
    assert data["formats"] == ["m4b"]
    assert data["added_at"] is not None

    listing = app_client.get("/api/v1/library/books").json()
    assert listing[0]["file_count"] == 2
    assert listing[0]["size_bytes"] == 3000

    files_resp = app_client.get(f"/api/v1/library/books/{book['id']}/files")
    assert files_resp.status_code == 200
    files = files_resp.json()
    assert len(files) == 2
    assert {f["format"] for f in files} == {"m4b"}
    assert all(f["edition_id"] == edition_id for f in files)

    missing = app_client.get("/api/v1/library/books/9999/files")
    assert missing.status_code == 404


def test_api_library_stats(app_client):
    empty = app_client.get("/api/v1/library/stats")
    assert empty.status_code == 200
    assert empty.json() == {
        "book_count": 0,
        "author_count": 0,
        "narrator_count": 0,
        "series_count": 0,
        "file_count": 0,
        "total_size_bytes": 0,
        "root_folder_count": 0,
    }

    app_client.post("/api/v1/library/root-folders", json={"path": "/data/audiobooks"})
    app_client.post(
        "/api/v1/library/books",
        json={
            "title": "Der Vorleser",
            "authors": ["Bernhard Schlink"],
            "narrators": ["Hans Korte"],
            "series": "Nachkriegsromane",
            "language": "de",
            "provider": "audible",
            "provider_id": "B004UWRY6M",
            "locale": "de",
        },
    )

    stats = app_client.get("/api/v1/library/stats").json()
    assert stats["book_count"] == 1
    assert stats["author_count"] == 1
    assert stats["narrator_count"] == 1
    assert stats["series_count"] == 1
    assert stats["root_folder_count"] == 1


def test_api_book_refresh_updates_fields_from_provider(app_client, monkeypatch):
    """Book detail "Refresh" toolbar action (#50): re-fetches metadata from
    the book's own linked provider id and persists the fresh fields."""
    resp = app_client.post(
        "/api/v1/library/books",
        json={
            "title": "Der Vorleser (old title)",
            "authors": ["Bernhard Schlink"],
            "provider": "stub",
            "provider_id": "STUB1",
            "locale": "de",
        },
    )
    assert resp.status_code == 201
    book = resp.json()

    detail = BookDetailInfo(
        provider_uid="STUB1",
        provider_name="stub",
        provider_external_id="STUB1",
        title="Der Vorleser",
        subtitle="Roman",
        description="Updated description",
        release_date="1995-09-01",
        language="de",
        duration_seconds=17880,
        cover_url="https://example.invalid/cover.jpg",
        publishers=["Diogenes"],
    )
    monkeypatch.setattr(routes_library, "build_provider_chain", lambda: _stub_chain(detail))

    refreshed = app_client.post(f"/api/v1/library/books/{book['id']}/refresh")
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["title"] == "Der Vorleser"
    assert body["subtitle"] == "Roman"
    assert body["description"] == "Updated description"
    assert body["release_date"] == "1995-09-01"
    assert body["cover_url"] == "https://example.invalid/cover.jpg"
    assert body["publisher"] == "Diogenes"


def test_api_book_refresh_without_linked_provider_returns_422(app_client):
    resp = app_client.post("/api/v1/library/books", json={"title": "Manually added book"})
    assert resp.status_code == 201
    book = resp.json()

    refreshed = app_client.post(f"/api/v1/library/books/{book['id']}/refresh")
    assert refreshed.status_code == 422


def test_api_book_refresh_provider_miss_returns_502(app_client, monkeypatch):
    resp = app_client.post(
        "/api/v1/library/books",
        json={
            "title": "Der Vorleser",
            "provider": "stub",
            "provider_id": "STUB1",
            "locale": "de",
        },
    )
    book = resp.json()
    monkeypatch.setattr(routes_library, "build_provider_chain", lambda: _stub_chain(None))

    refreshed = app_client.post(f"/api/v1/library/books/{book['id']}/refresh")
    assert refreshed.status_code == 502


def test_api_book_refresh_missing_book_returns_404(app_client):
    resp = app_client.post("/api/v1/library/books/9999/refresh")
    assert resp.status_code == 404

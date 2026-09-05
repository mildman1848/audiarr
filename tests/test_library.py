"""Library store tests: CRUD + provider attribution + API endpoints."""

from __future__ import annotations

from app.db import get_conn, migrate
from app.library import (
    BookCreate,
    RootFolderCreate,
    create_book,
    create_root_folder,
    find_book_by_provider_id,
    get_book,
    provider_id_exists,
    update_book,
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

    # Duplicate -> 409
    dup = app_client.post(
        "/api/v1/library/root-folders", json={"path": "/data/audiobooks"}
    )
    assert dup.status_code == 409

    # List
    listing = app_client.get("/api/v1/library/root-folders")
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    # Delete
    gone = app_client.delete(f"/api/v1/library/root-folders/{folder['id']}")
    assert gone.status_code == 204
    assert app_client.get("/api/v1/library/root-folders").json() == []


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

"""Tag CRUD (#27): API roundtrip, book/root-folder assignment, library filter."""

from __future__ import annotations


def _make_book(app_client, **overrides: object) -> dict:
    payload: dict[str, object] = dict(
        title="Der Vorleser",
        authors=["Bernhard Schlink"],
        narrators=["Hans Korte"],
        language="de",
        provider="audible",
        provider_id="B004UWRY6M",
        locale="de",
    )
    payload.update(overrides)
    resp = app_client.post("/api/v1/library/books", json=payload)
    assert resp.status_code == 201
    return resp.json()


# -- tag CRUD -----------------------------------------------------------------


def test_tag_crud_roundtrip(app_client):
    # Create
    created = app_client.post("/api/v1/tags", json={"label": "Favorites", "color": "#ff8c00"})
    assert created.status_code == 201
    tag = created.json()
    assert tag["label"] == "Favorites"
    assert tag["color"] == "#ff8c00"

    # List
    listing = app_client.get("/api/v1/tags")
    assert listing.status_code == 200
    assert listing.json() == [
        {"id": tag["id"], "label": "Favorites", "color": "#ff8c00", "book_count": 0}
    ]

    # Duplicate (case-insensitive) -> 409
    dup = app_client.post("/api/v1/tags", json={"label": "favorites"})
    assert dup.status_code == 409

    # Patch: rename + recolor
    patched = app_client.patch(
        f"/api/v1/tags/{tag['id']}", json={"label": "Favourites", "color": "#00ff00"}
    )
    assert patched.status_code == 200
    assert patched.json() == {"id": tag["id"], "label": "Favourites", "color": "#00ff00"}

    # Patch to unknown id -> 404
    missing = app_client.patch("/api/v1/tags/9999", json={"label": "x"})
    assert missing.status_code == 404

    # Delete
    gone = app_client.delete(f"/api/v1/tags/{tag['id']}")
    assert gone.status_code == 204
    assert app_client.get("/api/v1/tags").json() == []

    # Delete unknown id -> 404
    missing_delete = app_client.delete(f"/api/v1/tags/{tag['id']}")
    assert missing_delete.status_code == 404


def test_tag_create_validates_label(app_client):
    empty = app_client.post("/api/v1/tags", json={"label": "   "})
    assert empty.status_code == 422

    too_long = app_client.post("/api/v1/tags", json={"label": "x" * 61})
    assert too_long.status_code == 422

    trimmed = app_client.post("/api/v1/tags", json={"label": "  Sci-Fi  "})
    assert trimmed.status_code == 201
    assert trimmed.json()["label"] == "Sci-Fi"


def test_tag_rename_collision(app_client):
    first = app_client.post("/api/v1/tags", json={"label": "Fiction"}).json()
    second = app_client.post("/api/v1/tags", json={"label": "Non-Fiction"}).json()

    collide = app_client.patch(f"/api/v1/tags/{second['id']}", json={"label": "fiction"})
    assert collide.status_code == 409

    # Renaming a tag to its own (differently-cased) label is not a collision.
    same = app_client.patch(f"/api/v1/tags/{first['id']}", json={"label": "FICTION"})
    assert same.status_code == 200
    assert same.json()["label"] == "FICTION"


# -- book tag sync --------------------------------------------------------------


def test_book_tag_sync_via_patch(app_client):
    book = _make_book(app_client)

    # Absent "tags" key -> no change, and GET reflects no tags yet.
    untouched = app_client.patch(f"/api/v1/library/books/{book['id']}", json={"title": "Renamed"})
    assert untouched.status_code == 200
    assert untouched.json()["tags"] == []

    # Set tags; unknown labels are created on the fly.
    set_resp = app_client.patch(
        f"/api/v1/library/books/{book['id']}", json={"tags": ["Fiction", "Favorites"]}
    )
    assert set_resp.status_code == 200
    labels = sorted(t["label"] for t in set_resp.json()["tags"])
    assert labels == ["Favorites", "Fiction"]

    get_resp = app_client.get(f"/api/v1/library/books/{book['id']}")
    assert sorted(t["label"] for t in get_resp.json()["tags"]) == ["Favorites", "Fiction"]

    all_tags = {t["label"]: t for t in app_client.get("/api/v1/tags").json()}
    assert all_tags["Fiction"]["book_count"] == 1
    assert all_tags["Favorites"]["book_count"] == 1

    # Replace the tag set: drop Favorites, add Series.
    replace_resp = app_client.patch(
        f"/api/v1/library/books/{book['id']}", json={"tags": ["Fiction", "Series"]}
    )
    assert replace_resp.status_code == 200
    labels = sorted(t["label"] for t in replace_resp.json()["tags"])
    assert labels == ["Fiction", "Series"]

    all_tags = {t["label"]: t for t in app_client.get("/api/v1/tags").json()}
    assert all_tags["Favorites"]["book_count"] == 0  # mapping removed, tag itself still exists

    # Remove all tags via an empty list -- this alone must not 422.
    clear_resp = app_client.patch(f"/api/v1/library/books/{book['id']}", json={"tags": []})
    assert clear_resp.status_code == 200
    assert clear_resp.json()["tags"] == []

    # Neither tags nor any other field -> 422, as before.
    no_fields = app_client.patch(f"/api/v1/library/books/{book['id']}", json={})
    assert no_fields.status_code == 422

    # Unknown book -> 404
    missing = app_client.patch("/api/v1/library/books/9999", json={"tags": ["x"]})
    assert missing.status_code == 404


# -- root folder tag sync --------------------------------------------------------


def test_root_folder_tag_put(app_client):
    folder = app_client.post(
        "/api/v1/library/root-folders", json={"path": "/data/audiobooks"}
    ).json()

    put_resp = app_client.put(
        f"/api/v1/library/root-folders/{folder['id']}/tags",
        json={"tags": ["Archive", "German"]},
    )
    assert put_resp.status_code == 200
    labels = sorted(t["label"] for t in put_resp.json())
    assert labels == ["Archive", "German"]

    listing = app_client.get("/api/v1/library/root-folders").json()
    assert sorted(t["label"] for t in listing[0]["tags"]) == ["Archive", "German"]

    # Replace with a different set.
    replace_resp = app_client.put(
        f"/api/v1/library/root-folders/{folder['id']}/tags", json={"tags": ["German"]}
    )
    assert replace_resp.status_code == 200
    assert [t["label"] for t in replace_resp.json()] == ["German"]

    # Unknown folder -> 404
    missing = app_client.put(
        "/api/v1/library/root-folders/9999/tags", json={"tags": ["x"]}
    )
    assert missing.status_code == 404


# -- library tag filter -----------------------------------------------------------


def test_library_books_tag_filter(app_client):
    tagged = _make_book(app_client, title="Tagged Book", provider_id="AAA")
    untagged = _make_book(app_client, title="Untagged Book", provider_id="BBB")

    app_client.patch(f"/api/v1/library/books/{tagged['id']}", json={"tags": ["Fiction"]})

    filtered = app_client.get("/api/v1/library/books", params={"tag": "fiction"})
    assert filtered.status_code == 200
    titles = [b["title"] for b in filtered.json()]
    assert titles == ["Tagged Book"]

    no_match = app_client.get("/api/v1/library/books", params={"tag": "Nonexistent"})
    assert no_match.status_code == 200
    assert no_match.json() == []

    unfiltered = app_client.get("/api/v1/library/books")
    assert {b["title"] for b in unfiltered.json()} == {"Tagged Book", "Untagged Book"}
    assert untagged  # sanity: created successfully, just never tagged

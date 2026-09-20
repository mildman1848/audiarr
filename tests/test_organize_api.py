"""API tests for the organize preview/apply endpoints (issue #29)."""

from __future__ import annotations

from app.db import get_conn


def _create_book(app_client, **overrides):
    payload = dict(
        title="Der Vorleser",
        authors=["Bernhard Schlink"],
        narrators=["Hans Korte"],
        release_date="2008-05-01",
        provider="audible",
        provider_id="B004UWRY6M",
        locale="de",
    )
    payload.update(overrides)
    resp = app_client.post("/api/v1/library/books", json=payload)
    assert resp.status_code == 201
    return resp.json()


def _attach_file(book_id, tmp_path, filename="disc1.m4b"):
    source_dir = tmp_path / "src"
    source_dir.mkdir(exist_ok=True)
    source_path = source_dir / filename
    source_path.write_bytes(b"data")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO editions (book_id, format, locale) VALUES (?, 'm4b', 'de')", (book_id,)
        )
        edition_id = cur.lastrowid
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) VALUES (?, ?, 4, 'm4b')",
            (edition_id, str(source_path)),
        )
    return source_path


def _enable_rename_files(app_client):
    settings = app_client.get("/api/v1/settings").json()
    settings["media_management"]["rename_files"] = True
    resp = app_client.put("/api/v1/settings", json=settings)
    assert resp.status_code == 200


def test_preview_404_for_missing_book(app_client):
    resp = app_client.post("/api/v1/library/books/9999/organize/preview", json={})
    assert resp.status_code == 404


def test_apply_404_for_missing_book(app_client):
    _enable_rename_files(app_client)
    resp = app_client.post("/api/v1/library/books/9999/organize/apply", json={})
    assert resp.status_code == 404


def test_preview_422_when_no_root_folder_configured(app_client):
    book = _create_book(app_client)
    resp = app_client.post(f"/api/v1/library/books/{book['id']}/organize/preview", json={})
    assert resp.status_code == 422


def test_preview_422_for_unknown_root_folder_id(app_client, tmp_path):
    book = _create_book(app_client)
    app_client.post("/api/v1/library/root-folders", json={"path": str(tmp_path / "library")})
    resp = app_client.post(
        f"/api/v1/library/books/{book['id']}/organize/preview",
        json={"root_folder_id": 9999},
    )
    assert resp.status_code == 422


def test_apply_400_when_rename_files_disabled(app_client, tmp_path):
    book = _create_book(app_client)
    _attach_file(book["id"], tmp_path)
    app_client.post("/api/v1/library/root-folders", json={"path": str(tmp_path / "library")})
    # rename_files defaults to False -- apply must refuse outright.
    resp = app_client.post(f"/api/v1/library/books/{book['id']}/organize/apply", json={})
    assert resp.status_code == 400


def test_preview_and_apply_happy_path(app_client, tmp_path):
    book = _create_book(app_client)
    source_path = _attach_file(book["id"], tmp_path)
    root_resp = app_client.post(
        "/api/v1/library/root-folders", json={"path": str(tmp_path / "library")}
    )
    root_folder = root_resp.json()

    preview_resp = app_client.post(
        f"/api/v1/library/books/{book['id']}/organize/preview",
        json={"root_folder_id": root_folder["id"], "pattern": "{author}/{title} ({year})"},
    )
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["safe_to_apply"] is True
    assert len(preview["items"]) == 1
    item = preview["items"][0]
    assert item["status"] == "ready"
    assert item["source_path"] == str(source_path)
    expected_target = str(
        (tmp_path / "library" / "Bernhard Schlink" / "Der Vorleser (2008)" / "disc1.m4b").resolve()
    )
    assert item["target_path"] == expected_target

    _enable_rename_files(app_client)
    apply_resp = app_client.post(
        f"/api/v1/library/books/{book['id']}/organize/apply",
        json={"root_folder_id": root_folder["id"], "pattern": "{author}/{title} ({year})"},
    )
    assert apply_resp.status_code == 200
    applied = apply_resp.json()
    assert applied["moved_count"] == 1
    assert applied["items"][0]["status"] == "moved"
    assert not source_path.exists()
    assert (tmp_path / "library" / "Bernhard Schlink" / "Der Vorleser (2008)" / "disc1.m4b").exists()

    files_resp = app_client.get(f"/api/v1/library/books/{book['id']}/files")
    assert files_resp.json()[0]["path"] == expected_target


def test_apply_409_when_preview_unsafe(app_client, tmp_path):
    book = _create_book(app_client)
    source_path = _attach_file(book["id"], tmp_path)
    source_path.unlink()  # source now missing -> preview unsafe
    app_client.post("/api/v1/library/root-folders", json={"path": str(tmp_path / "library")})
    _enable_rename_files(app_client)

    resp = app_client.post(f"/api/v1/library/books/{book['id']}/organize/apply", json={})
    assert resp.status_code == 409

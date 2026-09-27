"""Tests for the OPDS export feed (issue #65): feed structure, pagination,
auth enforcement, and the download route (real temp file + missing/
traversal rejection). Offline -- no network, no external services.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from app.db import get_conn

ATOM_NS = "{http://www.w3.org/2005/Atom}"
OPDS_ACQUISITION_REL = "http://opds-spec.org/acquisition"


def _create_root_folder(app_client, path) -> int:
    resp = app_client.post("/api/v1/library/root-folders", json={"path": str(path)})
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_book(app_client, title="Der Vorleser", authors=None, narrators=None) -> dict:
    payload = {
        "title": title,
        "authors": authors if authors is not None else ["Bernhard Schlink"],
        "narrators": narrators if narrators is not None else ["Hans Korte"],
        "language": "de",
        "provider": "audible",
        "provider_id": f"B{title!r}",
        "locale": "de",
    }
    resp = app_client.post("/api/v1/library/books", json=payload)
    assert resp.status_code == 201
    return resp.json()


def _attach_file(book_id: int, folder, filename: str, content: bytes = b"fake audio bytes") -> str:
    """Write a real temp file under ``folder`` and register it as a library
    file for ``book_id`` via a single-edition insert. Returns the path."""
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / filename
    file_path.write_bytes(content)
    fmt = filename.rsplit(".", 1)[-1]
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO editions (book_id, format, locale) VALUES (?, ?, 'de')",
            (book_id, fmt),
        )
        edition_id = cur.lastrowid
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) "
            "VALUES (?, ?, ?, ?)",
            (edition_id, str(file_path), len(content), fmt),
        )
    return str(file_path)


def _parse(resp) -> ET.Element:
    assert resp.headers["content-type"].startswith("application/atom+xml")
    return ET.fromstring(resp.content)


def test_opds_root_is_valid_navigation_feed(app_client):
    resp = app_client.get("/opds")
    assert resp.status_code == 200
    root = _parse(resp)
    assert root.tag == f"{ATOM_NS}feed"
    assert root.find(f"{ATOM_NS}title").text == "Audiarr"

    hrefs_by_rel = {}
    for link in root.findall(f"{ATOM_NS}link"):
        hrefs_by_rel.setdefault(link.get("rel"), []).append(link.get("href"))
    assert hrefs_by_rel["self"][0].endswith("/opds")
    assert hrefs_by_rel["search"][0].endswith("/opds/search.xml")

    subsection_hrefs = {
        entry.find(f"{ATOM_NS}title").text: entry.find(f"{ATOM_NS}link").get("href")
        for entry in root.findall(f"{ATOM_NS}entry")
    }
    assert subsection_hrefs["New"].endswith("/opds/new")
    assert subsection_hrefs["Authors"].endswith("/opds/authors")
    assert subsection_hrefs["Narrators"].endswith("/opds/narrators")


def test_opds_search_description_document(app_client):
    resp = app_client.get("/opds/search.xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/opensearchdescription+xml")
    root = ET.fromstring(resp.content)
    url_el = root.find("{http://a9.com/-/spec/opensearch/1.1/}Url")
    assert url_el is not None
    assert "searchTerms" in url_el.get("template")


def test_opds_new_lists_book_with_acquisition_link(app_client, tmp_path):
    _create_root_folder(app_client, tmp_path / "audiobooks")
    book = _create_book(app_client)
    _attach_file(book["id"], tmp_path / "audiobooks" / "book", "part1.m4b")

    resp = app_client.get("/opds/new")
    root = _parse(resp)
    entries = root.findall(f"{ATOM_NS}entry")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.find(f"{ATOM_NS}title").text == "Der Vorleser"
    assert entry.find(f"{ATOM_NS}id").text == f"urn:audiarr:book:{book['id']}"
    assert entry.find(f"{ATOM_NS}updated").text is not None
    author_name = entry.find(f"{ATOM_NS}author").find(f"{ATOM_NS}name").text
    assert author_name == "Bernhard Schlink"

    acquisition_links = [
        link for link in entry.findall(f"{ATOM_NS}link") if link.get("rel") == OPDS_ACQUISITION_REL
    ]
    assert len(acquisition_links) == 1
    link = acquisition_links[0]
    assert link.get("href").endswith(f"/opds/books/{book['id']}/download")
    assert link.get("type") == "audio/x-m4b"


def test_opds_new_omits_acquisition_link_when_file_missing(app_client, tmp_path):
    _create_root_folder(app_client, tmp_path / "audiobooks")
    book = _create_book(app_client)
    # Register a library_files row without ever writing the file to disk.
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO editions (book_id, format, locale) VALUES (?, 'm4b', 'de')",
            (book["id"],),
        )
        edition_id = cur.lastrowid
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) "
            "VALUES (?, ?, 0, 'm4b')",
            (edition_id, str(tmp_path / "audiobooks" / "ghost.m4b")),
        )

    resp = app_client.get("/opds/new")
    root = _parse(resp)
    entry = root.findall(f"{ATOM_NS}entry")[0]
    acquisition_links = [
        link for link in entry.findall(f"{ATOM_NS}link") if link.get("rel") == OPDS_ACQUISITION_REL
    ]
    assert acquisition_links == []


def test_opds_primary_file_prefers_m4b_over_mp3(app_client, tmp_path):
    _create_root_folder(app_client, tmp_path / "audiobooks")
    book = _create_book(app_client)
    folder = tmp_path / "audiobooks" / "book"
    _attach_file(book["id"], folder, "part1.mp3")
    _attach_file(book["id"], folder, "part1.m4b")

    resp = app_client.get(f"/opds/books/{book['id']}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/x-m4b"


def test_opds_new_pagination_limit_and_offset(app_client, tmp_path):
    _create_root_folder(app_client, tmp_path / "audiobooks")
    for i in range(3):
        _create_book(app_client, title=f"Book {i}")

    first_page = _parse(app_client.get("/opds/new?limit=2"))
    entries = first_page.findall(f"{ATOM_NS}entry")
    assert len(entries) == 2
    next_links = [
        link.get("href")
        for link in first_page.findall(f"{ATOM_NS}link")
        if link.get("rel") == "next"
    ]
    assert len(next_links) == 1
    assert "offset=2" in next_links[0]

    second_page = _parse(app_client.get("/opds/new?limit=2&offset=2"))
    entries = second_page.findall(f"{ATOM_NS}entry")
    assert len(entries) == 1
    next_links = [
        link.get("href")
        for link in second_page.findall(f"{ATOM_NS}link")
        if link.get("rel") == "next"
    ]
    assert next_links == []


def test_opds_authors_and_narrators_navigation_and_detail(app_client, tmp_path):
    _create_root_folder(app_client, tmp_path / "audiobooks")
    book = _create_book(app_client)

    authors_feed = _parse(app_client.get("/opds/authors"))
    entries = authors_feed.findall(f"{ATOM_NS}entry")
    assert len(entries) == 1
    author_href = entries[0].find(f"{ATOM_NS}link").get("href")
    assert "/opds/authors/" in author_href

    author_id = author_href.rstrip("/").rsplit("/", 1)[-1]
    author_books = _parse(app_client.get(f"/opds/authors/{author_id}"))
    book_entries = author_books.findall(f"{ATOM_NS}entry")
    assert len(book_entries) == 1
    assert book_entries[0].find(f"{ATOM_NS}id").text == f"urn:audiarr:book:{book['id']}"

    narrators_feed = _parse(app_client.get("/opds/narrators"))
    entries = narrators_feed.findall(f"{ATOM_NS}entry")
    assert len(entries) == 1
    narrator_href = entries[0].find(f"{ATOM_NS}link").get("href")
    narrator_id = narrator_href.rstrip("/").rsplit("/", 1)[-1]
    narrator_books = _parse(app_client.get(f"/opds/narrators/{narrator_id}"))
    assert len(narrator_books.findall(f"{ATOM_NS}entry")) == 1

    assert app_client.get("/opds/authors/999999").status_code == 404
    assert app_client.get("/opds/narrators/999999").status_code == 404


def test_opds_search_matches_title_and_author(app_client, tmp_path):
    _create_root_folder(app_client, tmp_path / "audiobooks")
    _create_book(app_client, title="Der Vorleser", authors=["Bernhard Schlink"])
    _create_book(app_client, title="Unrelated Title", authors=["Someone Else"])

    by_title = _parse(app_client.get("/opds/search?query=Vorleser"))
    assert len(by_title.findall(f"{ATOM_NS}entry")) == 1

    by_author = _parse(app_client.get("/opds/search?query=Schlink"))
    assert len(by_author.findall(f"{ATOM_NS}entry")) == 1

    no_match = _parse(app_client.get("/opds/search?query=doesnotexist"))
    assert no_match.findall(f"{ATOM_NS}entry") == []


def test_opds_download_real_file(app_client, tmp_path):
    _create_root_folder(app_client, tmp_path / "audiobooks")
    book = _create_book(app_client)
    content = b"\x00\x01fake-m4b-bytes"
    _attach_file(book["id"], tmp_path / "audiobooks" / "book", "part1.m4b", content=content)

    resp = app_client.get(f"/opds/books/{book['id']}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/x-m4b"
    assert resp.content == content


def test_opds_download_missing_file_returns_404(app_client, tmp_path):
    _create_root_folder(app_client, tmp_path / "audiobooks")
    book = _create_book(app_client)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO editions (book_id, format, locale) VALUES (?, 'm4b', 'de')",
            (book["id"],),
        )
        edition_id = cur.lastrowid
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) "
            "VALUES (?, ?, 0, 'm4b')",
            (edition_id, str(tmp_path / "audiobooks" / "missing.m4b")),
        )

    resp = app_client.get(f"/opds/books/{book['id']}/download")
    assert resp.status_code == 404


def test_opds_download_nonexistent_book_returns_404(app_client):
    resp = app_client.get("/opds/books/999999/download")
    assert resp.status_code == 404


def test_opds_download_rejects_file_outside_root_folder(app_client, tmp_path):
    """Defense in depth: a library_files.path outside every configured root
    folder is treated as unservable, even though it never comes from user
    input directly."""
    _create_root_folder(app_client, tmp_path / "audiobooks")
    book = _create_book(app_client)
    outside_dir = tmp_path / "outside"
    _attach_file(book["id"], outside_dir, "part1.m4b")

    resp = app_client.get(f"/opds/books/{book['id']}/download")
    assert resp.status_code == 404


def test_opds_requires_auth_but_accepts_api_key_header(app_client):
    settings = app_client.get("/api/v1/settings").json()
    settings["auth"]["method"] = "forms"
    settings["auth"]["username"] = "admin"
    settings["auth"]["password"] = "s3cret-pw"
    put_resp = app_client.put("/api/v1/settings", json=settings)
    assert put_resp.status_code == 200
    api_key = put_resp.json()["auth"]["api_key"]
    assert api_key

    # /opds isn't under /api/, so unauthenticated requests get the same
    # redirect-to-login treatment as any other page route (app/auth.py);
    # OPDS is deliberately not added to EXEMPT_PATHS.
    unauthenticated = app_client.get("/opds", follow_redirects=False)
    assert unauthenticated.status_code != 200

    authenticated = app_client.get("/opds", headers={"X-Api-Key": api_key})
    assert authenticated.status_code == 200

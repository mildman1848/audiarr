"""Tests for app.library.organizer: pattern rendering, preview, apply, rollback.

See app/library/organizer.py for the design notes (issue #29). Preview/apply
tests use a real temp directory tree plus a real sqlite DB (via
set_db_path_override) so file moves and DB updates are exercised end to end,
not mocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import get_conn, migrate, set_db_path_override
from app.library import BookCreate, create_book
from app.library.organizer import (
    apply_preview,
    build_context,
    build_preview,
    render_pattern,
    render_target_relative_path,
    sanitize_component,
)

# -- sanitize_component -------------------------------------------------------


def test_sanitize_component_strips_unsafe_characters():
    assert sanitize_component('Foo/Bar\\Baz:Qux*?"<>|') == "FooBarBazQux"


def test_sanitize_component_strips_control_chars():
    assert sanitize_component("Foo\x00Bar\x1f") == "FooBar"


def test_sanitize_component_collapses_whitespace():
    assert sanitize_component("  Der   Vorleser  ") == "Der Vorleser"


def test_sanitize_component_rejects_dot_segments():
    assert sanitize_component(".") == ""
    assert sanitize_component("..") == ""
    assert sanitize_component("") == ""
    assert sanitize_component("   ") == ""


def test_sanitize_component_keeps_unicode_letters():
    assert sanitize_component("Bernhard Schlink Ängström") == "Bernhard Schlink Ängström"


# -- build_context -------------------------------------------------------------


def test_build_context_defaults_for_missing_people():
    context = build_context({"title": "T", "authors": "", "narrators": ""}, book_id=7, quality="m4b")
    assert context["author"] == "Unknown Author"
    assert context["narrator"] == "Unknown Narrator"
    assert context["authors"] == ""
    assert context["narrators"] == ""
    assert context["book_id"] == "7"
    assert context["quality"] == "m4b"


def test_build_context_first_of_multiple_and_full_lists():
    book = {"authors": "Alice, Bob", "narrators": "Carol, Dave", "title": "T"}
    context = build_context(book, book_id=1, quality="mp3")
    assert context["author"] == "Alice"
    assert context["authors"] == "Alice, Bob"
    assert context["narrator"] == "Carol"
    assert context["narrators"] == "Carol, Dave"


def test_build_context_year_from_release_date():
    context = build_context({"release_date": "2008-05-01", "authors": "", "narrators": ""}, 1, "m4b")
    assert context["year"] == "2008"


def test_build_context_year_empty_without_release_date():
    context = build_context({"authors": "", "narrators": ""}, 1, "m4b")
    assert context["year"] == ""


def test_build_context_series_from_series_name_key():
    context = build_context(
        {"series_name": "Nachkriegsromane", "series_position": 2.0, "authors": "", "narrators": ""},
        1,
        "m4b",
    )
    assert context["series"] == "Nachkriegsromane"
    assert context["series_position"] == "2.0"


def test_build_context_series_empty_when_absent():
    context = build_context({"authors": "", "narrators": ""}, 1, "m4b")
    assert context["series"] == ""
    assert context["series_position"] == ""


# -- render_pattern -------------------------------------------------------------


def test_render_pattern_default_shape_drops_empty_series_component():
    context = build_context(
        {
            "title": "Der Vorleser",
            "authors": "Bernhard Schlink",
            "narrators": "",
            "release_date": "2008-01-01",
        },
        1,
        "m4b",
    )
    components = render_pattern("{author}/{series}/{title} ({year})", context)
    assert components == ["Bernhard Schlink", "Der Vorleser (2008)"]


def test_render_pattern_unknown_token_becomes_empty():
    assert render_pattern("{nope}/{title}", {"title": "T"}) == ["T"]


def test_render_pattern_token_value_cannot_inject_extra_components():
    # A token value that is itself a full path must not turn into multiple
    # rendered components -- slashes inside a token's value are stripped as
    # part of sanitizing the whole rendered segment.
    context = {"author": "../../etc/passwd", "title": "T"}
    components = render_pattern("{author}/{title}", context)
    assert components == ["....etcpasswd", "T"]
    assert ".." not in components


def test_render_pattern_token_value_exact_dotdot_is_dropped():
    context = {"author": "..", "title": "T"}
    assert render_pattern("{author}/{title}", context) == ["T"]


# -- render_target_relative_path ------------------------------------------------


def test_render_target_relative_path_preserves_stem_and_suffix():
    context = build_context({"title": "T", "authors": "", "narrators": ""}, 1, "m4b")
    relative = render_target_relative_path("{title}", context, "/src/disc1.m4b")
    assert relative == Path("T", "disc1.m4b")


def test_render_target_relative_path_multi_file_no_collision():
    context = build_context({"title": "T", "authors": "", "narrators": ""}, 1, "m4b")
    r1 = render_target_relative_path("{title}", context, "/src/disc1.m4b")
    r2 = render_target_relative_path("{title}", context, "/src/disc2.m4b")
    assert r1 != r2


# -- preview/apply against a real temp tree + DB --------------------------------


@pytest.fixture()
def lib(tmp_path):
    db_path = tmp_path / "lib.db"
    set_db_path_override(db_path)
    migrate(db_path)
    yield tmp_path
    set_db_path_override(None)


def _make_book_with_file(tmp_path, filename="disc1.m4b", **overrides):
    payload: dict[str, object] = dict(
        title="Der Vorleser",
        authors=["Bernhard Schlink"],
        narrators=["Hans Korte"],
        series="Nachkriegsromane",
        series_position=1.0,
        release_date="2008-05-01",
    )
    payload.update(overrides)
    with get_conn() as conn:
        book_id = create_book(conn, BookCreate(**payload))  # type: ignore[arg-type]
        cur = conn.execute(
            "INSERT INTO editions (book_id, format, locale) VALUES (?, 'm4b', 'de')", (book_id,)
        )
        edition_id = cur.lastrowid
        source_dir = tmp_path / "src"
        source_dir.mkdir(exist_ok=True)
        source_path = source_dir / filename
        source_path.write_bytes(b"data")
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) VALUES (?, ?, 4, 'm4b')",
            (edition_id, str(source_path)),
        )
        file_id = conn.execute(
            "SELECT id FROM library_files WHERE path = ?", (str(source_path),)
        ).fetchone()[0]
    return book_id, file_id, source_path


def test_preview_ready_when_target_is_free(lib):
    book_id, file_id, source_path = _make_book_with_file(lib)
    root = lib / "library"
    root.mkdir()
    with get_conn() as conn:
        preview = build_preview(conn, book_id, str(root), "{author}/{title} ({year})")
    assert preview.safe_to_apply
    assert len(preview.items) == 1
    item = preview.items[0]
    assert item.status == "ready"
    assert item.file_id == file_id
    expected = (root / "Bernhard Schlink" / "Der Vorleser (2008)" / "disc1.m4b").resolve()
    assert Path(item.target_path) == expected


def test_preview_unchanged_when_source_already_at_target(lib):
    root = lib / "library"
    target_dir = root / "Bernhard Schlink" / "Der Vorleser (2008)"
    target_dir.mkdir(parents=True)
    source_path = target_dir / "disc1.m4b"
    source_path.write_bytes(b"data")

    with get_conn() as conn:
        book_id = create_book(
            conn,
            BookCreate(title="Der Vorleser", authors=["Bernhard Schlink"], release_date="2008-05-01"),
        )
        cur = conn.execute("INSERT INTO editions (book_id, format) VALUES (?, 'm4b')", (book_id,))
        edition_id = cur.lastrowid
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) VALUES (?, ?, 4, 'm4b')",
            (edition_id, str(source_path)),
        )
        preview = build_preview(conn, book_id, str(root), "{author}/{title} ({year})")

    assert preview.safe_to_apply
    assert preview.items[0].status == "unchanged"


def test_preview_missing_source_is_unsafe(lib):
    book_id, file_id, source_path = _make_book_with_file(lib)
    source_path.unlink()
    root = lib / "library"
    root.mkdir()
    with get_conn() as conn:
        preview = build_preview(conn, book_id, str(root), "{author}/{title} ({year})")
    assert not preview.safe_to_apply
    assert preview.items[0].status == "missing"


def test_preview_conflict_when_target_already_exists(lib):
    book_id, file_id, source_path = _make_book_with_file(lib)
    root = lib / "library"
    target_dir = root / "Bernhard Schlink" / "Der Vorleser (2008)"
    target_dir.mkdir(parents=True)
    (target_dir / "disc1.m4b").write_bytes(b"already here")

    with get_conn() as conn:
        preview = build_preview(conn, book_id, str(root), "{author}/{title} ({year})")

    assert not preview.safe_to_apply
    assert preview.items[0].status == "conflict"


def test_preview_conflict_when_two_files_render_to_same_target(lib):
    with get_conn() as conn:
        book_id = create_book(conn, BookCreate(title="T", authors=["A"]))
        cur = conn.execute("INSERT INTO editions (book_id, format) VALUES (?, 'm4b')", (book_id,))
        edition_id = cur.lastrowid
        dir1 = lib / "src1"
        dir2 = lib / "src2"
        dir1.mkdir()
        dir2.mkdir()
        (dir1 / "disc1.m4b").write_bytes(b"x")
        (dir2 / "disc1.m4b").write_bytes(b"y")
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) VALUES (?, ?, 1, 'm4b')",
            (edition_id, str(dir1 / "disc1.m4b")),
        )
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) VALUES (?, ?, 1, 'm4b')",
            (edition_id, str(dir2 / "disc1.m4b")),
        )
        root = lib / "library"
        preview = build_preview(conn, book_id, str(root), "{title}")

    assert not preview.safe_to_apply
    assert len(preview.items) == 2
    assert {item.status for item in preview.items} == {"conflict"}


def test_preview_outside_root_when_root_resolves_elsewhere(lib):
    # A root folder path that doesn't actually contain the rendered target
    # (e.g. it points at a sibling directory) must never be reported ready.
    book_id, file_id, source_path = _make_book_with_file(lib)
    root = lib / "library"
    root.mkdir()
    with get_conn() as conn:
        preview = build_preview(conn, book_id, str(root), "{author}/{title} ({year})")
    assert preview.items[0].status == "ready"
    # Sanity: the rendered target really is contained under root.
    assert str(Path(preview.items[0].target_path)).startswith(str(root.resolve()))


# -- apply ----------------------------------------------------------------------


def test_apply_moves_file_and_updates_db(lib):
    book_id, file_id, source_path = _make_book_with_file(lib)
    root = lib / "library"
    with get_conn() as conn:
        preview = build_preview(conn, book_id, str(root), "{author}/{title} ({year})")
        assert preview.safe_to_apply
        result = apply_preview(conn, preview)
        assert result.success
        assert len(result.moved) == 1

    target = (root / "Bernhard Schlink" / "Der Vorleser (2008)" / "disc1.m4b").resolve()
    assert target.exists()
    assert not source_path.exists()

    with get_conn() as conn:
        row = conn.execute("SELECT path FROM library_files WHERE id = ?", (file_id,)).fetchone()
    assert row["path"] == str(target)


def test_apply_refuses_when_preview_not_safe_and_leaves_db_unchanged(lib):
    book_id, file_id, source_path = _make_book_with_file(lib)
    source_path.unlink()
    root = lib / "library"
    with get_conn() as conn:
        preview = build_preview(conn, book_id, str(root), "{author}/{title} ({year})")
        assert not preview.safe_to_apply
        result = apply_preview(conn, preview)

    assert not result.success
    assert not result.moved
    with get_conn() as conn:
        row = conn.execute("SELECT path FROM library_files WHERE id = ?", (file_id,)).fetchone()
    assert row["path"] == str(source_path)


def test_apply_rolls_back_first_move_when_second_fails(lib, monkeypatch):
    with get_conn() as conn:
        book_id = create_book(conn, BookCreate(title="Multi", authors=["Author"]))
        cur = conn.execute("INSERT INTO editions (book_id, format) VALUES (?, 'm4b')", (book_id,))
        edition_id = cur.lastrowid
        source_dir = lib / "src"
        source_dir.mkdir()
        paths = []
        for name in ("disc1.m4b", "disc2.m4b"):
            p = source_dir / name
            p.write_bytes(b"data")
            paths.append(p)
            conn.execute(
                "INSERT INTO library_files (edition_id, path, size_bytes, format) VALUES (?, ?, 1, 'm4b')",
                (edition_id, str(p)),
            )
        root = lib / "library"
        preview = build_preview(conn, book_id, str(root), "{author}")

    assert preview.safe_to_apply
    assert len(preview.items) == 2

    import shutil

    from app.library import organizer as organizer_module

    real_move = shutil.move
    calls = {"n": 0}

    def flaky_move(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated disk failure")
        return real_move(src, dst)

    monkeypatch.setattr(organizer_module.shutil, "move", flaky_move)

    with get_conn() as conn:
        result = apply_preview(conn, preview)

    assert not result.success
    assert len(result.rollback) == 1
    assert result.rollback[0]["restored"] is True

    # Both files are back at their original paths -- the first one was
    # actually moved and then rolled back; the second was never moved.
    for p in paths:
        assert p.exists()

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT path FROM library_files WHERE edition_id = ?", (edition_id,)
        ).fetchall()
    assert {r["path"] for r in rows} == {str(p) for p in paths}

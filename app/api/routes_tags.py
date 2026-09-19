"""Tag CRUD API (#27): plain labels with an optional color.

No Radarr-shaped per-tag notification routing or delay profiles here --
tags are just a shared vocabulary for organizing and filtering books and
root folders. See routes_library.py for how books/root folders assign
tags (create-on-the-fly by label, synced via PATCH/PUT).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_conn, migrate

log = logging.getLogger("audiarr.api.tags")

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])


# -- models -------------------------------------------------------------------


class TagIn(BaseModel):
    label: str
    color: str = ""


class TagPatch(BaseModel):
    label: str | None = None
    color: str | None = None


class TagOut(BaseModel):
    id: int
    label: str
    color: str


class TagListOut(TagOut):
    book_count: int


def _ensure_schema() -> None:
    """Ensure the tags tables exist for direct/TestClient API calls.

    Mirrors the defensive migrate() call in routes_library.py.
    """
    migrate()


def _clean_label(label: str) -> str:
    label = label.strip()
    if not label:
        raise HTTPException(422, "label must not be empty")
    if len(label) > 60:
        raise HTTPException(422, "label must be at most 60 characters")
    return label


@router.get("", response_model=list[TagListOut])
async def list_tags() -> list[TagListOut]:
    _ensure_schema()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.id, t.label, t.color, COUNT(bt.book_id) AS book_count
                 FROM tags t
                 LEFT JOIN book_tags bt ON bt.tag_id = t.id
                GROUP BY t.id
                ORDER BY t.label COLLATE NOCASE"""
        ).fetchall()
    return [TagListOut(**dict(r)) for r in rows]


@router.post("", response_model=TagOut, status_code=201)
async def create_tag(data: TagIn) -> TagOut:
    _ensure_schema()
    label = _clean_label(data.label)
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM tags WHERE label = ? COLLATE NOCASE", (label,)
        ).fetchone()
        if existing is not None:
            raise HTTPException(409, f"Tag {label!r} already exists")
        cur = conn.execute(
            "INSERT INTO tags (label, color) VALUES (?, ?)", (label, data.color)
        )
        tag_id = int(cur.lastrowid or 0)
        row = conn.execute(
            "SELECT id, label, color FROM tags WHERE id = ?", (tag_id,)
        ).fetchone()
    assert row is not None
    return TagOut(**dict(row))


@router.patch("/{tag_id}", response_model=TagOut)
async def patch_tag(tag_id: int, patch: TagPatch) -> TagOut:
    _ensure_schema()
    updates = patch.model_dump(exclude_unset=True)
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if existing is None:
            raise HTTPException(404, "Tag not found")

        fields: dict[str, str] = {}
        if updates.get("label") is not None:
            label = _clean_label(updates["label"])
            dup = conn.execute(
                "SELECT id FROM tags WHERE label = ? COLLATE NOCASE AND id != ?",
                (label, tag_id),
            ).fetchone()
            if dup is not None:
                raise HTTPException(409, f"Tag {label!r} already exists")
            fields["label"] = label
        if updates.get("color") is not None:
            fields["color"] = updates["color"]

        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE tags SET {sets} WHERE id = ?", (*fields.values(), tag_id)
            )
        row = conn.execute(
            "SELECT id, label, color FROM tags WHERE id = ?", (tag_id,)
        ).fetchone()
    assert row is not None
    return TagOut(**dict(row))


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(tag_id: int) -> None:
    _ensure_schema()
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Tag not found")

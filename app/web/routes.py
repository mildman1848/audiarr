"""Server-rendered dashboard UI (minimal, Jinja2-based).

The UI is English-first by default and supports German as an official
alternative via app/web/i18n/de.json. Locale selection is driven by the
UI settings (see app/models/settings.py::UiSettings).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import load_settings
from app.web.i18n_util import get_default_ui_language, load_strings

router = APIRouter()

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _base_context(active_page: str) -> dict:
    """Shared template context: i18n strings, language, version, active nav item."""
    settings = load_settings()
    lang = settings.ui.language or get_default_ui_language()
    return {
        "t": load_strings(lang),
        "lang": lang,
        "version": __version__,
        "active_page": active_page,
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    settings = load_settings()
    strings = load_strings(settings.ui.language or get_default_ui_language())

    context = {
        **_base_context("dashboard"),
        "root_folder_count": len(settings.root_folders),
        "quality_profile_count": len(settings.quality_profiles),
        "primary_metadata_provider": (
            settings.metadata.provider_order[0]
            if settings.metadata.provider_order
            else "none"
        ),
        "audiobookshelf_status": (
            strings["status_ok"]
            if settings.connections.audiobookshelf.enabled
            else strings["status_not_configured"]
        ),
        "m4b_convertarr_status": (
            strings["status_ok"]
            if settings.connections.m4b_convertarr.enabled
            else strings["status_not_configured"]
        ),
    }
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "library.html", _base_context("library"))


@router.get("/library/books/{book_id}", response_class=HTMLResponse)
async def book_detail_page(request: Request, book_id: int) -> HTMLResponse:
    # Server-rendered shell only; the JS client fetches book data by id and
    # handles the 404/error state inline, so no book lookup happens here.
    context = {**_base_context("library"), "book_id": book_id}
    return templates.TemplateResponse(request, "book_detail.html", context)


@router.get("/metadata", response_class=HTMLResponse)
async def metadata_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "metadata.html", _base_context("metadata"))


@router.get("/connections", response_class=HTMLResponse)
async def connections_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "connections.html", _base_context("connections")
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "settings.html", _base_context("settings"))


@router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "search.html", _base_context("search"))


@router.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "activity.html", _base_context("activity"))

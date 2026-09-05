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


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    settings = load_settings()
    lang = settings.ui.language or get_default_ui_language()
    strings = load_strings(lang)

    context = {
        "t": strings,
        "lang": lang,
        "version": __version__,
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

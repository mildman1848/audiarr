"""Forms-login and API-key auth endpoints.

See app/auth.py for the hashing/session-token primitives and the ASGI
middleware that enforces auth on every other route.
"""

from __future__ import annotations

import hmac
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.auth import (
    DEFAULT_SESSION_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    LoginRateLimiter,
    hash_password,
    make_session_token,
    verify_password,
    verify_session_token,
)
from app.config import load_settings
from app.web.i18n_util import get_default_ui_language, load_strings

log = logging.getLogger("audiarr.api.auth")

router = APIRouter()

TEMPLATES_DIR = Path(__file__).parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Route-level state (kept out of app/auth.py, which stays state-free of
# request handling): one process-wide limiter instance for the login route.
_login_rate_limiter = LoginRateLimiter()


class LoginRequest(BaseModel):
    username: str = ""
    password: str = ""


class OkResponse(BaseModel):
    ok: bool = True


class LoginError(BaseModel):
    detail: str


class StatusResponse(BaseModel):
    method: str
    authenticated: bool


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    settings = load_settings()
    if settings.auth.method != "forms":
        return RedirectResponse(url="/", status_code=302)
    # Same language resolution as app/web/routes.py::_base_context: the
    # settings.ui.language field, falling back to the app default.
    lang = settings.ui.language or get_default_ui_language()
    return templates.TemplateResponse(
        request, "login.html", {"active_page": "login", "t": load_strings(lang)}
    )


@router.post(
    "/api/v1/auth/login",
    responses={200: {"model": OkResponse}, 401: {"model": LoginError}},
)
async def login(
    data: LoginRequest, request: Request, response: Response
) -> OkResponse | LoginError:
    # No response_model= here: this route intentionally returns one of two
    # different shapes (OkResponse / LoginError) by status code, and a
    # single response_model would coerce the other shape to match it,
    # silently dropping the "detail" field on the 401 path.
    client_ip = request.client.host if request.client else "unknown"

    allowed, retry_after = _login_rate_limiter.check(client_ip)
    if not allowed:
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return LoginError(detail="Too many login attempts")

    settings = load_settings()
    stored_hash = settings.auth.password_hash

    # Always run a verify against SOME hash (a throwaway one when no
    # password is configured yet) so failure timing doesn't reveal
    # whether the username/hash is even set up, limiting probing.
    password_ok = verify_password(data.password, stored_hash or hash_password(""))
    username_ok = bool(settings.auth.username) and hmac.compare_digest(
        data.username, settings.auth.username
    )
    ok = bool(stored_hash) and username_ok and password_ok

    if not ok:
        _login_rate_limiter.record_failure(client_ip)
        response.status_code = 401
        return LoginError(detail="Invalid credentials")

    _login_rate_limiter.reset(client_ip)
    token = make_session_token(data.username, DEFAULT_SESSION_TTL_SECONDS)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=DEFAULT_SESSION_TTL_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
    )
    return OkResponse()


@router.post("/api/v1/auth/logout", response_model=OkResponse)
async def logout(response: Response) -> OkResponse:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return OkResponse()


@router.get("/api/v1/auth/status", response_model=StatusResponse)
async def status(request: Request) -> StatusResponse:
    settings = load_settings()
    if settings.auth.method == "none":
        return StatusResponse(method=settings.auth.method, authenticated=True)

    authenticated = False
    api_key = settings.auth.api_key
    header_key = request.headers.get("x-api-key", "")
    if api_key and header_key and hmac.compare_digest(header_key, api_key):
        authenticated = True

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and verify_session_token(token):
        authenticated = True

    return StatusResponse(method=settings.auth.method, authenticated=authenticated)

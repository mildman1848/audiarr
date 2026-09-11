"""Tests for auth: forms login (session cookie), API-key auth, the
method=="none" no-op baseline, and the login rate limiter.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.api.routes_auth import _login_rate_limiter


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter():
    # _login_rate_limiter is a module-level singleton in app.api.routes_auth.
    # The app_client fixture reloads app.main (and app.config) per test but
    # not app.api.routes_auth, so this same instance outlives every test in
    # the session — clear it so tests stay independent of run order.
    _login_rate_limiter._failures.clear()
    yield
    _login_rate_limiter._failures.clear()


def _get_settings(client):
    return client.get("/api/v1/settings").json()


def _put_settings(client, settings):
    response = client.put("/api/v1/settings", json=settings)
    assert response.status_code == 200
    return response.json()


def test_method_none_is_wide_open_and_bootstraps_api_key(app_client):
    # No credentials of any kind required when auth.method == "none".
    assert app_client.get("/api/v1/library/stats").status_code == 200
    assert app_client.get("/library").status_code == 200

    body = _get_settings(app_client)
    assert body["auth"]["method"] == "none"
    assert body["auth"]["api_key"] != ""
    assert "password" not in body["auth"]
    assert "password_hash" not in body["auth"]


def test_enable_forms_hides_password_fields_and_persists_hash(app_client):
    # Enabling forms is itself done under auth.method == "none", so this
    # PUT needs no credentials yet.
    settings = _get_settings(app_client)
    settings["auth"]["method"] = "forms"
    settings["auth"]["username"] = "admin"
    settings["auth"]["password"] = "s3cret-pw"

    body = _put_settings(app_client, settings)
    assert "password" not in body["auth"]
    assert "password_hash" not in body["auth"]

    # From here on auth.method == "forms": authenticate before touching
    # /api/v1/settings again.
    login = app_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "s3cret-pw"}
    )
    assert login.status_code == 200

    body = _get_settings(app_client)
    assert "password" not in body["auth"]
    assert "password_hash" not in body["auth"]
    assert body["auth"]["method"] == "forms"

    # Re-PUT with an empty password must keep the stored hash (and thus
    # keep login working), not wipe it.
    settings = _get_settings(app_client)
    settings["auth"]["password"] = ""
    _put_settings(app_client, settings)

    app_client.post("/api/v1/auth/logout")
    login = app_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "s3cret-pw"}
    )
    assert login.status_code == 200


def _enable_forms(client, username="admin", password="s3cret-pw"):
    """Enable forms auth (while method is still "none", so no auth needed)."""
    settings = _get_settings(client)
    settings["auth"]["method"] = "forms"
    settings["auth"]["username"] = username
    settings["auth"]["password"] = password
    return _put_settings(client, settings)


def test_unauthenticated_requests_are_denied(app_client):
    _enable_forms(app_client)

    redirect = app_client.get("/library", follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "/login"

    api = app_client.get("/api/v1/library/stats")
    assert api.status_code == 401
    assert api.json()["detail"] == "Unauthorized"


def test_login_wrong_and_correct_password(app_client):
    _enable_forms(app_client)

    wrong = app_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "Invalid credentials"

    correct = app_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "s3cret-pw"}
    )
    assert correct.status_code == 200
    assert correct.json() == {"ok": True}
    assert "audiarr_session" in correct.cookies

    # The client keeps cookies across requests automatically.
    assert app_client.get("/library").status_code == 200
    assert app_client.get("/api/v1/library/stats").status_code == 200


def test_api_key_header_grants_access_without_cookie(app_client):
    settings = _enable_forms(app_client)
    api_key = settings["auth"]["api_key"]
    assert api_key

    response = app_client.get(
        "/api/v1/library/stats", headers={"X-Api-Key": api_key}
    )
    assert response.status_code == 200


def test_exempt_paths_bypass_auth(app_client):
    _enable_forms(app_client)

    assert app_client.get("/health").status_code == 200
    assert app_client.get("/login").status_code == 200

    webhook = app_client.post(
        "/api/v1/webhooks/m4b-convertarr", json={"status": "failed", "title": "x"}
    )
    # Middleware must not block this with 401; the handler's own response
    # (accepted/not-found) is unrelated to auth.
    assert webhook.status_code != 401


def test_logout_clears_session_cookie(app_client):
    _enable_forms(app_client)
    login = app_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "s3cret-pw"}
    )
    assert login.status_code == 200
    assert app_client.get("/library").status_code == 200

    logout = app_client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}

    redirect = app_client.get("/library", follow_redirects=False)
    assert redirect.status_code == 302


def test_login_rate_limit_blocks_after_five_failures(app_client):
    _enable_forms(app_client)

    for _ in range(5):
        wrong = app_client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
        )
        assert wrong.status_code == 401

    # 6th attempt is blocked even with the correct password.
    limited = app_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "s3cret-pw"}
    )
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Too many login attempts"}
    assert int(limited.headers["Retry-After"]) > 0


def test_login_rate_limit_is_per_ip(app_client):
    _enable_forms(app_client)

    for _ in range(5):
        assert (
            app_client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
            ).status_code
            == 401
        )
    assert (
        app_client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
        ).status_code
        == 429
    )

    # A different client IP is unaffected by the first IP's exhausted
    # window. TestClient's `client=` param sets the fake remote address
    # that ends up in request.client.host, so this is a real request from
    # a distinct IP, not a direct limiter manipulation.
    other_client = TestClient(app_client.app, client=("203.0.113.5", 54321))
    other = other_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
    )
    assert other.status_code == 401


def test_login_success_resets_rate_limit_counter(app_client):
    _enable_forms(app_client)

    for _ in range(4):
        assert (
            app_client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
            ).status_code
            == 401
        )

    correct = app_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "s3cret-pw"}
    )
    assert correct.status_code == 200

    # If the successful login had NOT reset the window, these cumulative
    # failures (4 + these) would trip the limiter well before the 5th one.
    # Since it did reset, all 5 fresh failures stay under the limit.
    for _ in range(5):
        assert (
            app_client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "nope"}
            ).status_code
            == 401
        )

    limited = app_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "s3cret-pw"}
    )
    assert limited.status_code == 429

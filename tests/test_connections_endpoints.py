"""API-level tests for the connection test endpoints.

These hit real (unreachable) local addresses, so they exercise the
failure path without needing mocks at the HTTP-client level or any
external network access.
"""


def test_audiobookshelf_test_endpoint_unreachable(app_client):
    response = app_client.post(
        "/api/v1/connections/audiobookshelf/test",
        json={"url": "http://127.0.0.1:1", "api_key": "x"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_m4b_convertarr_test_endpoint_unreachable(app_client):
    response = app_client.post(
        "/api/v1/connections/m4b-convertarr/test",
        json={"url": "http://127.0.0.1:1"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False

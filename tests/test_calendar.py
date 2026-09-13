"""Calendar API tests: date-range filtering, monitored filter, validation."""

from __future__ import annotations


def _create_book(app_client, **overrides) -> dict:
    payload = {
        "title": "Der Vorleser",
        "authors": ["Bernhard Schlink"],
        "language": "de",
        "provider": "audible",
        "provider_id": "B004UWRY6M",
        "locale": "de",
    }
    payload.update(overrides)
    resp = app_client.post("/api/v1/library/books", json=payload)
    assert resp.status_code == 201
    return resp.json()


def test_calendar_empty_range_returns_empty_list(app_client):
    resp = app_client.get("/api/v1/calendar?start=2030-01-01&end=2030-01-31")
    assert resp.status_code == 200
    assert resp.json() == []


def test_calendar_returns_books_with_release_date_in_range(app_client):
    book = _create_book(app_client, release_date="2030-06-15")
    _create_book(
        app_client,
        title="Outside Range",
        release_date="2030-07-01",
        provider_id="B004UWRY6M-OUT",
    )

    resp = app_client.get("/api/v1/calendar?start=2030-06-01&end=2030-06-30")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["id"] == book["id"]
    assert row["title"] == "Der Vorleser"
    assert row["authors"] == ["Bernhard Schlink"]
    assert row["release_date"] == "2030-06-15"
    assert row["monitored"] is True


def test_calendar_excludes_books_without_release_date(app_client):
    _create_book(app_client)  # release_date left NULL

    resp = app_client.get("/api/v1/calendar?start=2000-01-01&end=2099-12-31")
    assert resp.status_code == 200
    assert resp.json() == []


def test_calendar_range_is_inclusive_of_both_endpoints(app_client):
    _create_book(app_client, title="Start Day", release_date="2030-06-01", provider_id="B1")
    _create_book(app_client, title="End Day", release_date="2030-06-30", provider_id="B2")

    resp = app_client.get("/api/v1/calendar?start=2030-06-01&end=2030-06-30")
    assert resp.status_code == 200
    titles = {row["title"] for row in resp.json()}
    assert titles == {"Start Day", "End Day"}


def test_calendar_monitored_filter(app_client):
    _create_book(
        app_client, title="Monitored Book", release_date="2030-06-10", provider_id="B-mon"
    )
    _create_book(
        app_client,
        title="Unmonitored Book",
        release_date="2030-06-11",
        provider_id="B-unmon",
        monitored=False,
    )

    both = app_client.get("/api/v1/calendar?start=2030-06-01&end=2030-06-30")
    assert both.status_code == 200
    assert {row["title"] for row in both.json()} == {"Monitored Book", "Unmonitored Book"}

    only_monitored = app_client.get(
        "/api/v1/calendar?start=2030-06-01&end=2030-06-30&monitored=true"
    )
    assert only_monitored.status_code == 200
    assert {row["title"] for row in only_monitored.json()} == {"Monitored Book"}

    only_unmonitored = app_client.get(
        "/api/v1/calendar?start=2030-06-01&end=2030-06-30&monitored=false"
    )
    assert only_unmonitored.status_code == 200
    assert {row["title"] for row in only_unmonitored.json()} == {"Unmonitored Book"}


def test_calendar_rejects_start_after_end(app_client):
    resp = app_client.get("/api/v1/calendar?start=2030-06-30&end=2030-06-01")
    assert resp.status_code == 400


def test_calendar_rejects_non_iso_dates(app_client):
    resp = app_client.get("/api/v1/calendar?start=06/01/2030&end=2030-06-30")
    assert resp.status_code == 422


def test_calendar_requires_start_and_end(app_client):
    resp = app_client.get("/api/v1/calendar")
    assert resp.status_code == 422

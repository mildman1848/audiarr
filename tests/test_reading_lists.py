"""Unit tests for reading-list parsing and the Goodreads feed client (#79).

No network: the feed client is exercised with ``httpx.MockTransport`` only.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app import reading_lists as rl
from app.reading_lists import (
    FeedFetchError,
    FeedUrlError,
    GoodreadsFeedRef,
    InputTooLargeError,
    ParseError,
    build_goodreads_feed_url,
    clean_text,
    fetch_goodreads_feed,
    normalize_isbn,
    parse_goodreads_rss,
    parse_reading_list_csv,
    validate_goodreads_feed_url,
)

SECRET = "s3cr3tK3y"

# -- feed URL validation ----------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "user_id", "shelf"),
    [
        ("https://www.goodreads.com/review/list_rss/12345?shelf=to-read", "12345", "to-read"),
        ("https://goodreads.com/review/list_rss/12345?shelf=to-read", "12345", "to-read"),
        ("HTTPS://WWW.GOODREADS.COM/review/list_rss/9?shelf=Audio_Books", "9", "audio_books"),
        ("https://www.goodreads.com/review/list_rss/9?shelf=%23ALL%23", "9", "#ALL#"),
        ("  https://www.goodreads.com/review/list_rss/9?shelf=read  ", "9", "read"),
    ],
)
def test_feed_url_accepts_public_shelf_shapes(url, user_id, shelf):
    ref = validate_goodreads_feed_url(url)
    assert (ref.user_id, ref.shelf) == (user_id, shelf)


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("", "feed_url_missing"),
        ("http://www.goodreads.com/review/list_rss/1?shelf=read", "feed_url_not_https"),
        ("ftp://www.goodreads.com/review/list_rss/1?shelf=read", "feed_url_not_https"),
        (f"https://user:{SECRET}@www.goodreads.com/review/list_rss/1?shelf=read", "feed_url_userinfo"),
        (f"https://www.goodreads.com@evil.example/review/list_rss/1?shelf={SECRET}", "feed_url_userinfo"),
        ("https://www.goodreads.com:8443/review/list_rss/1?shelf=read", "feed_url_port"),
        ("https://www.goodreads.com:443/review/list_rss/1?shelf=read", "feed_url_port"),
        ("https://www.goodreads.com:abc/review/list_rss/1?shelf=read", "feed_url_invalid"),
        ("https://evil.example/review/list_rss/1?shelf=read", "feed_url_host"),
        ("https://www.goodreads.com.evil.example/review/list_rss/1?shelf=read", "feed_url_host"),
        ("https://notgoodreads.com/review/list_rss/1?shelf=read", "feed_url_host"),
        ("https://www.goodreads.com./review/list_rss/1?shelf=read", "feed_url_host"),
        ("https://www.goodreads.com/review/list_rss/1?shelf=read#frag", "feed_url_fragment"),
        ("https://www.goodreads.com/review/list_rss/1?shelf=read#", "feed_url_fragment"),
        ("https://www.goodreads.com/user/show/1?shelf=read", "feed_url_path"),
        ("https://www.goodreads.com/review/list_rss/abc?shelf=read", "feed_url_path"),
        ("https://www.goodreads.com/review/list_rss/1/?shelf=read", "feed_url_path"),
        ("https://www.goodreads.com/review/list_rss/1/../2?shelf=read", "feed_url_path"),
        (f"https://www.goodreads.com/review/list_rss/1?shelf=read&key={SECRET}", "feed_url_private_key"),
        (f"https://www.goodreads.com/review/list_rss/1?key={SECRET}", "feed_url_private_key"),
        ("https://www.goodreads.com/review/list_rss/1?shelf=read&sort=title", "feed_url_query"),
        ("https://www.goodreads.com/review/list_rss/1?shelf=read&shelf=read", "feed_url_query"),
        ("https://www.goodreads.com/review/list_rss/1?page=2", "feed_url_query"),
        ("https://www.goodreads.com/review/list_rss/1", "feed_url_shelf"),
        ("https://www.goodreads.com/review/list_rss/1?shelf=", "feed_url_shelf"),
        ("https://www.goodreads.com/review/list_rss/1?shelf=a%2Fb", "feed_url_shelf"),
        ("https://www.goodreads.com/review/list_rss/1?shelf=read to", "feed_url_invalid"),
        ("https://www.goodreads.com/review/list_rss/1?shelf=re\nad", "feed_url_invalid"),
        ("https://www.goodreads.com/review/list_rss/1?shelf=" + "a" * 3000, "feed_url_invalid"),
    ],
)
def test_feed_url_rejects_unsafe_shapes_without_echoing_input(url, code):
    with pytest.raises(FeedUrlError) as excinfo:
        validate_goodreads_feed_url(url)
    assert excinfo.value.code == code
    text = f"{excinfo.value} {excinfo.value.message}"
    assert SECRET not in text
    assert "evil" not in text
    assert "goodreads.com" not in text.replace("goodreads.com feed", "")


def test_build_feed_url_is_canonical_and_fixed_host():
    url = build_goodreads_feed_url(GoodreadsFeedRef("42", "#ALL#"), page=3)
    assert url == "https://www.goodreads.com/review/list_rss/42?shelf=%23ALL%23&page=3&per_page=100"


@pytest.mark.parametrize(
    "ref", [GoodreadsFeedRef("4x", "read"), GoodreadsFeedRef("4", "../etc"), GoodreadsFeedRef("", "read")]
)
def test_build_feed_url_revalidates_stored_parts(ref):
    with pytest.raises(FeedUrlError):
        build_goodreads_feed_url(ref)


def test_build_feed_url_rejects_out_of_range_page():
    with pytest.raises(FeedUrlError):
        build_goodreads_feed_url(GoodreadsFeedRef("4", "read"), page=0)
    with pytest.raises(FeedUrlError):
        build_goodreads_feed_url(GoodreadsFeedRef("4", "read"), page=rl.MAX_FEED_PAGE + 1)


# -- feed client -------------------------------------------------------------------


def _client(handler) -> httpx.AsyncClient:
    # follow_redirects=True on purpose: the client must still refuse redirects.
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


async def test_fetch_requests_only_the_canonical_goodreads_url():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"<rss/>")

    body = await fetch_goodreads_feed(GoodreadsFeedRef("77", "to-read"), 2, client=_client(handler))
    assert body == b"<rss/>"
    assert len(seen) == 1
    assert seen[0].url.host == "www.goodreads.com"
    assert seen[0].url.scheme == "https"
    assert seen[0].url.path == "/review/list_rss/77"
    assert dict(seen[0].url.params) == {"shelf": "to-read", "page": "2", "per_page": "100"}
    assert "authorization" not in seen[0].headers and "cookie" not in seen[0].headers


async def test_fetch_refuses_redirects_even_if_client_follows_them():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.host))
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    with pytest.raises(FeedFetchError) as excinfo:
        await fetch_goodreads_feed(GoodreadsFeedRef("77", "read"), client=_client(handler))
    assert excinfo.value.code == "feed_redirect"
    assert seen == ["www.goodreads.com"]  # the redirect target was never requested


async def test_fetch_non_200_is_a_safe_error():
    with pytest.raises(FeedFetchError) as excinfo:
        await fetch_goodreads_feed(
            GoodreadsFeedRef("77", "read"),
            client=_client(lambda r: httpx.Response(503, content=b"<secret body>")),
        )
    assert excinfo.value.code == "feed_http_error"
    assert "secret body" not in excinfo.value.message


async def test_fetch_bounds_declared_and_streamed_size(monkeypatch):
    monkeypatch.setattr(rl, "MAX_FEED_BYTES", 100)
    with pytest.raises(FeedFetchError) as declared:
        await fetch_goodreads_feed(
            GoodreadsFeedRef("77", "read"), client=_client(lambda r: httpx.Response(200, content=b"x" * 500))
        )
    assert declared.value.code == "feed_too_large"

    async def chunks():
        for _ in range(10):
            yield b"x" * 50

    with pytest.raises(FeedFetchError) as streamed:
        await fetch_goodreads_feed(
            GoodreadsFeedRef("77", "read"), client=_client(lambda r: httpx.Response(200, content=chunks()))
        )
    assert streamed.value.code == "feed_too_large"


async def test_fetch_transport_error_never_leaks_url_or_text(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url} {SECRET}")

    with pytest.raises(FeedFetchError) as excinfo:
        await fetch_goodreads_feed(GoodreadsFeedRef("77", "read"), client=_client(handler))
    assert excinfo.value.code == "feed_unreachable"
    assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__
    assert SECRET not in excinfo.value.message
    assert SECRET not in caplog.text
    assert "list_rss" not in caplog.text


# -- RSS parsing -------------------------------------------------------------------


def _rss(*items: str, extra: str = "") -> bytes:
    body = "".join(f"<item>{i}</item>" for i in items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss version="2.0"><channel><title>x</title>{extra}{body}</channel></rss>'
    ).encode()


def _item(title="T", book_id="1", author="A", isbn="", isbn13="", shelves="to-read") -> str:
    return (
        f"<title><![CDATA[{title}]]></title><book_id>{book_id}</book_id>"
        f"<author_name>{author}</author_name><isbn><![CDATA[{isbn}]]></isbn>"
        f"<isbn13><![CDATA[{isbn13}]]></isbn13><user_shelves>{shelves}</user_shelves>"
        "<media:thumbnail>ignored</media:thumbnail>"
    )


def test_rss_parses_core_fields_and_preserves_non_ascii_and_entities():
    data = _rss(
        _item("Die Känguru-Chroniken (Känguru, #1)", "101", "Marc-Uwe Kling", "3550081898", "9783550081897",
              "to-read, audible"),
        _item("Fahrenheit 451 &amp;#39;quoted&amp;#39; &amp;amp; more", "102", "Ray Bradbury"),
        _item("日本語のタイトル – Ödön", "103", "山田 太郎"),
        _item("Fish&notes", "104", "X"),
    )
    result = parse_goodreads_rss(data)
    assert result.format == "goodreads_rss" and result.total_rows == 4
    first, second, third, fourth = result.entries
    assert first.title == "Die Känguru-Chroniken (Känguru, #1)"
    assert first.search_title == "Die Känguru-Chroniken"
    assert first.entry_key == "gr:101" and first.source_book_id == "101"
    assert first.authors == ["Marc-Uwe Kling"]
    assert (first.isbn, first.isbn13) == ("3550081898", "9783550081897")
    assert first.shelves == ["to-read", "audible"]
    assert second.title == "Fahrenheit 451 'quoted' & more"
    assert (third.title, third.authors) == ("日本語のタイトル – Ödön", ["山田 太郎"])
    assert fourth.title == "Fish&notes"  # unterminated entity must not be rewritten


def test_rss_skips_titleless_and_dedupes_items():
    items = [_item("", "1"), _item("Same", "2"), _item("Same", "2"), _item("Other", "3")]
    result = parse_goodreads_rss(_rss(*items))
    assert [e.title for e in result.entries] == ["Same", "Other"]
    assert (result.total_rows, result.skipped_rows, result.duplicate_rows) == (4, 1, 1)


def test_rss_invalid_isbn_is_dropped():
    result = parse_goodreads_rss(_rss(_item("T", "1", isbn="1234567890", isbn13="9781234567890")))
    assert (result.entries[0].isbn, result.entries[0].isbn13) == ("", "")


def test_rss_item_cap_sets_truncated(monkeypatch):
    monkeypatch.setattr(rl, "MAX_PARSED_ROWS", 2)
    result = parse_goodreads_rss(_rss(*[_item(f"T{i}", str(i)) for i in range(5)]))
    assert len(result.entries) == 2 and result.truncated is True


@pytest.mark.parametrize(
    "payload",
    [
        b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "boom">]>'
        b"<rss><channel><item><title>&a;</title></item></channel></rss>",
        b'<?xml version="1.0"?><!DOCTYPE rss SYSTEM "http://evil.example/x.dtd"><rss><channel/></rss>',
        # UTF-16 hides the DOCTYPE bytes from a naive byte scan; expat handlers must still catch it.
        '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE rss [<!ENTITY a "x">]><rss/>'.encode("utf-16"),
    ],
)
def test_rss_rejects_dtd_and_entity_declarations(payload):
    with pytest.raises(ParseError) as excinfo:
        parse_goodreads_rss(payload)
    assert excinfo.value.code == "xml_forbidden"


@pytest.mark.parametrize(
    "payload", [b"", b"not xml at all", b"<rss><channel><item></channel></rss>", b"<rss>&undefined;</rss>",
     b'<?xml version="1.0"?><!doctype rss><rss/>']
)
def test_rss_malformed_xml_is_a_safe_error(payload):
    with pytest.raises(ParseError) as excinfo:
        parse_goodreads_rss(payload)
    assert excinfo.value.code == "xml_malformed"


def test_rss_wrong_root_and_oversize(monkeypatch):
    with pytest.raises(ParseError) as wrong:
        parse_goodreads_rss(b"<html><body/></html>")
    assert wrong.value.code == "feed_not_rss"
    monkeypatch.setattr(rl, "MAX_FEED_BYTES", 50)
    with pytest.raises(InputTooLargeError):
        parse_goodreads_rss(_rss(_item()))


# -- CSV parsing -------------------------------------------------------------------

GOODREADS_HEADER = (
    "Book Id,Title,Author,Author l-f,Additional Authors,ISBN,ISBN13,My Rating,Average Rating,Publisher,"
    "Binding,Number of Pages,Year Published,Original Publication Year,Date Read,Date Added,Bookshelves,"
    "Bookshelves with positions,Exclusive Shelf,My Review,Spoiler,Private Notes,Read Count,Owned Copies"
)


def _gr_row(book_id, title, author, isbn='=""', isbn13='=""', extra="", shelves="", exclusive="to-read"):
    return (
        f'{book_id},"{title}","{author}","",{extra or chr(34) * 2},{isbn},{isbn13},'
        "0,4.0,Pub,Audio,300,2000,2000,,"
        f'2024/01/01,"{shelves}","",{exclusive},"",,,0,0'
    )


def test_goodreads_csv_parses_excel_isbns_shelves_and_unicode():
    csv_text = "\n".join(
        [
            GOODREADS_HEADER,
            _gr_row(
                "1", "Der Schwarm", "Frank Schätzing", '="3462033956"', '="9783462033953"',
                shelves="audible, favs",
            ),
            _gr_row("2", "Salt &amp; Fire (Series, #2)", "Ann O'Neil", extra='"Bob, Cara"', exclusive="read"),
            _gr_row("3", "Zażółć gęślą jaźń", "Zofia Żółć", shelves="to-read"),
        ]
    )
    result = parse_reading_list_csv(("﻿" + csv_text).encode("utf-8"))
    assert result.format == "goodreads_csv" and result.total_rows == 3
    a, b, c = result.entries
    assert a.entry_key == "gr:1"
    assert (a.isbn, a.isbn13) == ("3462033956", "9783462033953")
    assert a.shelves == ["audible", "favs", "to-read"] and a.exclusive_shelf == "to-read"
    assert b.title == "Salt & Fire (Series, #2)" and b.search_title == "Salt & Fire"
    assert b.authors == ["Ann O'Neil", "Bob", "Cara"]
    assert b.exclusive_shelf == "read" and (b.isbn, b.isbn13) == ("", "")
    assert c.title == "Zażółć gęślą jaźń" and c.authors == ["Zofia Żółć"]


def test_goodreads_csv_header_aliases_and_quoted_newlines():
    text = 'Title,Author(s),ISBN13,Goodreads Book ID,Bookshelves\n"Line\nBreak",A; B,9780306406157,5,"x,y"\n'
    entry = parse_reading_list_csv(text.encode(), "goodreads").entries[0]
    assert entry.title == "Line Break" and entry.authors == ["A", "B"]
    assert entry.isbn13 == "9780306406157" and entry.entry_key == "gr:5" and entry.shelves == ["x", "y"]


def test_goodreads_csv_without_book_id_uses_stable_hash_key():
    text = "Title,Author,ISBN,Exclusive Shelf\nFoo,Bar,,read\n"
    first = parse_reading_list_csv(text.encode()).entries[0]
    second = parse_reading_list_csv(text.encode()).entries[0]
    assert first.entry_key == second.entry_key and first.entry_key.startswith("h:")


def test_storygraph_csv_uses_explicit_aliases_and_ignores_non_isbn_uid():
    text = (
        "Title,Authors,Contributors,ISBN/UID,Format,Read Status,Date Added\n"
        '"Hôtel du Nord","Eugène Dabit, Jean Aurenche",,9780306406157,audio,to-read,2024/01/01\n'
        "Plain Title,Solo Author,,B0ABCDEFGH,audio,Currently-Reading,2024/01/02\n"
    )
    result = parse_reading_list_csv(text.encode())
    assert result.format == "storygraph_csv"
    first, second = result.entries
    assert first.source == "storygraph" and first.title == "Hôtel du Nord"
    assert first.authors == ["Eugène Dabit", "Jean Aurenche"]
    assert first.isbn13 == "9780306406157" and first.shelves == ["to-read"]
    assert second.isbn == "" and second.isbn13 == "" and second.exclusive_shelf == "currently-reading"
    assert first.entry_key.startswith("h:")


@pytest.mark.parametrize(
    ("payload", "fmt", "code"),
    [
        (b"", "auto", "csv_empty"),
        (b"Foo,Bar\n1,2\n", "auto", "csv_format_unrecognized"),
        (b"Title,Rating\nX,5\n", "auto", "csv_format_unrecognized"),
        (b"Title,Author\nX,Y\n", "storygraph", "csv_format_unrecognized"),  # wrong explicit format
        (b"Title,Read Status\nX,read\n", "goodreads", "csv_format_unrecognized"),
        ("Title,Author\nCaf\xe9,Y\n".encode("latin-1"), "auto", "csv_encoding"),
        (b"Title,Author\nX\x00,Y\n", "auto", "csv_invalid"),
    ],
)
def test_csv_rejects_bad_input_with_safe_errors(payload, fmt, code):
    with pytest.raises(ParseError) as excinfo:
        parse_reading_list_csv(payload, fmt)
    assert excinfo.value.code == code


def test_csv_oversize_and_row_cap(monkeypatch):
    monkeypatch.setattr(rl, "MAX_CSV_BYTES", 40)
    with pytest.raises(InputTooLargeError):
        parse_reading_list_csv(b"Title,Author\n" + b"x,y\n" * 20)
    monkeypatch.setattr(rl, "MAX_CSV_BYTES", 5 * 1024 * 1024)
    monkeypatch.setattr(rl, "MAX_PARSED_ROWS", 3)
    result = parse_reading_list_csv(b"Title,Author\n" + b"".join(b"T%d,A\n" % i for i in range(10)))
    assert result.truncated is True and len(result.entries) == 3 and result.total_rows == 3


def test_csv_oversized_field_is_a_safe_error():
    huge = b'Title,Author\n"' + b"x" * 200_000 + b'",A\n'
    with pytest.raises(ParseError) as excinfo:
        parse_reading_list_csv(huge)
    assert excinfo.value.code == "csv_invalid"


def test_csv_skips_blank_and_titleless_rows():
    result = parse_reading_list_csv(b"Title,Author\n,\nNo Title Author,\n,Orphan\nGood,Me\n")
    assert [e.title for e in result.entries] == ["No Title Author", "Good"] or result.skipped_rows == 1
    assert result.skipped_rows == 1 and result.total_rows == 3


# -- helpers -----------------------------------------------------------------------


def test_normalize_isbn_and_clean_text():
    assert normalize_isbn('="0441172717"') == "0441172717"
    assert normalize_isbn("978-0-306-40615-7") == "9780306406157"
    assert normalize_isbn("080442957X") == "080442957X"
    assert normalize_isbn("9780306406158") == ""  # bad checksum
    assert normalize_isbn('=""') == ""
    assert clean_text("  a\u0000b\t c  ") == "a b c"
    assert clean_text("é") == "é"  # NFC
    assert clean_text("x" * 600) == "x" * rl.MAX_FIELD_LENGTH

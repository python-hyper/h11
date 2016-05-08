import pytest

from .._headers import *

def test_normalize_and_validate():
    assert normalize_and_validate([("foo", "bar")]) == [(b"foo", b"bar")]
    assert normalize_and_validate([(b"foo", b"bar")]) == [(b"foo", b"bar")]

    # no leading/trailing whitespace in names
    with pytest.raises(ProtocolError):
        normalize_and_validate([(b"foo ", "bar")])
    with pytest.raises(ProtocolError):
        normalize_and_validate([(b" foo", "bar")])

    # leading/trailing whitespace on values is stripped
    assert normalize_and_validate([("foo", "   bar  ")]) == [(b"foo", b"bar")]

    # content-length
    assert (normalize_and_validate([("Content-Length", "1")])
            == [(b"content-length", b"1")])
    with pytest.raises(ProtocolError):
        normalize_and_validate([("Content-Length", "asdf")])
    with pytest.raises(ProtocolError):
        normalize_and_validate([
            ("Content-Length", "1"),
            ("Content-Length", "2"),
        ])

    # transfer-encoding
    assert (normalize_and_validate([("Transfer-Encoding", "chunked")])
            == [(b"transfer-encoding", b"chunked")])
    assert (normalize_and_validate([("Transfer-Encoding", "cHuNkEd")])
            == [(b"transfer-encoding", b"chunked")])
    with pytest.raises(ProtocolError):
        normalize_and_validate([("Transfer-Encoding", "gzip")])
    with pytest.raises(ProtocolError):
        normalize_and_validate([
            ("Transfer-Encoding", "chunked"),
            ("Transfer-Encoding", "gzip"),
        ])

def test_get_set_comma_header():
    headers = normalize_and_validate([
        ("Connection", "close"),
        ("whatever", "something"),
        ("connectiON", "fOo,, , BAR "),
        ])

    assert get_comma_header(headers, "connECtion") == [
        b"close", b"foo", b"bar"]
    assert get_comma_header(headers, "connECtion", lowercase=False) == [
        b"close", b"fOo", b"BAR"]

    set_comma_header(headers, "NewThing", [" a", "b"])

    assert headers == [
        (b"connection", b"close"),
        (b"whatever", b"something"),
        (b"connection", b"fOo,, , BAR"),
        (b"newthing", b"a"),
        (b"newthing", b"b"),
    ]

    set_comma_header(headers, "whatever", ["different thing"])

    assert headers == [
        (b"connection", b"close"),
        (b"connection", b"fOo,, , BAR"),
        (b"newthing", b"a"),
        (b"newthing", b"b"),
        (b"whatever", b"different thing"),
    ]

def test_has_100_continue():
    from .._events import Request

    assert has_expect_100_continue(Request(
        method="GET",
        target="/",
        headers=[("Host", "example.com"), ("Expect", "100-continue")]))
    assert not has_expect_100_continue(Request(
        method="GET",
        target="/",
        headers=[("Host", "example.com")]))
    # Case sensitive
    assert not has_expect_100_continue(Request(
        method="GET",
        target="/",
        headers=[("Host", "example.com"), ("Expect", "100-Continue")]))
    # Doesn't work in HTTP/1.0
    assert not has_expect_100_continue(Request(
        method="GET",
        target="/",
        headers=[("Host", "example.com"), ("Expect", "100-continue")],
        http_version="1.0"))

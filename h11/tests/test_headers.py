import pytest

from .._headers import LocalProtocolError
from .._headers import normalize_and_validate
from .._headers import set_comma_header, get_comma_header
from .._headers import has_expect_100_continue


def test_normalize_and_validate():
    assert normalize_and_validate([("foo", "bar")]) == [(b"foo", b"bar")]
    assert normalize_and_validate([(b"foo", b"bar")]) == [(b"foo", b"bar")]

    # no leading/trailing whitespace in names
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([(b"foo ", "bar")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([(b" foo", "bar")])

    # no weird characters in names
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([(b"foo bar", b"baz")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([(b"foo\x00bar", b"baz")])

    # no return or NUL characters in values
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("foo", "bar\rbaz")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("foo", "bar\nbaz")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("foo", "bar\x00baz")])
    # no leading/trailing whitespace
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("foo", "  barbaz  ")])

    # content-length
    assert (normalize_and_validate([("Content-Length", "1")])
            == [(b"content-length", b"1")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("Content-Length", "asdf")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([("Content-Length", "1x")])
    with pytest.raises(LocalProtocolError):
        normalize_and_validate([
            ("Content-Length", "1"),
            ("Content-Length", "2"),
        ])

    # transfer-encoding
    assert (normalize_and_validate([("Transfer-Encoding", "chunked")])
            == [(b"transfer-encoding", b"chunked")])
    assert (normalize_and_validate([("Transfer-Encoding", "cHuNkEd")])
            == [(b"transfer-encoding", b"chunked")])
    with pytest.raises(LocalProtocolError) as excinfo:
        normalize_and_validate([("Transfer-Encoding", "gzip")])
    assert excinfo.value.error_status_hint == 501  # Not Implemented
    with pytest.raises(LocalProtocolError) as excinfo:
        normalize_and_validate([
            ("Transfer-Encoding", "chunked"),
            ("Transfer-Encoding", "gzip"),
        ])
    assert excinfo.value.error_status_hint == 501  # Not Implemented


def test_get_set_comma_header():
    headers = normalize_and_validate([
        ("Connection", "close"),
        ("whatever", "something"),
        ("connectiON", "fOo,, , BAR"),
    ])

    assert get_comma_header(headers, "connECtion") == [
        b"close", b"foo", b"bar"]
    assert get_comma_header(headers, "connECtion", lowercase=False) == [
        b"close", b"fOo", b"BAR"]

    set_comma_header(headers, "NewThing", ["a", "b"])

    with pytest.raises(LocalProtocolError):
        set_comma_header(headers, "NewThing", ["  a", "b"])

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

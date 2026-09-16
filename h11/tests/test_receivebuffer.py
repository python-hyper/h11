import re
from types import SimpleNamespace
from typing import List, Match, Optional, Tuple

import pytest

from .. import _receivebuffer
from .._receivebuffer import ReceiveBuffer


def test_receivebuffer() -> None:
    b = ReceiveBuffer()
    assert not b
    assert len(b) == 0
    assert bytes(b) == b""

    b += b"123"
    assert b
    assert len(b) == 3
    assert bytes(b) == b"123"

    assert bytes(b) == b"123"

    assert b.maybe_extract_at_most(2) == b"12"
    assert b
    assert len(b) == 1
    assert bytes(b) == b"3"

    assert bytes(b) == b"3"

    assert b.maybe_extract_at_most(10) == b"3"
    assert bytes(b) == b""

    assert b.maybe_extract_at_most(10) is None
    assert not b

    ################################################################
    # maybe_extract_until_next
    ################################################################

    b += b"123\n456\r\n789\r\n"

    assert b.maybe_extract_next_line() == b"123\n456\r\n"
    assert bytes(b) == b"789\r\n"

    assert b.maybe_extract_next_line() == b"789\r\n"
    assert bytes(b) == b""

    b += b"12\r"
    assert b.maybe_extract_next_line() is None
    assert bytes(b) == b"12\r"

    b += b"345\n\r"
    assert b.maybe_extract_next_line() is None
    assert bytes(b) == b"12\r345\n\r"

    # here we stopped at the middle of b"\r\n" delimiter

    b += b"\n6789aaa123\r\n"
    assert b.maybe_extract_next_line() == b"12\r345\n\r\n"
    assert b.maybe_extract_next_line() == b"6789aaa123\r\n"
    assert b.maybe_extract_next_line() is None
    assert bytes(b) == b""

    ################################################################
    # maybe_extract_lines
    ################################################################

    b += b"123\r\na: b\r\nfoo:bar\r\n\r\ntrailing"
    lines = b.maybe_extract_lines()
    assert lines == [b"123", b"a: b", b"foo:bar"]
    assert bytes(b) == b"trailing"

    assert b.maybe_extract_lines() is None

    b += b"\r\n\r"
    assert b.maybe_extract_lines() is None

    assert b.maybe_extract_at_most(100) == b"trailing\r\n\r"
    assert not b

    # Empty body case (as happens at the end of chunked encoding if there are
    # no trailing headers, e.g.)
    b += b"\r\ntrailing"
    assert b.maybe_extract_lines() == []
    assert bytes(b) == b"trailing"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(
            (
                b"HTTP/1.1 200 OK\r\n",
                b"Content-type: text/plain\r\n",
                b"Connection: close\r\n",
                b"\r\n",
                b"Some body",
            ),
            id="with_crlf_delimiter",
        ),
        pytest.param(
            (
                b"HTTP/1.1 200 OK\n",
                b"Content-type: text/plain\n",
                b"Connection: close\n",
                b"\n",
                b"Some body",
            ),
            id="with_lf_only_delimiter",
        ),
        pytest.param(
            (
                b"HTTP/1.1 200 OK\n",
                b"Content-type: text/plain\r\n",
                b"Connection: close\n",
                b"\n",
                b"Some body",
            ),
            id="with_mixed_crlf_and_lf",
        ),
    ],
)
def test_receivebuffer_for_invalid_delimiter(data: Tuple[bytes]) -> None:
    b = ReceiveBuffer()

    for line in data:
        b += line

    lines = b.maybe_extract_lines()

    assert lines == [
        b"HTTP/1.1 200 OK",
        b"Content-type: text/plain",
        b"Connection: close",
    ]
    assert bytes(b) == b"Some body"


@pytest.fixture
def receivebuffer_with_scan_sizes(
    monkeypatch: pytest.MonkeyPatch,
) -> Tuple[ReceiveBuffer, List[int]]:
    """Count search-window sizes, not timings or native bytearray copying."""

    # A subclass lets us wrap find on this instance without replacing its storage.
    class TrackedBytearray(bytearray):
        pass

    data = TrackedBytearray()
    scan_sizes: List[int] = []
    real_find = data.find
    real_search = _receivebuffer.blank_line_regex.search

    def find(sub: bytes, start: int = 0) -> int:
        scan_sizes.append(max(0, len(data) - start))
        return real_find(sub, start)

    def search(buffer: bytearray, start: int = 0) -> Optional[Match[bytes]]:
        scan_sizes.append(max(0, len(buffer) - start))
        return real_search(buffer, start)

    monkeypatch.setattr(data, "find", find)
    monkeypatch.setattr(
        _receivebuffer, "blank_line_regex", SimpleNamespace(search=search)
    )
    b = ReceiveBuffer()
    b._data = data
    return b, scan_sizes


@pytest.mark.parametrize("size", [256, 4096])
@pytest.mark.parametrize("consume", [0, 32])
def test_receivebuffer_next_line_scan_bounds(
    receivebuffer_with_scan_sizes: Tuple[ReceiveBuffer, List[int]],
    size: int,
    consume: int,
) -> None:
    b, scan_sizes = receivebuffer_with_scan_sizes
    line = b"x" * size + b"\r\n"
    following = b"next\r\n"

    for _ in range(3):
        scan_sizes.clear()
        for byte in line[:-1]:
            b += bytes([byte])
            assert b.maybe_extract_next_line() is None
            assert b.maybe_extract_next_line() is None

        if consume:
            assert b.maybe_extract_at_most(consume) == line[:consume]

        b += line[-1:] + following
        assert b.maybe_extract_next_line() == line[consume:]
        assert b.maybe_extract_next_line() == following
        assert not b

        # Allow delimiter overlap, unchanged-buffer polls, and a rescan after
        # consuming a prefix, but not a full-buffer scan for every new byte.
        assert scan_sizes
        assert sum(scan_sizes) <= 8 * (len(line) + len(following))


@pytest.mark.parametrize("size", [256, 4096])
@pytest.mark.parametrize("consume", [0, 32])
@pytest.mark.parametrize(
    "line_ending,blank_line",
    [
        pytest.param(b"\r\n", b"\r\n", id="crlf"),
        pytest.param(b"\n", b"\n", id="lf"),
        pytest.param(b"\n", b"\r\n", id="lf-crlf"),
        pytest.param(b"\r\n", b"\n", id="crlf-lf"),
    ],
)
def test_receivebuffer_lines_scan_bounds(
    receivebuffer_with_scan_sizes: Tuple[ReceiveBuffer, List[int]],
    size: int,
    consume: int,
    line_ending: bytes,
    blank_line: bytes,
) -> None:
    b, scan_sizes = receivebuffer_with_scan_sizes
    lines = [b"first: " + b"x" * size, b"second: value"]
    block = line_ending.join(lines) + line_ending + blank_line
    following = b"next: value\r\n\r\n"

    for _ in range(3):
        scan_sizes.clear()
        for byte in block[:-1]:
            b += bytes([byte])
            assert b.maybe_extract_lines() is None
            assert b.maybe_extract_lines() is None

        if consume:
            assert b.maybe_extract_at_most(consume) == block[:consume]

        b += block[-1:] + following + blank_line
        assert b.maybe_extract_lines() == [lines[0][consume:], lines[1]]
        assert b.maybe_extract_lines() == [b"next: value"]
        assert b.maybe_extract_lines() == []
        assert not b

        assert scan_sizes
        assert sum(scan_sizes) <= 8 * (len(block) + len(following) + len(blank_line))

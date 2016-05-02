import pytest

from ..util import ProtocolError
from ..receivebuffer import ReceiveBuffer
from ..headers import normalize_and_validate
from ..state import *
from ..events import *

from ..writers import (
    WRITERS,
    write_headers, write_request, write_any_response,
    ContentLengthWriter, ChunkedWriter, Http10Writer,
)
from ..readers import (
    READERS,
    ContentLengthReader, ChunkedReader, Http10Reader,
)

SIMPLE_CASES = [
    ((CLIENT, IDLE),
     Request(method="GET", target="/a",
             headers=[("Host", "foo"), ("Connection", "close")]),
     b"GET /a HTTP/1.1\r\nhost: foo\r\nconnection: close\r\n\r\n"),

    ((SERVER, IDLE),
     Response(status_code=200, headers=[("Connection", "close")]),
     b"HTTP/1.1 200 \r\nconnection: close\r\n\r\n"),

    ((SERVER, IDLE),
     Response(status_code=200, headers=[]),
     b"HTTP/1.1 200 \r\n\r\n"),

    ((SERVER, IDLE),
     InformationalResponse(status_code=101,
                           headers=[("Upgrade", "websocket")]),
     b"HTTP/1.1 101 \r\nupgrade: websocket\r\n\r\n"),

    ((SERVER, IDLE),
     InformationalResponse(status_code=101, headers=[]),
     b"HTTP/1.1 101 \r\n\r\n"),
]

def tw(writer, obj, expected):
    got_list = []
    writer(obj, got_list.append)
    got = b"".join(got_list)
    assert got == expected

def makebuf(data):
    buf = ReceiveBuffer()
    buf += data
    return buf

def tr(reader, data, expected):
    # Simple: consume whole thing
    buf = makebuf(data)
    assert reader(buf) == expected
    assert not buf

    # Incrementally growing buffer
    buf = ReceiveBuffer()
    for i in range(len(data)):
        buf += data[i:i + 1]
        if len(buf) < len(data):
            assert reader(buf) is None
        else:
            assert reader(buf) == expected

    # Extra
    buf = makebuf(data)
    buf += b"trailing"
    assert reader(buf) == expected
    assert bytes(buf) == b"trailing"

def test_writers_simple():
    for ((role, state), event, binary) in SIMPLE_CASES:
        tw(WRITERS[role, state], event, binary)

def test_readers_simple():
    for ((role, state), event, binary) in SIMPLE_CASES:
        tr(READERS[role, state], binary, event)

def test_writers_unusual():
    # Simple test of the write_headers utility routine
    tw(write_headers,
       normalize_and_validate([("foo", "bar"), ("baz", "quux")]),
       b"foo: bar\r\nbaz: quux\r\n\r\n")
    tw(write_headers, [], b"\r\n")

    # We understand HTTP/1.0, but we don't speak it
    with pytest.raises(ProtocolError):
        tw(write_request,
           Request(method="GET", target="/",
                   headers=[("Host", "foo"), ("Connection", "close")],
                   http_version="1.0"),
           None)
    with pytest.raises(ProtocolError):
        tw(write_any_response,
           Response(status_code=200, headers=[("Connection", "close")],
                   http_version="1.0"),
           None)

def test_readers_unusual():
    # Reading HTTP/1.0
    tr(READERS[CLIENT, IDLE],
       b"HEAD /foo HTTP/1.0\r\nSome: header\r\n\r\n",
       Request(method="HEAD", target="/foo", headers=[("Some", "header")],
               http_version="1.0"))

    # check no-headers, since it's only legal with HTTP/1.0
    tr(READERS[CLIENT, IDLE],
       b"HEAD /foo HTTP/1.0\r\n\r\n",
       Request(method="HEAD", target="/foo", headers=[], http_version="1.0"))

    tr(READERS[SERVER, SEND_RESPONSE],
       b"HTTP/1.0 200 OK\r\nSome: header\r\n\r\n",
       Response(status_code=200, headers=[("Some", "header")],
                http_version="1.0"))

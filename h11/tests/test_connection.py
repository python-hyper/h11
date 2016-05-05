import pytest

from ..util import ProtocolError
from ..events import *
from ..state import *
from ..connection import (
    _keep_alive, _body_framing,
    Connection,
)

from .helpers import ConnectionPair

def test__keep_alive():
    assert _keep_alive(
        Request(method="GET", target="/", headers=[("Host", "Example.com")]))
    assert not _keep_alive(
        Request(method="GET", target="/",
                headers=[("Host", "Example.com"), ("Connection", "close")]))
    assert not _keep_alive(
        Request(method="GET", target="/",
                headers=[("Host", "Example.com"),
                         ("Connection", "a, b, cLOse, foo")]))
    assert not _keep_alive(
        Request(method="GET", target="/", headers=[], http_version="1.0"))

    assert _keep_alive(
        Response(status_code=200, headers=[]))
    assert not _keep_alive(
        Response(status_code=200, headers=[("Connection", "close")]))
    assert not _keep_alive(
        Response(status_code=200,
                 headers=[("Connection", "a, b, cLOse, foo")]))
    assert not _keep_alive(
        Response(status_code=200, headers=[], http_version="1.0"))


def test__body_framing():
    def headers(cl, te):
        headers = []
        if cl is not None:
            headers.append(("Content-Length", str(cl)))
        if te:
            headers.append(("Transfer-Encoding", "chunked"))
        return headers

    def resp(status_code=200, cl=None, te=False):
        return Response(status_code=status_code, headers=headers(cl, te))

    def req(cl=None, te=False):
        h = headers(cl, te)
        h += [("Host", "example.com")]
        return Request(method="GET", target="/", headers=h)

    # Special cases where the headers are ignored:
    for kwargs in [{}, {"cl": 100}, {"te": True}, {"cl": 100, "te": True}]:
        for meth, r in [(b"HEAD", resp(**kwargs)),
                        (b"GET",  resp(status_code=204, **kwargs)),
                        (b"GET",  resp(status_code=304, **kwargs))]:
            assert _body_framing(meth, r) == ("content-length", (0,))

    # Transfer-encoding
    for kwargs in [{"te": True}, {"cl": 100, "te": True}]:
        for meth, r in [(None, req(**kwargs)), (b"GET", resp(**kwargs))]:
            assert _body_framing(meth, r) == ("chunked", ())

    # Content-Length
    for meth, r in [(None, req(cl=100)), (b"GET", resp(cl=100))]:
        assert _body_framing(meth, r) == ("content-length", (100,))

    # No headers
    assert _body_framing(None, req()) == ("content-length", (0,))
    assert _body_framing(b"GET", resp()) == ("http/1.0", ())


def test_Connection_basics_and_content_length():
    with pytest.raises(ValueError):
        Connection("CLIENT")

    p = ConnectionPair()
    assert p.conn[CLIENT].our_role is CLIENT
    assert p.conn[CLIENT].their_role is SERVER
    assert p.conn[SERVER].our_role is SERVER
    assert p.conn[SERVER].their_role is CLIENT

    data = p.send(CLIENT,
                  Request(method="GET", target="/",
                          headers=[("Host", "example.com"),
                                   ("Content-Length", "10")]))
    assert data == (
        b"GET / HTTP/1.1\r\n"
        b"host: example.com\r\n"
        b"content-length: 10\r\n\r\n")

    for conn in p.conns:
        assert conn.state_of(CLIENT) is SEND_BODY
        assert conn.client_state is SEND_BODY
        assert conn.state_of(SERVER) is SEND_RESPONSE
        assert conn.server_state is SEND_RESPONSE
    assert p.conn[CLIENT].our_state is SEND_BODY
    assert p.conn[CLIENT].their_state is SEND_RESPONSE
    assert p.conn[SERVER].our_state is SEND_RESPONSE
    assert p.conn[SERVER].their_state is SEND_BODY

    assert p.conn[CLIENT].their_http_version is None
    assert p.conn[SERVER].their_http_version == b"1.1"

    data = p.send(SERVER,
                  InformationalResponse(status_code=100, headers=[]))
    assert data == b"HTTP/1.1 100 \r\n\r\n"

    data = p.send(SERVER,
                  Response(status_code=200,
                           headers=[("Content-Length", "11")]))
    assert data == b"HTTP/1.1 200 \r\ncontent-length: 11\r\n\r\n"

    for conn in p.conns:
        assert conn.client_state is SEND_BODY
        assert conn.server_state is SEND_BODY

    assert p.conn[CLIENT].their_http_version == b"1.1"
    assert p.conn[SERVER].their_http_version == b"1.1"

    data = p.send(CLIENT, Data(data=b"12345"))
    assert data == b"12345"
    data = p.send(CLIENT, Data(data=b"67890"),
                  expect=[Data(data=b"67890"), EndOfMessage()])
    assert data == b"67890"
    data = p.send(CLIENT, EndOfMessage(), expect=[])
    assert data == b""

    for conn in p.conns:
        assert conn.client_state is DONE
        assert conn.server_state is SEND_BODY

    data = p.send(SERVER, Data(data=b"1234567890"))
    assert data == b"1234567890"
    data = p.send(SERVER, Data(data=b"1"),
                  expect=[Data(data=b"1"), EndOfMessage()])
    assert data == b"1"
    data = p.send(SERVER, EndOfMessage(), expect=[])
    assert data == b""

    for conn in p.conns:
        assert conn.client_state is DONE
        assert conn.server_state is DONE

def test_chunked():
    p = ConnectionPair()

    p.send(CLIENT,
           Request(method="GET", target="/",
                   headers=[("Host", "example.com"),
                            ("Transfer-Encoding", "chunked")]))
    data = p.send(CLIENT, Data(data=b"1234567890"))
    assert data == b"a\r\n1234567890\r\n"
    data = p.send(CLIENT, Data(data=b"abcde"))
    assert data == b"5\r\nabcde\r\n"
    data = p.send(CLIENT, EndOfMessage(headers=[("hello", "there")]))
    assert data == b"0\r\nhello: there\r\n\r\n"

    p.send(SERVER,
           Response(status_code=200,
                    headers=[("Transfer-Encoding", "chunked")]))
    p.send(SERVER, Data(data=b"54321"))
    p.send(SERVER, Data(data=b"12345"))
    p.send(SERVER, EndOfMessage())

    for conn in p.conns:
        assert conn.client_state is DONE
        assert conn.server_state is DONE

def test_client_talking_to_http10_server():
    c = Connection(CLIENT)
    c.send(Request(method="GET", target="/",
                   headers=[("Host", "example.com")]))
    c.send(EndOfMessage())
    assert c.our_state is DONE
    # No content-length, so Http10 framing for body
    assert (c.receive_data(b"HTTP/1.0 200 OK\r\n\r\n")
            == [Response(status_code=200, headers=[], http_version="1.0")])
    assert c.our_state is MUST_CLOSE
    assert (c.receive_data(b"12345") == [Data(data=b"12345")])
    assert (c.receive_data(b"67890") == [Data(data=b"67890")])
    assert (c.receive_data(b"") == [EndOfMessage(), ConnectionClosed()])
    assert c.their_state is CLOSED

def test_server_talking_to_http10_client():
    c = Connection(SERVER)
    # No content-length, so no body
    # NB: no host header
    assert (c.receive_data(b"GET / HTTP/1.0\r\n\r\n")
            == [Request(method="GET", target="/",
                        headers=[],
                        http_version="1.0"),
                EndOfMessage()])
    assert c.their_state is MUST_CLOSE

    # We automatically Connection: close back at them
    assert (c.send(Response(status_code=200, headers=[]))
            == b"HTTP/1.1 200 \r\nconnection: close\r\n\r\n")

    assert c.send(Data(data=b"12345")) == b"12345"
    assert c.send(EndOfMessage()) == b""
    assert c.our_state is MUST_CLOSE

    # Check that it works if they do send Content-Length
    c = Connection(SERVER)
    # NB: no host header
    assert (c.receive_data(b"POST / HTTP/1.0\r\nContent-Length: 10\r\n\r\n1")
            == [Request(method="POST", target="/",
                        headers=[("Content-Length", "10")],
                        http_version="1.0"),
                Data(data=b"1")])
    assert (c.receive_data(b"234567890")
            == [Data(data=b"234567890"), EndOfMessage()])
    assert c.their_state is MUST_CLOSE
    assert c.receive_data(b"") == [ConnectionClosed()]

def test_automatic_transfer_encoding_in_response():
    # Check that in responses, the user can specify either Transfer-Encoding:
    # chunked or no framing at all, and in both cases we automatically select
    # the right option depending on whether the peer speaks HTTP/1.0 or
    # HTTP/1.1
    for user_headers in [[("Transfer-Encoding", "chunked")],
                         [],
                         # In fact, this even works if Content-Length is set,
                         # because if both are set then Transfer-Encoding wins
                         [("Transfer-Encoding", "chunked"),
                          ("Content-Length", "100")]]:
        p = ConnectionPair()
        p.send(CLIENT, [
            Request(method="GET", target="/",
                    headers=[("Host", "example.com")]),
            EndOfMessage(),
        ])
        # When speaking to HTTP/1.1 client, all of the above cases get
        # normalized to Transfer-Encoding: chunked
        p.send(SERVER,
               Response(status_code=200,
                        headers=user_headers),
               expect=Response(status_code=200,
                               headers=[("Transfer-Encoding", "chunked")]))

        # When speaking to HTTP/1.0 client, all of the above cases get
        # normalized to no-framing-headers
        c = Connection(SERVER)
        c.receive_data(b"GET / HTTP/1.0\r\n\r\n")
        assert (c.send(Response(status_code=200, headers=user_headers))
                == b"HTTP/1.1 200 \r\nconnection: close\r\n\r\n")
        assert c.send(Data(data=b"12345")) == b"12345"

def test_automagic_connection_close_handling():
    p = ConnectionPair()
    # If the user explicitly sets Connection: close, then we notice and
    # respect it
    p.send(CLIENT,
           [Request(method="GET", target="/",
                    headers=[("Host", "example.com"),
                             ("Connection", "close")]),
            EndOfMessage()])
    for conn in p.conns:
        assert conn.client_state is MUST_CLOSE
    # And if the client sets it, the server automatically echoes it back
    p.send(SERVER,
           # no header here...
           [Response(status_code=204, headers=[]),
            EndOfMessage()],
           # ...but oh look, it arrived anyway
           expect=[Response(status_code=204,
                            headers=[("connection", "close")]),
                   EndOfMessage()])
    for conn in p.conns:
        assert conn.client_state is MUST_CLOSE
        assert conn.server_state is MUST_CLOSE

def test_100_continue():
    def setup():
        p = ConnectionPair()
        p.send(CLIENT,
               Request(method="GET", target="/",
                       headers=[("Host", "example.com"),
                                ("Content-Length", "100"),
                                ("Expect", "100-continue")]))
        for conn in p.conns:
            assert conn.client_is_waiting_for_100_continue
        assert not p.conn[CLIENT].they_are_waiting_for_100_continue
        assert p.conn[SERVER].they_are_waiting_for_100_continue
        return p

    # Disabled by 100 Continue
    p = setup()
    p.send(SERVER,
           InformationalResponse(status_code=100, headers=[]))
    for conn in p.conns:
        assert not conn.client_is_waiting_for_100_continue
        assert not conn.they_are_waiting_for_100_continue

    # Disabled by a real response
    p = setup()
    p.send(SERVER,
           Response(status_code=200,
                    headers=[("Transfer-Encoding", "chunked")]))
    for conn in p.conns:
        assert not conn.client_is_waiting_for_100_continue
        assert not conn.they_are_waiting_for_100_continue

    # Disabled by the client going ahead and sending stuff anyway
    p = setup()
    p.send(CLIENT, Data(data=b"12345"))
    for conn in p.conns:
        assert not conn.client_is_waiting_for_100_continue
        assert not conn.they_are_waiting_for_100_continue


# reuse
# pipelining
# protocol switching and trailing_data
# - client switching back is buggy -- SEND_BODY isn't the only state that
#   should trigger switch back, and that SEND_BODY does trigger it is a little
#   dicey because the server passes through SEND_BODY briefly to get to
#   SWITCHED_PROTOCOL.
#   probably should trigger the server's SWITCHED_PROTOCOL directly from the
#   Response event so it never hits SEND_BODY, and then make the switch back
#   triggered by {SEND_BODY, DONE, MUST_CLOSE, CLOSED}
# close handling
# sendfile silliness
# error states
# end-to-end versus independent implementations?

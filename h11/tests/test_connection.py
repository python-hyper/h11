import pytest

from .._util import ProtocolError
from .._events import *
from .._state import *
from .._connection import (
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
        assert conn.states == {CLIENT: SEND_BODY, SERVER: SEND_RESPONSE}
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
        assert conn.states == {CLIENT: SEND_BODY, SERVER: SEND_BODY}

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
        assert conn.states == {CLIENT: DONE, SERVER: SEND_BODY}

    data = p.send(SERVER, Data(data=b"1234567890"))
    assert data == b"1234567890"
    data = p.send(SERVER, Data(data=b"1"),
                  expect=[Data(data=b"1"), EndOfMessage()])
    assert data == b"1"
    data = p.send(SERVER, EndOfMessage(), expect=[])
    assert data == b""

    for conn in p.conns:
        assert conn.states == {CLIENT: DONE, SERVER: DONE}

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
        assert conn.states == {CLIENT: DONE, SERVER: DONE}

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
        assert conn.states[CLIENT] is MUST_CLOSE
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
        assert conn.states == {CLIENT: MUST_CLOSE, SERVER: MUST_CLOSE}


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

def test_max_buffer_size_countermeasure():
    # Infinitely long headers are definitely not okay
    c = Connection(SERVER)
    c.receive_data(b"GET / HTTP/1.0\r\nEndless: ")
    with pytest.raises(ProtocolError):
        while True:
            c.receive_data(b"a" * 1024)

    # Checking that the same header is accepted / rejected depending on the
    # max_buffer_size setting:
    c = Connection(SERVER, max_buffer_size=5000)
    c.receive_data(b"GET / HTTP/1.0\r\nBig: ")
    c.receive_data(b"a" * 4000)
    assert c.receive_data(b"\r\n\r\n") == [
        Request(method="GET", target="/", http_version="1.0",
                headers=[("big", "a" * 4000)]),
        EndOfMessage(),
    ]

    c = Connection(SERVER, max_buffer_size=4000)
    c.receive_data(b"GET / HTTP/1.0\r\nBig: ")
    with pytest.raises(ProtocolError):
        c.receive_data(b"a" * 4000)

    # Temporarily exceeding the max buffer size is fine; it's just maintaining
    # large buffers over multiple calls that's a problem:
    c = Connection(SERVER, max_buffer_size=5000)
    c.receive_data(b"GET / HTTP/1.0\r\nContent-Length: 10000")
    assert c.receive_data(b"\r\n\r\n" + b"a" * 10000) == [
        Request(method="GET", target="/", http_version="1.0",
                headers=[("Content-Length", "10000")]),
        Data(data=b"a" * 10000),
        EndOfMessage(),
    ]

    # Exceeding the max buffer size is fine if we are paused
    c = Connection(SERVER, max_buffer_size=100)
    # Two pipelined requests in a big big buffer
    assert (c.receive_data(b"GET /1 HTTP/1.1\r\nHost: a\r\n\r\n"
                           b"GET /2 HTTP/1.1\r\nHost: b\r\n\r\n"
                           + b"X" * 1000)
            == [Request(method="GET", target="/1", headers=[("host", "a")]),
                EndOfMessage(),
                Paused(reason=DONE)])
    # Even more data comes in, no problem
    assert c.receive_data(b"X" * 1000)
    # We can respond and reuse to get the second pipelined request
    c.send(Response(status_code=200, headers=[]))
    c.send(EndOfMessage())
    c.prepare_to_reuse()
    assert (c.receive_data(None)
            == [Request(method="GET", target="/2", headers=[("host", "b")]),
                EndOfMessage(),
                Paused(reason=DONE)])
    # But once we unpause and try to read the next message, the buffer size is
    # enforced again
    c.send(Response(status_code=200, headers=[]))
    c.send(EndOfMessage())
    c.prepare_to_reuse()
    with pytest.raises(ProtocolError):
        c.receive_data(None)

def test_reuse_simple():
    p = ConnectionPair()
    p.send(CLIENT,
           [Request(method="GET", target="/", headers=[("Host", "a")]),
            EndOfMessage()])
    p.send(SERVER,
           [Response(status_code=200, headers=[]),
            EndOfMessage()])
    for conn in p.conns:
        assert conn.states == {CLIENT: DONE, SERVER: DONE}
        conn.prepare_to_reuse()

    p.send(CLIENT,
           [Request(method="DELETE", target="/foo", headers=[("Host", "a")]),
            EndOfMessage()])
    p.send(SERVER,
           [Response(status_code=404, headers=[]),
            EndOfMessage()])

def test_pipelining():
    # Client doesn't support pipelining, so we have to do this by hand
    c = Connection(SERVER)
    assert c.receive_data(None) == []
    # 3 requests all bunched up
    events = c.receive_data(
        b"GET /1 HTTP/1.1\r\nHost: a.com\r\nContent-Length: 5\r\n\r\n"
        b"12345"
        b"GET /2 HTTP/1.1\r\nHost: a.com\r\nContent-Length: 5\r\n\r\n"
        b"67890"
        b"GET /3 HTTP/1.1\r\nHost: a.com\r\n\r\n")
    assert events == [
        Request(method="GET", target="/1",
                headers=[("Host", "a.com"), ("Content-Length", "5")]),
        Data(data=b"12345"),
        EndOfMessage(),
        Paused(reason=DONE),
        ]
    assert c.their_state is DONE
    assert c.our_state is SEND_RESPONSE

    # Pause pseudo-events are re-emitted each time through:
    assert c.receive_data(None) == [Paused(reason=DONE)]

    c.send(Response(status_code=200, headers=[]))
    c.send(EndOfMessage())
    assert c.their_state is DONE
    assert c.our_state is DONE

    c.prepare_to_reuse()

    events = c.receive_data(None)
    assert events == [
        Request(method="GET", target="/2",
                headers=[("Host", "a.com"), ("Content-Length", "5")]),
        Data(data=b"67890"),
        EndOfMessage(),
        Paused(reason=DONE),
    ]
    c.send(Response(status_code=200, headers=[]))
    c.send(EndOfMessage())
    c.prepare_to_reuse()

    events = c.receive_data(None)
    assert events == [
        Request(method="GET", target="/3",
                headers=[("Host", "a.com")]),
        EndOfMessage(),
        # Doesn't pause this time, no trailing data
    ]
    c.send(Response(status_code=200, headers=[]))
    c.send(EndOfMessage())

    # Arrival of more data triggers pause
    assert c.receive_data(None) == []
    assert c.receive_data(b"SADF") == [Paused(reason=DONE)]
    assert c.trailing_data == (b"SADF", False)
    assert c.receive_data(b"") == [Paused(reason=DONE)]
    assert c.trailing_data == (b"SADF", True)
    assert c.receive_data(None) == [Paused(reason=DONE)]
    assert c.receive_data(b"") == [Paused(reason=DONE)]
    # Can't call receive_data with non-empty buf after closing it
    with pytest.raises(RuntimeError):
        c.receive_data(b"FDSA")
    # Can't re-use after an error like that
    with pytest.raises(ProtocolError):
        c.prepare_to_reuse()


def test_protocol_switch():
    for (req, deny, accept) in [
            (Request(method="CONNECT", target="example.com:443",
                     headers=[("Host", "foo"), ("Content-Length", "1")]),
             Response(status_code=404, headers=[]),
             Response(status_code=200, headers=[])),

            (Request(method="GET", target="/",
                     headers=[("Host", "foo"),
                              ("Content-Length", "1"),
                              ("Upgrade", "a, b")]),
             Response(status_code=200, headers=[]),
             InformationalResponse(status_code=101,
                                   headers=[("Upgrade", "a")])),

            (Request(method="CONNECT", target="example.com:443",
                     headers=[("Host", "foo"),
                              ("Content-Length", "1"),
                              ("Upgrade", "a, b")]),
             Response(status_code=404, headers=[]),
             # Accept CONNECT, not upgrade
             Response(status_code=200, headers=[])),

            (Request(method="CONNECT", target="example.com:443",
                     headers=[("Host", "foo"),
                              ("Content-Length", "1"),
                              ("Upgrade", "a, b")]),
             Response(status_code=404, headers=[]),
             # Accept Upgrade, not CONNECT
             InformationalResponse(status_code=101,
                                   headers=[("Upgrade", "b")])),
            ]:

        def setup():
            p = ConnectionPair()
            p.send(CLIENT, req)
            # No switch-related state change stuff yet; the client has to
            # finish the request before that kicks in
            for conn in p.conns:
                assert conn.states[CLIENT] is SEND_BODY
            p.send(CLIENT,
                   [Data(data=b"1"), EndOfMessage()],
                   expect=[Data(data=b"1"),
                           EndOfMessage(),
                           Paused(reason=MIGHT_SWITCH_PROTOCOL)])
            for conn in p.conns:
                assert conn.states[CLIENT] is MIGHT_SWITCH_PROTOCOL
            assert p.conn[SERVER].receive_data(None) == [
                Paused(reason=MIGHT_SWITCH_PROTOCOL),
            ]
            return p

        # Test deny case
        p = setup()
        p.send(SERVER, deny)
        for conn in p.conns:
            assert conn.states == {CLIENT: DONE, SERVER: SEND_BODY}
        p.send(SERVER, EndOfMessage())
        # Check that re-use is still allowed after a denial
        for conn in p.conns:
            conn.prepare_to_reuse()

        # Test accept case
        p = setup()
        p.send(SERVER, accept,
               expect=[accept, Paused(reason=SWITCHED_PROTOCOL)])
        for conn in p.conns:
            assert conn.states == {CLIENT: SWITCHED_PROTOCOL,
                                   SERVER: SWITCHED_PROTOCOL}
            assert conn.receive_data(b"123") == [
                Paused(reason=SWITCHED_PROTOCOL),
            ]
            assert conn.receive_data(b"456") == [
                Paused(reason=SWITCHED_PROTOCOL),
            ]
            assert conn.trailing_data == (b"123456", False)

        # Pausing in might-switch, then recovery
        # (weird artificial case where the trailing data actually is valid
        # HTTP for some reason, because this makes it easier to test the state
        # logic)
        p = setup()
        sc = p.conn[SERVER]
        assert sc.receive_data(b"GET / HTTP/1.0\r\n\r\n") == [
            Paused(reason=MIGHT_SWITCH_PROTOCOL),
        ]
        assert sc.receive_data(None) == [
            Paused(reason=MIGHT_SWITCH_PROTOCOL),
        ]
        assert sc.trailing_data == (b"GET / HTTP/1.0\r\n\r\n", False)
        sc.send(deny)
        assert sc.receive_data(None) == [
            Paused(reason=DONE),
        ]
        sc.send(EndOfMessage())
        sc.prepare_to_reuse()
        assert sc.receive_data(None) == [
            Request(method="GET", target="/", headers=[], http_version="1.0"),
            EndOfMessage(),
        ]

        # When we're DONE, have no trailing data, and the connection gets
        # closed, we report ConnectionClosed(). When we're in might-switch or
        # switched, we don't.
        p = setup()
        sc = p.conn[SERVER]
        assert sc.receive_data(b"") == [
            Paused(reason=MIGHT_SWITCH_PROTOCOL),
        ]
        assert sc.receive_data(None) == [
            Paused(reason=MIGHT_SWITCH_PROTOCOL),
        ]
        assert sc.trailing_data == (b"", True)
        p.send(SERVER, accept,
               expect=[accept, Paused(reason=SWITCHED_PROTOCOL)])
        assert sc.receive_data(None) == [
            Paused(reason=SWITCHED_PROTOCOL),
        ]

        p = setup()
        sc = p.conn[SERVER]
        assert sc.receive_data(b"") == [
            Paused(reason=MIGHT_SWITCH_PROTOCOL),
        ]
        sc.send(deny)
        assert sc.receive_data(None) == [
            ConnectionClosed(),
        ]

        # You can't send after switching protocols, or while waiting for a
        # protocol switch
        p = setup()
        with pytest.raises(ProtocolError):
            p.conn[CLIENT].send(
                Request(method="GET", target="/", headers=[("Host", "a")]))
        p = setup()
        p.send(SERVER, accept,
               expect=[accept, Paused(reason=SWITCHED_PROTOCOL)])
        with pytest.raises(ProtocolError):
            p.conn[SERVER].send(Data(data=b"123"))


def test_close_simple():
    # Just immediately closing a new connection without anything having
    # happened yet.
    for (who_shot_first, who_shot_second) in [
            (CLIENT, SERVER),
            (SERVER, CLIENT),
            ]:
        def setup():
            p = ConnectionPair()
            p.send(who_shot_first, ConnectionClosed())
            for conn in p.conns:
                assert conn.states == {
                    who_shot_first: CLOSED,
                    who_shot_second: MUST_CLOSE,
                }
            return p
        # You can keep putting b"" into a closed connection, and you keep
        # getting ConnectionClosed() out:
        p = setup()
        assert p.conn[who_shot_second].receive_data(None) == [
            ConnectionClosed(),
        ]
        assert p.conn[who_shot_second].receive_data(b"") == [
            ConnectionClosed(),
        ]
        # Second party can close...
        p = setup()
        p.send(who_shot_second, ConnectionClosed())
        for conn in p.conns:
            assert conn.our_state is CLOSED
            assert conn.their_state is CLOSED
        # But trying to receive new data on a closed connection is a
        # RuntimeError (not ProtocolError, because the problem here isn't
        # violation of HTTP, it's violation of physics)
        p = setup()
        with pytest.raises(RuntimeError):
            p.conn[who_shot_second].receive_data(b"123")
        # And receiving new data on a MUST_CLOSE connection is a ProtocolError
        p = setup()
        with pytest.raises(ProtocolError):
            p.conn[who_shot_first].receive_data(b"GET")


def test_close_different_states():
    req = [Request(method="GET", target="/foo", headers=[("Host", "a")]),
           EndOfMessage()]
    resp = [Response(status_code=200, headers=[]), EndOfMessage()]

    # Client before request
    p = ConnectionPair()
    p.send(CLIENT, ConnectionClosed())
    for conn in p.conns:
        assert conn.states == {CLIENT: CLOSED, SERVER: MUST_CLOSE}

    # Client after request
    p = ConnectionPair()
    p.send(CLIENT, req)
    p.send(CLIENT, ConnectionClosed())
    for conn in p.conns:
        assert conn.states == {CLIENT: CLOSED, SERVER: SEND_RESPONSE}

    # Server after request -> not allowed
    p = ConnectionPair()
    p.send(CLIENT, req)
    with pytest.raises(ProtocolError):
        p.conn[SERVER].send(ConnectionClosed())
    with pytest.raises(ProtocolError):
        p.conn[CLIENT].receive_data(b"")

    # Server after response
    p = ConnectionPair()
    p.send(CLIENT, req)
    p.send(SERVER, resp)
    p.send(SERVER, ConnectionClosed())
    for conn in p.conns:
        assert conn.states == {CLIENT: MUST_CLOSE, SERVER: CLOSED}

    # Both after closing (ConnectionClosed() is idempotent)
    p = ConnectionPair()
    p.send(CLIENT, req)
    p.send(SERVER, resp)
    p.send(CLIENT, ConnectionClosed())
    p.send(SERVER, ConnectionClosed())
    p.send(CLIENT, ConnectionClosed())
    p.send(SERVER, ConnectionClosed())

    # In the middle of sending -> not allowed
    p = ConnectionPair()
    p.send(CLIENT,
           Request(method="GET", target="/",
                   headers=[("Host", "a"), ("Content-Length", "10")]))
    with pytest.raises(ProtocolError):
        p.conn[CLIENT].send(ConnectionClosed())
    with pytest.raises(ProtocolError):
        p.conn[SERVER].receive_data(b"")

# Receive several requests and then client shuts down their side of the
# connection; we can respond to each
def test_pipelined_close():
    c = Connection(SERVER)
    # 2 requests then a close
    c.receive_data(
        b"GET /1 HTTP/1.1\r\nHost: a.com\r\nContent-Length: 5\r\n\r\n"
        b"12345"
        b"GET /2 HTTP/1.1\r\nHost: a.com\r\nContent-Length: 5\r\n\r\n"
        b"67890")
    c.receive_data(b"")
    assert c.states[CLIENT] is DONE
    c.send(Response(status_code=200, headers=[]))
    c.send(EndOfMessage())
    assert c.states[SERVER] is DONE
    c.prepare_to_reuse()
    assert c.receive_data(None) == [
        Request(method="GET", target="/2",
                headers=[("host", "a.com"), ("content-length", "5")]),
        Data(data=b"67890"),
        EndOfMessage(),
        ConnectionClosed(),
    ]
    assert c.states == {CLIENT: CLOSED, SERVER: SEND_RESPONSE}
    c.send(Response(status_code=200, headers=[]))
    c.send(EndOfMessage())
    assert c.states == {CLIENT: CLOSED, SERVER: MUST_CLOSE}
    c.send(ConnectionClosed())
    assert c.states == {CLIENT: CLOSED, SERVER: CLOSED}

def test_sendfile():
    class SendfilePlaceholder:
        def __len__(self):
            return 10
    placeholder = SendfilePlaceholder()

    def setup(header, http_version):
        c = Connection(SERVER)
        c.receive_data("GET / HTTP/{}\r\nHost: a\r\n\r\n"
                       .format(http_version)
                       .encode("ascii"))
        headers = []
        if header:
            headers.append(header)
        c.send(Response(status_code=200, headers=headers))
        return c, c.send_with_data_passthrough(Data(data=placeholder))

    c, data = setup(("Content-Length", "10"), "1.1")
    assert data == [placeholder]
    # Raises an error if the connection object doesn't think we've sent
    # exactly 10 bytes
    c.send(EndOfMessage())

    _, data = setup(("Transfer-Encoding", "chunked"), "1.1")
    assert placeholder in data
    data[data.index(placeholder)] = b"x" * 10
    assert b"".join(data) == b"a\r\nxxxxxxxxxx\r\n"

    c, data = setup(None, "1.0")
    assert data == [placeholder]
    assert c.our_state is SEND_BODY

def test_errors():
    # After a receive error, you can't receive
    for role in [CLIENT, SERVER]:
        c = Connection(our_role=role)
        with pytest.raises(ProtocolError):
            c.receive_data(b"gibberish\r\n\r\n")
        # Now any attempt to receive continues to raise
        assert c.their_state is ERROR
        assert c.our_state is not ERROR
        print(c._cstate.states)
        with pytest.raises(ProtocolError):
            c.receive_data(None)
        # But we can still yell at the client for sending us gibberish
        if role is SERVER:
            assert (c.send(Response(status_code=400, headers=[]))
                    == b"HTTP/1.1 400 \r\nconnection: close\r\n\r\n")

    # After an error sending, you can no longer send
    # (This is especially important for things like content-length errors,
    # where there's complex internal state being modified)
    def conn(role):
        c = Connection(our_role=role)
        if role is SERVER:
            # Put it into the state where it *could* send a response...
            c.receive_data(b"GET / HTTP/1.0\r\n\r\n")
            assert c.our_state is SEND_RESPONSE
        return c

    for role in [CLIENT, SERVER]:
        if role is CLIENT:
            # This HTTP/1.0 request won't be detected as bad until after we go
            # through the state machine and hit the writing code
            good = Request(method="GET", target="/",
                           headers=[("Host", "example.com")])
            bad = Request(method="GET", target="/",
                          headers=[("Host", "example.com")],
                          http_version="1.0")
        elif role is SERVER:
            good = Response(status_code=200, headers=[])
            bad = Response(status_code=200, headers=[], http_version="1.0")
        # Make sure 'good' actually is good
        c = conn(role)
        c.send(good)
        assert c.our_state is not ERROR
        # Do that again, but this time sending 'bad' first
        c = conn(role)
        with pytest.raises(ProtocolError):
            c.send(bad)
        assert c.our_state is ERROR
        assert c.their_state is not ERROR
        # Now 'good' is not so good
        with pytest.raises(ProtocolError):
            c.send(good)

def test_idle_receive_nothing():
    # At one point this incorrectly raised an error
    for role in [CLIENT, SERVER]:
        c = Connection(role)
        assert c.receive_data(None) == []

def test_connection_drop():
    c = Connection(SERVER)
    assert c.receive_data(b"GET /") == []
    with pytest.raises(ProtocolError):
        c.receive_data(b"")

def test_408_request_timeout():
    # Should be able to send this spontaneously as a server without seeing
    # anything from client
    p = ConnectionPair()
    p.send(SERVER, Response(status_code=408, headers=[]))

# This used to raise IndexError
def test_empty_request():
    c = Connection(SERVER)
    with pytest.raises(ProtocolError):
        c.receive_data(b"\r\n")

# This used to raise IndexError
def test_empty_response():
    c = Connection(CLIENT)
    c.send(Request(method="GET", target="/", headers=[("Host", "a")]))
    with pytest.raises(ProtocolError):
        c.receive_data(b"\r\n")

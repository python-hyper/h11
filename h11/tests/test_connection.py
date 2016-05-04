import pytest

from ..util import ProtocolError
from ..events import *
from ..state import *
from ..connection import (
    _keep_alive, _response_allows_body,
    _switched_protocol, _client_requests_protocol_switch,
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


def test__response_allows_body():
    assert not _response_allows_body(
        b"GET", InformationalResponse(status_code=100, headers=[]))
    assert not _response_allows_body(
        b"GET", Response(status_code=204, headers=[]))
    assert not _response_allows_body(
        b"GET", Response(status_code=304, headers=[]))
    assert not _response_allows_body(
        b"GET", Response(status_code=304, headers=[]))
    assert _response_allows_body(
        b"GET", Response(status_code=200,
                         headers=[("Content-Length", "100")]))
    assert not _response_allows_body(
        b"HEAD", Response(status_code=200,
                          headers=[("Content-Length", "100")]))
    assert _response_allows_body(
        b"CONNECT", Response(status_code=400,
                             headers=[("Content-Length", "100")]))
    assert not _response_allows_body(
        b"CONNECT", Response(status_code=200,
                             headers=[("Content-Length", "100")]))

def test__switched_protocol():
    assert not _switched_protocol(
        b"GET", Response(status_code=200, headers=[]))
    assert _switched_protocol(
        b"CONNECT", Response(status_code=200, headers=[]))
    assert not _switched_protocol(
        b"CONNECT", Response(status_code=400, headers=[]))
    assert not _switched_protocol(
        b"GET", InformationalResponse(status_code=100, headers=[]))
    assert _switched_protocol(
        b"GET", InformationalResponse(status_code=101, headers=[]))

    assert not _switched_protocol(
        None, Request(method="GET", target="/", headers=[("Host", "a")]))
    assert not _switched_protocol(
        b"CONNECT", Data(data=b""))

def test__client_requests_protocol_switch():
    assert _client_requests_protocol_switch(
        Request(method="CONNECT",
                target="example.com:443",
                headers=[("Host", "example.com")]))
    assert not _client_requests_protocol_switch(
        Request(method="Get",
                target="/websocket",
                headers=[("Host", "example.com")]))
    assert _client_requests_protocol_switch(
        Request(method="Get",
                target="/websocket",
                headers=[("Host", "example.com"),
                         ("Upgrade", "websocket")]))


def test_Connection_basics():
    p = ConnectionPair()
    assert p.conn[CLIENT].our_role is CLIENT
    assert p.conn[CLIENT].their_role is SERVER
    assert p.conn[SERVER].our_role is SERVER
    assert p.conn[SERVER].their_role is CLIENT

    data, _ = p.send(CLIENT,
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
        assert conn.can_reuse == "maybe-later"
    assert p.conn[CLIENT].our_state is SEND_BODY
    assert p.conn[CLIENT].their_state is SEND_RESPONSE
    assert p.conn[SERVER].our_state is SEND_RESPONSE
    assert p.conn[SERVER].their_state is SEND_BODY

    assert p.conn[CLIENT].their_http_version is None
    assert p.conn[SERVER].their_http_version == b"1.1"

    data, _ = p.send(SERVER,
                     Response(status_code=200,
                              headers=[("Content-Length", "11")]))
    assert data == b"HTTP/1.1 200 \r\ncontent-length: 11\r\n\r\n"

    assert p.conn[CLIENT].their_http_version == b"1.1"
    assert p.conn[SERVER].their_http_version == b"1.1"

# HTTP/1.0
# 100-continue
# reuse
# pipelining
# response header munging
# - including HTTP/1.1 versus HTTP/1.0
# protocol switching
# close handling
# sendfile silliness

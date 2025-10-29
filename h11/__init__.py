# A highish-level implementation of the HTTP/1.1 wire protocol (RFC 7230),
# containing no networking code at all, loosely modelled on hyper-h2's generic
# implementation of HTTP/2 (and in particular the h2.connection.H2Connection
# class). There's still a bunch of subtle details you need to get right if you
# want to make this actually useful, because it doesn't implement all the
# semantics to check that what you're asking to write to the wire is sensible,
# but at least it gets you out of dealing with the wire itself.

# ruff: noqa: I001

from h11._connection import NEED_DATA, PAUSED, Connection
from h11._events import (
    ConnectionClosed as ConnectionClosed,
    Data as Data,
    EndOfMessage as EndOfMessage,
    Event as Event,
    InformationalResponse as InformationalResponse,
    Request as Request,
    Response as Response,
)
from h11._state import (
    CLIENT as CLIENT,
    CLOSED as CLOSED,
    DONE as DONE,
    ERROR as ERROR,
    IDLE as IDLE,
    MIGHT_SWITCH_PROTOCOL as MIGHT_SWITCH_PROTOCOL,
    MUST_CLOSE as MUST_CLOSE,
    SEND_BODY as SEND_BODY,
    SEND_RESPONSE as SEND_RESPONSE,
    SERVER as SERVER,
    SWITCHED_PROTOCOL as SWITCHED_PROTOCOL,
)
from h11._util import LocalProtocolError, ProtocolError, RemoteProtocolError
from h11._version import __version__

PRODUCT_ID = "python-h11/" + __version__


__all__ = (
    "CLIENT",
    "CLOSED",
    "DONE",
    "ERROR",
    "IDLE",
    "MIGHT_SWITCH_PROTOCOL",
    "MUST_CLOSE",
    "NEED_DATA",
    "PAUSED",
    "SEND_BODY",
    "SEND_RESPONSE",
    "SERVER",
    "SWITCHED_PROTOCOL",
    "Connection",
    "ConnectionClosed",
    "Data",
    "EndOfMessage",
    "Event",
    "InformationalResponse",
    "LocalProtocolError",
    "ProtocolError",
    "RemoteProtocolError",
    "Request",
    "Response",
)

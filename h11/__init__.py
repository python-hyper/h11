# A highish-level implementation of the HTTP/1.1 protocol, containing no
# networking code at all, loosely modelled on hyper-h2's generic
# implementation of HTTP/2 (and in particular the h2.connection.H2Connection
# class). There's still a bunch of subtle details you need to get right if you
# want to make this actually useful, because it doesn't implement all the
# semantics to check that what you're asking to write to the wire is sensible,
# but at least it gets you out of dealing with the wire itself.
#
# This is all based on node.js's libhttp_parser code for the core HTTP
# parsing, which is wrapped in _libhttp_parser.pyx

from .util import ProtocolError
from .events import *
from .connection import *

__all__ = ["ProtocolError", "Connection",
           # Event types
           "Request", "InformationalResponse", "Response",
           "Data", "EndOfMessage", "ConnectionClosed",
           # Party states
           "IDLE", "SEND_RESPONSE", "SEND_BODY", "DONE", "CLOSED"]

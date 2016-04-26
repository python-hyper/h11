# A high-level wrapper around _libhttp_parser.LowlevelHttpParser,
# which takes in bytes and emits h11.events objects.

from .events import *
# Note: in case we ever replace libhttp_parser with something else, we should
# ensure that our "something else" enforces an anti-DoS size limit on
# header size (like libhttp_parser does).
from ._libhttp_parser import LowlevelHttpParser, HttpParseError

__all__ = ["HttpParser", "HttpParseError"]

# This next set of functions is designed to process low-level events from
# libhttp_parser, and transduce them (in the "finite state transducer" sense)
# into the high-level HTTP events above. Except instead of an explicit finite
# state machine, our state is tracked through our position in these
# coroutines. (This is a very simple and linear FSM that requires a
# sub-machine for headers, so it's clearer and easier to write it as code with
# a subroutine than as an explicit FSM.)
#
# Our coroutine protocol:
# - We can yield None, in which case we'll get sent back the next event
# - We can yield an outgoing event ("emit"), in which case we'll immediately
#   get sent back None so we can continue
#   (these two rules are implemented by the driver routine,
#   HttpParser._transduce)
# - 'event' always refers to the next event to process (basically a lookahead
#   of 1); subroutines get it passed in and then pass it out.
#
# When reading this code you'll want to refer to the _http_parser.pyx file to
# see how the low-level events are formatted, but note that they are always
# (type, [payload]), and payload (if present) is always a bytestring or a
# dict.

def require_event_is(event_type, event):
    if event[0] != event_type:
        raise ValueError("expected event of type {}, not {}"
                         .format(event_type, event[0]))

# Collect a sequence of events like
#   ("url-data", b"/ind")
#   ("url-data", b"ex.h")
#   ("url-data", b"tml")
# into a single bytestring b"/index.html", stopping when we see a different
# type of event.
def collect_data(event_type, event):
    # must see at least one event of the appropriate type or it fails
    require_event_is(event_type, event)
    data = bytearray()
    while event[0] == event_type:
        data += event[-1]
        event = (yield)
    # returns data + next event
    return bytes(data), event

def decode_headers(event):
    headers = []
    # headers are optional, so it's possible to return early with no headers
    while event[0] == "header-field-data":
        field, event = yield from collect_data("header-field-data", event)
        value, event = yield from collect_data("header-value-data", event)
        headers.append((field, value))
    return headers, event

# Main loop for the libhttp_parser low-level -> high level event transduction
# machinery
def http_transducer(*, client_side):
    while True:
        # -- begin --
        event = (yield)
        require_event_is("message-begin", event)
        # -- read status line and headers --
        event = (yield)
        if not client_side:
            url, event = yield from collect_data("url-data", event)
        headers, event = yield from decode_headers(event)
        require_event_is("headers-complete", event)
        _, headers_complete_info = event
        if client_side:
            status_code = headers_complete_info["status_code"]
            if 100 <= status_code < 200:
                class_ = InformationalResponse
            else:
                class_ = Response
            yield class_(headers=headers, **headers_complete_info)
        else:
            yield Request(headers=headers, url=url, **headers_complete_info)
        del headers, headers_complete_info
        # -- read body --
        event = (yield)
        while event[0] == "body-data":
            yield Data(data=event[-1])
        # -- trailing headers (optional) --
        if event[0] == "header-field-data":
            trailing_headers, event = yield from decode_headers(event)
        else:
            trailing_headers = []
        # -- end-of-message --
        require_event_is("message-complete", event)
        yield EndOfMessage(headers=trailing_headers, **event[-1])
        # -- loop around for the next message --

# The wrapper that uses all that stuff above
class HttpParser:
    def __init__(self, *, client_side):
        self._lowlevel_parser = LowlevelHttpParser(client_side=client_side)
        self._transducer = http_transducer(client_side=client_side)
        # Prime the coroutine -- execute until the first yield.  This is
        # needed because we can't call .send to target the first yield until
        # after we've reached that yield -- .send on a newly-initialized
        # coroutine is an error.
        next(self._transducer)

    def _transduce(self, in_event):
        out_events = []
        result = self._transducer.send(in_event)
        while result is not None:
            out_events.append(result)
            result = next(self._transducer)
        return out_events

    def receive_data(self, data):
        # May throw HttpParseError.

        # Lowlevel parser treats b"" as indicating EOF, so we have to convert
        # None sentinel to this, while screening out literal b"".
        if data is None:
            lowlevel_data = b""
        elif data:
            lowlevel_data = data
        else:
            # data is an empty bytes-like, nothing to do
            assert not data
            return []
        self._lowlevel_parser.feed(lowlevel_data)
        out_events = []
        for event in self._lowlevel_parser.events:
            out_events += self._transduce(event)
        self._lowlevel_parser.events.clear()
        return out_events

    def set_request_method(self, method):
        self._lowlevel_parser.set_request_method(method)

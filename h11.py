# A highish-level implementation of the HTTP/1.1 protocol, containing no
# networking code at all, loosely modelled on hyper-h2's generic
# implementation of HTTP/2 (and in particular the h2.connection.H2Connection
# class). There's still a bunch of subtle details you need to get right if you
# want to make this actually useful, because it doesn't implement all the
# semantics to check that what you're asking to write to the wire is sensible,
# but at least it gets you out of dealing with the wire itself.
#
# This is all based on the node.js-associated libhttp_parser code for the core
# HTTP parsing, which is wrapped in _libhttp_parser.pyx

# Note: in case we ever replace libhttp_parser with something else, we should
# ensure that our "something else" enforces an anti-DoS size limit on
# header size (like libhttp_parser does).
from _libhttp_parser import LowlevelHttpParser, HttpParseError

__all__ = [
    "H11Connection",
    "HttpParseError",
    "RequestReceived",
    "ResponseReceived",
    "InformationalResponseReceived",
    "DataReceived",
    "TrailersReceived",
    "EndOfMessageReceived",
]

################################################################
#
# High level events that we emit as our external interface for reading HTTP
# streams, somewhat modelled on the corresponding events in hyper-h2:
#   http://python-hyper.org/h2/en/stable/api.html#events
#
################################################################

class RequestReceived:
    def __init__(self, http_version, method, path, headers, keep_alive):
        self.http_version = http_version
        self.method = method
        self.path = path
        self.headers = headers
        self.keep_alive = keep_alive

class _ResponseReceivedBase:
    def __init__(self, http_version, status_code, path, headers, keep_alive):
        self.http_version = http_version
        self.status_code = status_code
        self.path = path
        self.headers = headers
        self.keep_alive = keep_alive

class ResponseReceived(_ResponseReceivedBase):
    pass

class InformationalResponseReceived(_ResponseReceivedBase):
    pass

class DataReceived:
    def __init__(self, data):
        self.data = data

class TrailersReceived:
    def __init__(self, headers):
        self.headers = headers

class EndOfMessageReceived:
    def __init__(self, keep_alive, upgrade, trailing_data=None):
        self.keep_alive = keep_alive
        self.upgrade = upgrade
        self.trailing_data = trailing_data

################################################################
#
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
#
################################################################

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
    # headers are optional, so we can return early with no headers
    while event[0] == "header-field-data":
        field, event = yield from collect_data("header-field-data", event)
        value, event = yield from collect_data("header-value-data", event)
        headers.append((field, value))
    return headers, event

# Helper for building the high-level event triggered by the low-level
# headers-complete event.
def headers_complete_event(client_side, url, headers, headers_complete_info):
    http_version = ("{http_major}.{http_minor}"
                    .format(**headers_complete_info))
    if client_side:
        status_code = headers_complete_info["status_code"]
        if 100 <= status_code < 200:
            class_ = InformationalResponseReceived
        else:
            class_ = ResponseReceived
        return class_(
            http_version,
            status_code,
            url,
            headers,
            bool(headers_complete_info["keep_alive"]))
    else:
        return RequestReceived(
            http_version,
            headers_complete_info["method"],
            url,
            headers,
            bool(headers_complete_info["keep_alive"]))

# Main loop for the libhttp_parser low-level -> high level event transduction
# machinery
def http_transducer(*, client_side):
    while True:
        event = (yield)
        require_event_is("message-begin", event)
        event = (yield)
        url, event = yield from collect_data("url-data", event)
        headers, event = yield from decode_headers(event)
        require_event_is("headers-complete", event)
        _, headers_complete_info = event
        yield headers_complete_event(
            client_side, url, headers, headers_complete_info)
        del headers, headers_complete_info
        event = (yield)
        while event[0] == "body-data":
            yield DataReceived(event[-1])
        # body is followed by optional trailing headers
        if event[0] == "header-field-data":
            trailing_headers, event = yield from decode_headers(event)
            yield TrailersReceived(trailing_headers)
            del trailing_headers
        # and then end-of-message (which might be an upgrade)
        require_event_is("message-complete", event)
        yield EndOfMessageReceived(**event[-1])
        # fall through and loop around for the next message

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
        # treats b"" as indicating EOF
        # may throw HttpParseError
        self._lowlevel_parser.feed(data)
        out_events = []
        for event in self._lowlevel_parser.events:
            out_events += self._transduce(event)
        self._lowlevel_parser.events.clear()
        return out_events

    def set_method(self, method):
        self._lowlevel_parser.method = method


# Higher level stuff:
# - Connection handling
#   inserting Connection: close at right place, and respecting it.
# - Timeouts: waiting for 100-continue, killing idle keepalive connections,
#     killing idle connections in general
# - Expect:
#     https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7231.html#rfc.section.5.1.1
#   This is tightly integrated with flow control, not a lot we can do, except
#   maybe provide a method to be called before blocking waiting for the
#   request body?
# - Sending an error when things go wrong (esp. 400 Bad Request)
# - Tracking edge of each request/response pair across the same connection
#
# - Transfer-Encoding: compress, gzip
#   - but unfortunately, libhttp_parser doesn't support these at all (just
#     ignores the Transfer-Encoding field and doesn't even do chunked parsing,
#     so totally unfixable)
#       https://stackapps.com/questions/916/why-content-encoding-gzip-rather-than-transfer-encoding-gzip
#     So... this sucks, but I guess we don't support it either.

# Can a http client legally wait to send whole body before reading response?
# -- yes, in fact most browsers do
#      https://stackoverflow.com/questions/18367824/how-to-cancel-http-upload-from-data-events/18370751#18370751
#      https://bugs.chromium.org/p/chromium/issues/detail?id=174906
#      https://bugzilla.mozilla.org/show_bug.cgi?id=839078
#    however, the server is allowed to send a response and then close the
#    connection if they don't like it
#    maybe we should just require the whole body to be read before sending a
#      response, to simplify the state machine?
#    and likewise on the client?

# XX there's no content-length then ideally the server should remember the
# client's HTTP version, and automatically choose between chunked and
# close-on-complete depending on whether it's HTTP/1.0 or HTTP/1.1.

# hmm, apparently upgrade is recoverable from -- the server can just be like
# "nope, not doing that", see
#   https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#header.upgrade
#
# okay, so rules for upgrade are now:
# - when you get back an message-complete, you have to check for the upgrade
#   flag
# - if it's set, then there's also some trailing-data provided
# - if you continue doing HTTP on the same socket, then you have to

# server-side state machine:
# WAIT -> got request line + headers          ->            read body
#                             -> waiting for 100-continue ->
#                             -> response sent
#                                            -> finish response
#     need response sent + read body before can process the next request

# client-side state machine:
# send request line + headers
#    (-> wait for 100-continue ->)
#          send body
#    read response
# can't send new request until after have read response (crucial for current
# method tracking! and pipelining is not important to support)

# and NB 1xx responses don't count as having read the response (though libhttp_parser will
# parse them as a complete message-begin ... message-complete cycle)
# in fact it seems to have a bug where it doesn't know that 100 responses can't
# contain a body... probably harmless but eh.

# also note that client is allowed to just go ahead sending body even after
# saying Expect: 100-continue and seeing no response
# this is needed to handle ancient servers that don't know about Expect:
# 100-continue (maybe they don't exist anymore?)
# in fact even if gets a 4xx response still has to "send the rest" -- if using
# chunked it could send 0 bytes (but a common 4xx response is "your
# content-length is too big", so maybe you want to use content-length!), or
# could just close the connection (or server might just close the connection),
# but these are your options.
# - seeing a response when in wait-for-continue state just moves you to
# send-body state

# Connection shutdown is tricky. Quoth RFC 7230:
#
# "If a server performs an immediate close of a TCP connection, there is a
# significant risk that the client will not be able to read the last HTTP
# response. If the server receives additional data from the client on a fully
# closed connection, such as another request that was sent by the client
# before receiving the server's response, the server's TCP stack will send a
# reset packet to the client; unfortunately, the reset packet might erase the
# client's unacknowledged input buffers before they can be read and
# interpreted by the client's HTTP parser.
#
# "To avoid the TCP reset problem, servers typically close a connection in
# stages. First, the server performs a half-close by closing only the write
# side of the read/write connection. The server then continues to read from
# the connection until it receives a corresponding close by the client, or
# until the server is reasonably certain that its own TCP stack has received
# the client's acknowledgement of the packet(s) containing the server's last
# response. Finally, the server fully closes the connection."
#
# So this needs shutdown(2)

# used for methods, paths, and headers
def _asciify(s):
    if isinstance(s, str):
        s = s.encode("ascii")
    return s

class H11Connection:
    def __init__(self, *, client_side):
        self._client_side = client_side
        self._parser = HttpParser(client_side=client_side)
        self._data_to_send = bytearray()
        self._transfer_encoding = None

    def receive_data(self, data):
        # Lower-level parser treats b"" as indicating EOF.
        # Our exported interface separates EOF out to a different method, so
        # protect the low-level interface from b"":
        if data:
            return self._parser.receive_data(data)
        else:
            return []

    def receive_eof(self):
        return self._parser.receive_data(b"")

    def data_to_send(self, amt=None):
        if amt is None:
            amt = len(self._data_to_send)
        data = self._data_to_send[:amt]
        self._data_to_send = self._data_to_send[amt:]
        return data

    def _send(self, data):
        self._data_to_send += data

    # use None or None, None for sending trailing headers
    # You call send_client_headers:
    #   exactly once with initial request
    #   if using chunked encoding, exactly once with method = path = None for
    #     trailer headers
    def send_client_headers(self, method, path, headers):
        if not self._client_side:
            raise ValueError(
                "send_client_headers called on a server connection")
        is_trailer = (method is path is None)
        if not is_trailer:
            method = _asciify(method)
            path = _asciify(path)
            if b" " in path:
                raise ValueError(
                    "HTTP path {!r} is invalid -- paths cannot contain spaces "
                    "(see RFC 7230 sec. 3.1.1)"
                    .format(path))
            self._send(b"%s %s HTTP/1.1\r\n" % (method, path))
            self._parser.set_method(method)
            self._sniff_transfer_encoding(headers)
        self._send_headers(is_trailer, headers)

    # You call send_server_headers:
    #   0 or more times with status 1xx
    #   exactly once with any other status
    #   if using chunked encoding, exactly once with status = None for trailer
    #     headers
    def send_server_headers(self, status, headers):
        if self._client_side:
            raise ValueError(
                "send_server_headers called on a client connection")
        is_trailer = (status is None)
        if not is_trailer:
            self._send(b"HTTP/1.1 %s \r\n" % (str(status).encode("ascii"),))
            if status >= 200:
                self._sniff_transfer_encoding(headers)
        self._send_headers(is_trailer, headers)

    def _sniff_transfer_encoding(self, headers):
        assert self._transfer_encoding is None
        found = 0
        for field, value in headers:
            field = field.lower()
            if field == b"content-length":
                self._transfer_encoding = "raw"
                found += 1
            if field == b"transfer-encoding":
                value = value.lower()
                if value != "chunked":
                    raise ValueError(
                        "only chunked transfer-encoding is supported")
                self._transfer_encoding = value
                found += 1
        if found == 0:
            # No transfer-encoding -- either this is a no-body message
            # (e.g. CONNECT or 204 No Content), or we need to close the
            # connection when we're done.
            self._transfer_encoding = "raw"
        elif found > 1:
            raise ValueError("multiple framing headers found")

    def _send_headers(self, is_trailer, headers):
        if is_trailer:
            if self._transfer_encoding != "chunked":
                raise ValueError(
                    "must send trailer iff using chunked encoding")
            # the trailers are used to mark the end of the body
            self._send(b"0\r\n")
        # headers is [(field, value), (field, value), ...]
        for field, value in headers:
            self._send(b"%s: %s\r\n" % (_asciify(field), _asciify(value)))
        self._send(b"\r\n")

    def send_data(self, data):
        if not data:
            return
        if self._transfer_encoding == "raw":
            self._send(data)
        elif self._transfer_encoding == "chunked":
            # important not to accidentally write a 0 here, that would end the
            # body.
            self._send(b"{:x}\r\n" % (len(data),))
            self._send(data)
            self._send(b"\r\n")
        else:
            raise RuntimeError("transfer encoding not set")

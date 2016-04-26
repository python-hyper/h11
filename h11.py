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

import collections

# Note: in case we ever replace libhttp_parser with something else, we should
# ensure that our "something else" enforces an anti-DoS size limit on
# header size (like libhttp_parser does).
from _libhttp_parser import LowlevelHttpParser, HttpParseError

__all__ = [
    "H11Connection",
    # pass this to receive_data() to indicate socket close
    "CloseSocket",
    "HttpParseError",
    "Request",
    "Response",
    "InformationalResponse",
    "Data",
    "EndOfMessage",
]

################################################################
#
# High level events that we emit as our external interface for reading HTTP
# streams, somewhat modelled on the corresponding events in hyper-h2:
#   http://python-hyper.org/h2/en/stable/api.html#events
#
# Main difference is that I use the same objects for sending, so have dropped
#the Receive prefix.
#
################################################################

# used for methods, urls, and headers
def _asciify(s):
    if isinstance(s, str):
        s = s.encode("ascii")
    return s

def _asciify_headers(headers):
    return [(_asciify(f), _asciify(v)) for (f, v) in headers]

class _EventBundle:
    _required = []
    _optional = []

    def __init__(self, **kwargs):
        allowed = set(self._required + self._optional)
        for kwarg in kwargs:
            if kwarg not in allowed:
                raise TypeError(
                    "unrecognized kwarg {} for {}"
                    .format(kwarg, self.__class__.__name__))
        for field in self._required:
            if field not in kwargs:
                raise TypeError(
                    "missing required kwarg {} for {}"
                    .format(kwarg, self.__class__.__name__))
        self.__dict__.update(kwargs)

        if "headers" in self.__dict__:
            self.headers = _asciify_headers(self.headers)
        for field in ["method", "client_method", "url"]:
            if field in self.__dict__:
                self.__dict__[field] = _asciify(self.__dict__[field])

    def __repr__(self):
        name = self.__class__.__name__
        kwarg_strs = ["{}={}".format(field, self.__dict__[field])
                      for field in self._fields]
        kwarg_str = ", ".join(kwarg_strs)
        return "{}({})".format(name, kwarg_str)

    # Useful for tests
    def __eq__(self, other):
        return (self.__class__ == other.__class__
                and self.__dict__ == other.__dict__)

class Request(_EventBundle):
    _required = ["method", "url", "headers"]
    _optional = ["http_version", "keep_alive"]

class _ResponseBase(_EventBundle):
    _required = ["status_code", "headers"]
    _optional = ["http_version", "request_method", "keep_alive"]

class Response(_ResponseBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not (200 <= self.status_code):
            raise ValueError(
                "Response status_code should be >= 200, but got {}"
                .format(self.status_code))

class InformationalResponse(_ResponseBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not (100 <= self.status_code < 200):
            raise ValueError(
                "InformationalResponse status_code should be in range "
                "[200, 300), but got {}"
                .format(self.status_code))

class Data(_EventBundle):
    _required = ["data"]

# XX FIXME: "A recipient MUST ignore (or consider as an error) any fields that
# are forbidden to be sent in a trailer, since processing them as if they were
# present in the header section might bypass external security filters."
# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#chunked.trailer.part
class EndOfMessage:
    _optional = ["headers", "keep_alive", "upgrade", "trailing_data"]

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
        # may throw HttpParseError
        # lowlevel parser treats b"" as indicating EOF, so we have to convert
        # CloseSocket sentinel to this, while screening out literal b""
        if data is CloseSocket:
            lowlevel_data = b""
        elif data:
            lowlevel_data = data
        else:
            return []
        self._lowlevel_parser.feed(lowlevel_data)
        out_events = []
        for event in self._lowlevel_parser.events:
            out_events += self._transduce(event)
        self._lowlevel_parser.events.clear()
        return out_events

    def set_request_method(self, method):
        self._lowlevel_parser.set_request_method(method)


# Higher level stuff:
# - Timeouts: waiting for 100-continue, killing idle keepalive connections,
#     killing idle connections in general
#     basically just need a timeout when we block on read, and if it times out
#       then we close. should be settable in the APIs that block on read
#       (e.g. iterating over body).
# - Expect:
#     https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7231.html#rfc.section.5.1.1
#   This is tightly integrated with flow control, not a lot we can do, except
#   maybe provide a method to be called before blocking waiting for the
#   request body?
# - Sending an error when things go wrong (esp. 400 Bad Request)
#
# - Transfer-Encoding: compress, gzip
#   - but unfortunately, libhttp_parser doesn't support these at all (just
#     ignores the Transfer-Encoding field and doesn't even do chunked parsing,
#     so totally unfixable)
#       https://stackapps.com/questions/916/why-content-encoding-gzip-rather-than-transfer-encoding-gzip
#     So... this sucks, but I guess we don't support it either.

# rules for upgrade are:
# - when you get back an message-complete, you have to check for the upgrade
#   flag
# - if it's set, then there's also some trailing-data provided
# - if you continue doing HTTP on the same socket, then you have to
#   receive_data that trailing data again
# maybe we should make this an opt-in thing in the constructor -- you have to
# say whether you're prepared for upgrade handling?
#
# also, after sending a message-complete on the server you then have to
# immediately call receive_data even if there's no new bytes to pass, because
# more responses might have been pipelined up.

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
#
# and NB 1xx responses don't count as having read the response (though
# libhttp_parser will parse them as a complete message-begin
# ... message-complete cycle) in fact it seems to have a bug where it doesn't
# know that 100 responses can't contain a body... probably harmless but eh.
#
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

def _get_header_values(wanted_field, headers, *, split_comma, lower):
    wanted_field = _asciify(wanted_field).lower()
    values = []
    for field, value in headers:
        field = field.lower()
        if wanted_field == field:
            if split_comma:
                values.extend(v.strip() for v in value.split(b","))
            else:
                values.append(value)
    if lower:
        values = [v.lower() for v in values]
    return values

def _framing_headers(headers):
    content_lengths = _get_header_values("content-length", event.headers,
                                         split_comma=False, lower=False)
    transfer_encodings = _get_header_values("transfer-encoding", event.headers,
                                            split_comma=True, lower=True)
    if len(content_lengths) + len(transfer_encodings) > 1:
        raise ValueError(
            "I need at most one Content-Length and/or Transfer-Encoding header")
    for transfer_encoding in transfer_encodings:
        if transfer_encoding != b"chunked":
            raise ValueError(
                "I only know how to handle Transfer-Encoding: chunked")
    content_length = None
    if content_lengths:
        content_length = int(content_lengths[0])
    transfer_encoding = None
    if transfer_encodings:
        transfer_encoding = transfer_encodings[0]
        assert transfer_encoding == "chunked"
    return (content_length, transfer_encoding)

def _connection_close_is_set(headers):
    connection_headers = _get_header_values("Connection", headers,
                                            split_comma=True, lower=True)
    return b"close" in connection_headers

def _strip_transfer_encoding(headers):
    new_headers = []
    for header in headers:
        if header.lower() != b"transfer-encoding":
            new_headers.append(header)
    headers[:] = new_headers

class CloseSocketType:
    def __repr__(self):
        return "CloseSocket"

CloseSocket = CloseSocketType()

class NoBodyFramer:
    def send_data(self, data, connection):
        raise RuntimeError("no body allowed for this message")

    def send_eom(self, headers, connection):
        if headers:
            raise RuntimeError("can't send trailers on a body-less message")

class ContentLengthFramer:
    def __init__(self, length):
        self._length = length
        self._sent = 0

    def send_data(self, data, connection):
        self._sent += len(data)
        return data

    def send_eom(self, headers, connection):
        if self._sent != self._length:
            raise RuntimeError(
                "declared Content-Length doesn't match actual body length")
        if headers:
            raise RuntimeError("can't send trailers if using Content-Length")

class ChunkedFramer:
    def send_data(self, data, connection):
        # can't
        if data:
            connection._send(b"%x\r\n%s\r\n" % (len(data), data))

    def send_eom(self, headers, connection):
        connection._send(b"0\r\n")
        connection._send_headers(headers)

class HTTP10Framer:
    def send_data(self, data, connection):
        connection._send(data)

    def send_eom(self, headers, connection):
        if headers:
            raise RuntimeError("can't send trailers to HTTP/1.0 client")
        # no need to close the socket ourselves, that will be taken care of by
        # Connection: close machinery

def _examine_and_fix_framing_headers(self, event, response_to=None):
    # If the client requested that we close this, then do so.
    if response_to is not None and not response_to.keep_alive:
        if not _connection_close_is_set(event.headers):
            event.headers.append((b"Connection", b"close"))

    # The remainder is all the logic for picking the body encoder. It returns
    # the body encoder to use. It might also modify the headers (e.g. to
    # add/remove Transfer-Encoding or Connection: close).
    #
    #
    # Reference:
    #   https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#message.body.length

    # Some things never have a body:
    if (isinstance(event, InformationalResponse)
        or (isinstance(event, Response)
            and (event.status_code in (204, 304)
                 or response_to.method == b"HEAD"
                 or (response_to.method == b"CONNECT"
                     and 200 <= event.status_code < 300)))):
        return NoBodyFramer()

    # Otherwise, we have a body, so we have to figure out how we're
    # framing it.
    #
    # Let's see what the user gave us to work with:
    (content_length, transfer_encoding) = _framing_headers(event.headers)

    # Basically there are two cases: we know the length, or we don't.
    #
    # If we know it, great, life is easy.
    if content_length is not None:
        return ContentLengthFramer(int(content_lengths[0]))

    # Otherwise -- Transfer-Encoding: chunked is set or there's no framing
    # headers at all -- we don't know it, and we handle all such situations
    # identically.
    #
    # If we're a client, then we just set Transfer-Encoding: chunked and hope
    # for the best. This will only work with HTTP/1.1 servers, but almost all
    # servers now qualify, and if we have a HTTP/1.0 server then we can't send
    # a variable length body at all. (If you wanted to send no body then you
    # should have said Content-Length: 0.)
    if isinstance(event, Request):
        if transfer_encoding is None:
            event.headers.append((b"Transfer-Encoding", b"chunked"))
        return ChunkedFramer()
    # If we're a server, then we should use chunked IFF we are talking to
    # a HTTP/1.1 client, and otherwise use the HTTP/1.0 "send and then
    # close" framing. In the latter case we also have to set the
    # Connection: close header.
    else:
        # NB InformationalResponse got handled above, so Response is the only
        # possibility here.
        assert isinstance(event, Response)
        if response_to.http_version < (1, 1):
            if not _connection_close_is_set(event.headers):
                event.headers.append(b"Connection", b"close")
            if transfer_encoding is not None:
                _strip_transfer_encoding(event.headers)
            return HTTP10Framer()
        else:
            if transfer_encoding is None:
                event.headers.append((b"Transfer-Encoding", b"chunked"))
            return ChunkedFramer()

    assert False

class H11Connection:
    def __init__(self, *, client_side):
        self._client_side = client_side
        self._parser = HttpParser(client_side=client_side)
        # Data that should be sent when possible
        self._data_to_send = bytearray()
        # Holds the encoder we should use for sending body data:
        self._body_framer = None
        #
        # The rest are only relevant in server mode:
        #
        # Whether the socket should be closed after we finish sending the data
        # currently in self._data_to_send.
        self._send_close = False
        # Minimal pipelining support: as a HTTP/1.1 server we are required to
        # handle receiving multiple requests in a single package / call to
        # recieve_data. But to avoid state machine problems, we refuse to
        # actually process and return any new Requests to the application
        # until after they have sent a Response. This queue is where we hold
        # things in the mean time.
        self._receive_event_buffer = collections.deque()
        # Holds the last request received. Gets set back to None after we send
        # a response, which uncorks the receive event buffer queue.
        self._last_request_received = None
        # Records whether we sent a Connection: close header.
        self._close_after_message = False

    def receive_data(self, data):
        "data is either a bytes-like or SocketClose"
        self._receive_event_buffer.extend(self._parser.receive_data(data))
        events = []
        while self._receive_event_buffer:
            event = self._receive_event_buffer.popleft()
            if isinstance(event, Request):
                if self._last_request_received is not None:
                    # we are still processing the previous request, so put
                    # this one back and leave it there for now
                    self._receive_event_buffer.appendleft(event)
                    break
                else:
                    self._last_request_received = event
                    # fall through and process normally
            events.append(event)
        return event

    def data_to_send(self, amt=None):
        "returns (bytes-like, close-after-sending)"
        if amt is None:
            amt = len(self._data_to_send)
        data = self._data_to_send[:amt]
        self._data_to_send = self._data_to_send[amt:]
        if self._data_to_send:
            return (data, False)
        else:
            return (data, self._send_close)

    def _send(self, data):
        if data = CloseSocket:
            self._send_close = True
        else:
            if self._send_close:
                raise RuntimeError("tried to send data after socket close")
            else:
                self._data_to_send += data

    def _send_headers(self, headers):
        for field, value in headers:
            self._send(b"%s: %s\r\n" % (field, value))
        self._send(b"\r\n")

    def send(self, event):
        if isinstance(event, Request):
            if not self._client_side:
                raise ValueError("only clients can send requests")
            if b" " in event.url:
                raise ValueError(
                    "HTTP url {!r} is invalid -- urls cannot contain spaces "
                    "(see RFC 7230 sec. 3.1.1)"
                    .format(path))
            self._send(b"%s %s HTTP/1.1\r\n" % (event.method, event.url))
            # Tell the parser what request method we've just used, so that it
            # knows how to parse the response (needed for e.g. HEAD & CONNECT)
            self._parser.set_method(event.method)
            self._body_framer = self._examine_and_fix_framing_headers(event)
            self._send_headers(event.headers)

        elif isinstance(event, _ResponseBase):
            if self._client_side:
                raise ValueError("only servers can send responses")
            status_bytes = str(event.status).encode("ascii")
            # We don't bother sending ascii status messages like "OK"; they're
            # optional anyway. (But the space after the numeric status code is
            # mandatory.)
            self._send(b"HTTP/1.1 %s \r\n" % (status_bytes,))
            self._body_framer = self._examine_and_fix_framing_headers(
                event, response_to=self._last_request_received)
            if _connection_close_is_set(event.headers):
                self._close_after_message
            self._send_headers(event.headers)

        elif isinstance(event, Data):
            self._body_framer.send_data(event.data, self)

        elif isinstance(event, EndOfMessage):
            self._body_framer.send_eom(event, self)
            self._body_framer = None
            self._last_request_received = None
            if self._close_after_message:
                self._send_close = True

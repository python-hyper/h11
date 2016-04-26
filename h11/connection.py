import collections

__all__ = ["H11Connection"]

from .events import *
from .parser import HttpParser

################################################################
#
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

# XX FIXME: we should error out if people try to pipeline as a client, since
# otherwise we will give silently subtly wrong behavior
#
# XX FIXME: better tracking for when one has really and truly processed a
# single request/response pair would be good.
#
# XX FIXME: might at that point make sense to split the client and server into
# two separate classes?
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
        "data is either a bytes-like, or None to indicate EOF"
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

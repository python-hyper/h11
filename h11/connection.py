import collections

__all__ = ["H11Connection"]

from .util import asciify
from .events import *
from .parser import HttpParser

# XX FIXME: sendfile support?
#   maybe switch data_to_send to returning an iterable of stuff-to-do, which
#     could be a mix of bytes-likes, sendfile objects, and CloseSocket
#   and Data could accept sendfile objects as a .data field

# XX FIXME: once we have the high-level state machine in place, using it to
# drive our own lowlevel parser would not be that hard... it already knows
# (better than libhttp_parser!) things like "next is a chunked-encoded body",
# and if we are allowed to buffer and have context then HTTP tokenization is
# pretty trivial I think? and everything above tokenization we are already
# handling. basically the primitive we need is length-bounded regexp matching:
# try to match regexp, if it fails then wait for more data to arrive in
# buffer, raise HttpParseError if the buffer is already longer than the max
# permitted length.

# XX FIXME: it would be nice to support sending Connection: keep-alive headers
# back to HTTP 1.0 clients who have requested this:
#   https://en.wikipedia.org/wiki/HTTP_persistent_connection#HTTP_1.0
# though I'm not 100% sure whether this actually does anything.

# XX FIXME: replace our RuntimeError's with some more specific "you are doing
# HTTP wrong" error like H2's ProtocolError.

# XX FIXME: we should error out if people try to pipeline as a client, since
# otherwise we will give silently subtly wrong behavior
#
# XX FIXME: better tracking for when one has really and truly processed a
# single request/response pair would be good.
#
# XX FIXME: might at that point make sense to split the client and server into
# two separate classes?

# headers to consider auto-supporting at the high-level:
# - Date: https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7231.html#header.date
#     MUST be sent by origin servers who know what time it is
#     (clients don't bother)
# - Server
# - automagic compression

# should let handlers control timeouts

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
# So this needs shutdown(2). This is what data_to_send's close means -- this
# complicated close dance.

# EndOfMessage is tricky:
# - upgrade trailing data handling
# - must immediately call receive_data(b"") before blocking on socket

# Implementing Expect: 100-continue on the client is also tricky: see RFC 7231
# 5.1.1 for details, but in particular if you get a 417 then you have to drop
# the Expect: and then try again.
#
# On the server: HTTP/1.0 + Expect: 100-continue is like the 100-continue
# didn't even exist, you just ignore it.
# And if you want it to go away, you should send a 4xx + Connection: close +
# EOM and then we'll close it and the client won't send everything. Otherwise
# you have to read it all.
#
# For any Expect: value besides 100-continue, it was originally intended that
# the server should blow up if it's unrecognized, but the RFC7xxx specs gave
# up on this because no-one implemented it, so now servers are free to
# blithely ignore unrecognized Expect: values.

# Client sends (regex):
#   Request Data* EndOfMessage
# Server sends (regex):
#   InformationalResponse* Response Data* EndOfMessage
# They are linked in two places:
# - client has wait-for-100-continue state (not shown) where the transition
#   out is receiving a InformationalResponse or Response (or timeout)
# - *both* EndOfMessage's have to arrive before *either* machine returns to
#   the start state.

################################################################

# We model the joint state of the client and server as a pair of finite state
# automata: one for the client and one for the server. Transitions in each
# machine can be triggered by either local or remote events. (For example, the
# client sending a Request triggers both the client to move to SENDING-BODY
# and the server to move to SENDING-RESPONSE.)

# We reuse the Client and Server class objects as sentinel values for
# referring to the two parties.
class Client(Enum):
    IDLE = 1
    WAIT_FOR_100 = 2
    SENDING_BODY = 3
    DONE = 4
    CLOSED = 5

class Server(Enum):
    WAIT = 1
    SENDING_RESPONSE = 2
    SENDING_BODY = 3
    DONE = 4
    CLOSED = 5

def _get_next_client_state_for_request(request):
    # if this request has no body, go straight to DONE
    # if this request has Expect: 100-continue, go to WAIT_FOR_100
    # otherwise, go to SENDING_BODY

def _get_next_server_state_for_response(response):
    # if this response has no body, go straight to DONE
    # otherwise, go to SENDING_BODY

client_transitions = {
    Client.IDLE: {
        (Client, Request): _get_next_client_state_for_request,
    },
    Client.WAIT_FOR_100: {
        (Server, InformationalResponse): Client.SENDING_BODY,
        (Server, Response): Client.SENDING_BODY,
        (Client, Data): Client.SENDING_BODY,
        (Client, EndOfMessage): Client.DONE,
    }
    Client.SENDING_BODY: {
        (Client, Data): Client.SENDING_BODY,
        (Client, EndOfMessage): Client.DONE,
    },
}

server_transitions = {
    Server.WAIT_FOR_REQUEST: {
        (Client, Request): Server.SENDING_RESPONSE,
    },
    Server.SENDING_RESPONSE: {
        (Server, InformationalResponse): Server.SENDING_RESPONSE,
        (Server, Response): _get_next_server_state_for_response,
    },
    Server.SENDING_BODY: {
        (Server, Data): Server.SENDING_BODY,
        (Server, EndOfMessage): Server.DONE,
    },
}

class PartyMachine:
    def __init__(self, party, initial_state, transitions):
        self.party = party
        self.initial_state = initial_state
        self.transitions = transitions
        self.reset()

    def process_event(self, party, event):
        key = (party, type(event))
        new_state = self.transitions[self.state].get(key)
        if new_state is None:
            if party is not self.party:
                return
            else:
                raise RuntimeError(
                    "illegal event {} in state {}".format(key, self.state))
        if callable(new_state):
            new_state = new_state(event)
        self.state = new_state

    def reset(self):
        self.state = self.initial_state

class ConnectionState:
    def __init__(self):
        self._client_machine = PartyMachine(Client.IDLE, client_transitions)
        self._server_machine = PartyMachine(Server.WAIT, server_transitions)
        self.request = None
        self.response = None

    @property
    def client_state(self):
        return self._client_machine.state

    @property
    def server_state(self):
        return self._server_machine.state

    def process_event(self, party, event):
        self.client_machine.receive_event(party, event)
        self.server_machine.receive_event(party, event)
        if isinstance(event, Request):
            self.request = event
        if isinstance(event, Response):
            self.response = event
        if (self.client_state is Client.DONE
            and self.server_state is Server.DONE):
            # XX FIXME either move them to CLOSING state or reset them
            self._client_machine.reset()
            self._server_machine.reset()
            self.request = None
            self.response = None

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

    # Some things never have a body, regardless of what the headers might say.
    # (Technically in some but not all of these cases we should error out if
    # the framing headers are present since they'e protocol violations. But
    # for now we don't.)
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
    # headers at all -- we don't know it.
    #
    # If we're a client sending a request, then we just set Transfer-Encoding:
    # chunked and hope for the best. This will only work with HTTP/1.1
    # servers, but almost all servers now qualify, and if we have a HTTP/1.0
    # server then we can't send a variable length body at all. (If you wanted
    # to send no body then you should have said Content-Length: 0.)
    if isinstance(event, Request):
        if transfer_encoding is None:
            event.headers.append((b"Transfer-Encoding", b"chunked"))
        return ChunkedFramer()
    # If we're a server sending a response, then we should use chunked IFF we
    # are talking to a HTTP/1.1 client, and otherwise use the HTTP/1.0 "send
    # and then close" framing. In the latter case we also have to set the
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
            # XX FIXME: could at least make an effort to pull out the status
            # message from stdlib's http.HTTPStatus table. Or maybe just steal
            # their enums (either by import or copy/paste). We already accept
            # them since they're of type IntEnum < int.
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

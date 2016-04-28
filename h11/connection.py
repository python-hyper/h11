# We model the joint state of the client and server as a pair of finite state
# automata: one for the client and one for the server. Transitions in each
# machine can be triggered by either local or remote events. (For example, the
# client sending a Request triggers both the client to move to SENDING-BODY
# and the server to move to SENDING-RESPONSE.)


# for things we receive:
# can skip figuring out framing b/c the low-level parser does that
# but do want to check whether it has a body to keep the state machine
# consistent
# or maybe we should just figure out framing anyway, b/c someday we won't rely
# on the low-level parser

# for things we're sending:
# if HTTP/1.0 / Connection stuff means we should close, set Connection: close
#
# then figure out framing we're using and munge the headers to match
#
# save the connection close information somewhere

# invariant: state machine should see the final version of each event, post
# all munging


# Receive loop:
# - get event
# - if sending (remote) party is in DONE state, queue it for later
# - otherwise, pass it through state machines and then return
#   - maybe converting state machine errors into HttpParseError
#   - (or HttpPeerError)?
#
# Send loop:
# - get event
# - check if event is even allowed; otherwise, error
# - for Data and EndOfMessage, send via framer
# - for InformationalResponse, send
# - for Request:
#   - User must have set Content-Length or Transfer-Encoding if there's a body
#   - Figure out body framing:
#     - Content-Length: works
#     - Transfer-Encoding: chunked works (we hope, nothing to do otherwise)
#     - Otherwise, has no body
#   - Connection: close is already set or not, not our problem
# - for Response:
#   - Figure out body framing
#     - Content-Length: works
#     - Transfer-Encoding or unset: munge Transfer-Encoding and Connection
#       appropriately
#   - Set framing headers appropriately
#     - if no Content-length and is Response and has body:
#         if peer is HTTP/1.1, always use chunked. otherwise use close.
#   - set Connection: close appropriately (based on Transfer-Encoding etc.)
#   - send
# - pass it through state machines


import collections
from enum import Enum

__all__ = ["H11Connection"]

from .util import bytesify
from .events import *
from .parser import HttpParser
from .headers import get_comma_header, set_comma_header

# Standard rules:
# - If either side says they want to close the connection, then the connection
#   must close.
# - HTTP/1.1 defaults to keep-alive unless someone says Connection: close
# - HTTP/1.0 defaults to close unless both sides say Connection: keep-alive
#   (and even this is a mess -- e.g. if you're proxy then this is illegal).
#
# We simplify life by simply not supporting keep-alive with HTTP/1.0 peers. So
# our rule is:
# - If someone says Connection: close, we will close
# - If someone uses HTTP 1.0, we will close.
def _should_close(event):
    # NB: InformationalResponse should not come through here
    assert isinstance(event, (Request, Response))
    connection = _get_comma_header(event.headers, "Connection")
    if b"close" in connection:
        return True
    if getattr(event, "http_version", "1.1") < "1.1":
        return True
    return False

def _has_expect_100_continue(request):
    assert isinstance(request, Request)
    # Expect: 100-continue is case *sensitive*
    expect = _get_comma_header(request.headers, "Expect", lowercase=False)
    return (b"100-continue" in expect)

################################################################
#
# Body framing (Transfer-Encoding and Content-Length)
#
# Detailed rules for interpreting these headers are here:
#
#     https://tools.ietf.org/html/rfc7230#section-3.3.3
#
################################################################

def _get_framing_headers(headers):
    # Returns:
    #
    #   effective_transfer_encoding, effective_content_length
    #
    # At least one will always be None.
    #
    # Transfer-Encoding beats Content-Length, so check Transfer-Encoding
    # first.
    transfer_encodings = _get_comma_header(headers, "Transfer-Encoding")
    if transfer_encodings not in ([], [b"chunked"]):
        raise RuntimeError(
            "unsupported Transfer-Encodings {!r}".format(transfer_encodings))
    if transfer_encodings:
        return b"chunked", None

    content_lengths = _get_comma_header(headers, "Content-Length")
    if len(content_lengths) > 1:
        raise RuntimeError(
            "encountered multiple Content-Length headers")
    if content_lengths:
        return None, int(content_lengths[0])
    else:
        return None, None

def _request_has_body(request):
    assert isinstance(request, Request)
    # Requests by default don't have bodies; needs a Transfer-Encoding, or a
    # non-zero Content-Length.
    transfer_encoding, content_length = _get_framing_headers(request.headers)
    if transfer_encoding is not None:
        return True
    if content_length is not None and content_length > 0:
        return True
    return False

def _response_has_body(response, *, response_to):
    assert isinstance(response, (InformationalResponse, Response))
    assert isinstance(response_to, Request)
    # Responses by default *do* have bodies, except if they meet some
    # particular criteria, or have Content-Length: 0
    if (response.status_code < 200
        or response.status_code in (204, 304)
        or response_to.method == b"HEAD"
        or (response_to.method == b"CONNECT"
            and 200 <= response.status_code < 300)):
        return False
    _, content_length = _get_framing_headers(request.headers)
    if content_length is not None and content_length == 0:
        return False
    return True

def _clean_up_response_headers_for_sending(response, *, response_to):
    assert isinstance(response, Response)
    assert isinstance(response_to, Request)
    # Tricky bits to this:
    # - We take responsibility for setting Connection: close if the client
    #   doesn't support keep-alive
    # - We take the responsibility of setting Transfer-Encoding etc. correctly
    #   if user didn't set Content-Length, taking into account peer's HTTP
    #   version
    # - The actual framing stuff is taken care of later, based on the final
    #   munged headers.
    do_close = _should_close(response) or _should_close(response_to)

    _, effective_content_length = _get_framing_headers(response.headers)
    if (_response_has_body(response, response_to=response_to)
        and effective_content_length is None):
        # This response has a body of unknown length.
        # If our peer is HTTP/1.1, we use Transfer-Encoding: chunked
        # If our peer is HTTP/1.0, we use no framing headers, and close the
        # connection afterwards.
        #
        # Make sure to clear Content-Length (could have been set but
        # overridden by Transfer-Encoding -- even though setting both is a
        # spec violation)
        _set_comma_header(response.headers, "Content-Length", [])
        # If we're sending the response, the request came from the wire, so it
        # should have an attached http_version
        assert hasattr(response_to, "http_version")
        if response_to.http_version < "1.1":
            _set_comma_header(response.headers, "Transfer-Encoding", [])
            do_close = True
        else:
            _set_comma_header(response.headers,
                              "Transfer-Encoding", ["chunked"])

    # Set Connection: close if we need it.
    connection = set(_get_comma_header(response.headers, "Connection"))
    if do_close and b"close" not in connection:
        connection.discard(b"keep-alive")
        connection.add(b"close")
        _set_comma_header(response.headers, "Connection", connection)

# Only called if we have to send DATA, so can take for granted that this
# message does in fact have a body
def _get_framer(headers):
    transfer_encoding, content_length = _get_framing_headers(headers)
    if transfer_encoding is not None:
        return ChunkedFramer()
    elif content_length is not None:
        return ContentLengthFramer(content_length)
    else:
        return HTTP10Framer()

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

# Convention:
#
# Both machines see all events (i.e., client machine sees both client and
# server events, server machine sees both client and server events). But they
# have different defaults: if the client machine sees a client event that it
# has no transition for, that's an error. But if the client machine sees a
# server event that it has no transition for, then that's ignored. And
# similarly for the server.

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
        self._client_machine = PartyMachine(
            party=Client,
            initial_state=Client.IDLE,
            transitions = {
                Client.IDLE: {
                    (Client, Request): self._next_client_state_for_request,
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
            })

        self._client_machine = PartyMachine(
            party=Server,
            initial_state=Server.WAIT_FOR_REQUEST,
            transitions = {
                Server.WAIT_FOR_REQUEST: {
                    (Client, Request): Server.SENDING_RESPONSE,
                },
                Server.SENDING_RESPONSE: {
                    (Server, InformationalResponse): Server.SENDING_RESPONSE,
                    (Server, Response): self._next_server_state_for_response,
                },
                Server.SENDING_BODY: {
                    (Server, Data): Server.SENDING_BODY,
                    (Server, EndOfMessage): Server.DONE,
                },
            })
        self.request = None
        self.response = None

    def _next_client_state_for_request(self, request):
        if _request_has_body(request):
            if ("Expect", "100-continue") in request.headers:
                return Client.WAIT_FOR_100
            else:
                return Client.SENDING_BODY
        else:
            return Client.DONE

    def _next_server_state_for_response(self, response):
        assert self.request is not None
        if _response_has_body(response, response_to=self.request):
            return Server.SENDING_BODY
        else:
            return Server.DONE

    @property
    def client_state(self):
        return self._client_machine.state

    @property
    def server_state(self):
        return self._server_machine.state

    def process_event(self, party, event):
        self.client_machine.process_event(party, event)
        self.server_machine.process_event(party, event)
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
    wanted_field = bytesify(wanted_field).lower()
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
        if response_to.http_version < "1.1":
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

    # XX FIXME: "Since the Host field-value is critical information for
    # handling a request, a user agent SHOULD generate Host as the first
    # header field following the request-line." - RFC 7230
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

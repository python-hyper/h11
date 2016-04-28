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
from .headers import (
    get_comma_header, set_comma_header,
    get_framing_headers, request_has_body, response_has_body,
    should_close,
    )

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
        self.state = initial_state
        self.transitions = transitions

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
        if request_has_body(request):
            if _has_expect_100_continue(request):
                return Client.WAIT_FOR_100
            else:
                return Client.SENDING_BODY
        else:
            return Client.DONE

    def _next_server_state_for_response(self, response):
        assert self.request is not None
        if response_has_body(response, response_to=self.request):
            return Server.SENDING_BODY
        else:
            return Server.DONE

    @property
    def client_state(self):
        return self._client_machine.state

    @property
    def server_state(self):
        return self._server_machine.state

    def state(self, party):
        if party is Client:
            return self._client_machine.state
        elif party is Server:
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
            if should_close(self.request) or should_close(self.response):
                self._client_machine.state = Client.CLOSED
                self._server_machine.state = Server.CLOSED
            else:
                self._client_machine.state = Client.IDLE
                self._server_machine.state = Server.WAIT_FOR_REQUEST
            self.request = None
            self.response = None

################################################################
#
# Code for handling framing on send
#
################################################################

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

# This gets passed the headers of a Request or Response that we have sent, and
# that (a) definitely has a body, (b) has already had its headers cleaned up
# to their final form. Its only job is to create a Framer object to handle
# sending this message's body.
def _get_framer(headers):
    transfer_encoding, content_length = get_framing_headers(headers)
    if transfer_encoding is not None:
        return ChunkedFramer()
    elif content_length is not None:
        return ContentLengthFramer(content_length)
    else:
        return HTTP10Framer()

################################################################
#
# The one messy thing -- setting up headers on Responses that we're
# sending. We take a bit of responsibility here:
# - We worry about setting 'Connection: close' so the user doesn't have
#   to. (Of course they can, if they want, and we'll respect that and then
#   make sure things get closed at the appropriate time.)
# - The user has to set Content-Length if they want it. Otherwise, for
#   responses that have bodies (e.g. not HEAD), then we will take care of
#   picking the right way to do streaming depending on the peer's HTTP
#   version.
# - This function's *only* responsibility is making sure headers are set up
#   right -- everything downstream just looks at the headers. There are no
#   side channels.
#
################################################################

def _clean_up_response_headers_for_sending(response, *, response_to):
    assert isinstance(response, Response)
    assert isinstance(response_to, Request)

    do_close = should_close(response) or should_close(response_to)

    _, effective_content_length = get_framing_headers(response.headers)
    if (response_has_body(response, response_to=response_to)
        and effective_content_length is None):
        # This response has a body of unknown length.
        # If our peer is HTTP/1.1, we use Transfer-Encoding: chunked
        # If our peer is HTTP/1.0, we use no framing headers, and close the
        # connection afterwards.
        #
        # Make sure to clear Content-Length (in principle user could have set
        # both and then we ignored Content-Length b/c Transfer-Encoding
        # overwrote it -- this would be naughty of them, but the HTTP spec
        # says that if our peer does this then we have to fix it instead of
        # erroring out, so we'll accord the user the same respect).
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
        _set_comma_header(response.headers, "Connection", sorted(connection))

################################################################
#
# The main H11Connection class
#
################################################################

class H11Connection:
    def __init__(self, *, client_side):
        self._client_side = client_side
        # The double-state-machine that tracks the state of the two sides
        self._connection_state = ConnectionState()
        if client_side:
            self._me = Client
            self._them = Server
        else:
            self._me = Server
            self._them = Client
        # The streaming parser
        self._parser = HttpParser(client_side=client_side)
        # Data that should be sent when possible
        self._data_to_send = bytearray()
        # Holds the encoder we should use for sending body data:
        self._body_framer = None
        #
        # The rest are only relevant in server mode:
        #
        # We only process a single request/response cycle at a time. If the
        # other end pipelines (sends another request before we've responded to
        # the first), then we queue the new messages here and don't return
        # them until the full request/response cycle has completed.
        self._receive_event_buffer = collections.deque()

    def receive_data(self, data):
        "data is either a bytes-like, or None to indicate EOF"
        self._receive_event_buffer.extend(self._parser.receive_data(data))
        events = []
        while self._receive_event_buffer:
            if self._connection_state.state(self._them) is self._them.DONE:
                # Refuse to process any new events until this cycle is
                # complete.
                break
            event = self._receive_event_buffer.popleft()
            self._connection_state.process_event(self._them, event)
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
        if data is CloseSocket:
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
        for name, value in headers:
            self._send(b"%s: %s\r\n" % (bytesify(name), bytesify(value)))
        self._send(b"\r\n")

    def send(self, event):
        if isinstance(event, Response):
            _clean_up_response_headers_for_sending(
                event, response_to=self._connection_state.request)

        # XX FIXME: This is redundant with the connection state tracking,
        # because libhttp_parser needs this information redundantly. (See also
        # the duplication of logic between _libhttp_parser.on_headers_complete
        # and *_has_body.)
        if isinstance(event, Request):
            self._parser.set_method(event.method)

        self._connection_state.process_event(self._me, event)

        if (isinstance(event, (Request, Response))
            and self._connection_state.state(self._me) is self._me.SENDING_BODY:
            self._body_framer = _get_framer(event.headers)

        if isinstance(event, Request):
            self._send(b"%s %s HTTP/1.1\r\n" % (event.method, event.url))
            self._send_headers(event.headers)

        elif isinstance(event, _ResponseBase):
            status_bytes = str(event.status).encode("ascii")
            # We don't bother sending ascii status messages like "OK"; they're
            # optional anyway. (But the space after the numeric status code is
            # mandatory.)
            # XX FIXME: could at least make an effort to pull out the status
            # message from stdlib's http.HTTPStatus table. Or maybe just steal
            # their enums (either by import or copy/paste). We already accept
            # them since they're of type IntEnum < int.
            self._send(b"HTTP/1.1 %s \r\n" % (status_bytes,))
            self._send_headers(event.headers)

        elif isinstance(event, Data):
            self._body_framer.send_data(event.data, self)

        elif isinstance(event, EndOfMessage):
            self._body_framer.send_eom(event, self)

        # XX FIXME: when our state transitions to CLOSED, we should issue a
        # close. But this could happen on send *or* receive.
        # XX also, the server should go directly to CLOSED if they reach DONE
        # and would transition to CLOSED from there, if that's where they're
        # going -- no need to wait for client to finish sending.
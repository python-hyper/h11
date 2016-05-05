# This contains the main Connection class. Everything in h11 revolves around
# this.

# Import all event types
from .events import *
# Import all state sentinels
from .state import *
# Import the internal things we need
from .util import ProtocolError
from .state import ConnectionState
from .headers import (
    get_comma_header, set_comma_header, has_expect_100_continue,
)
from .receivebuffer import ReceiveBuffer
from .readers import READERS
from .writers import WRITERS

# Everything in __all__ gets re-exported as part of the h11 public API.
__all__ = ["Connection"]

# If we ever have this much buffered without it making a complete parseable
# event, we error out. The only time we really buffer is when reading the
# request/reponse line + headers together, so this is effectively the limit on
# the size of that.
#
# Some precedents for defaults:
# - node.js: 80 * 1024
# - tomcat: 8 * 1024
# - IIS: 16 * 1024
# - Apache: <8 KiB per line>
HTTP_DEFAULT_MAX_BUFFER_SIZE = 16 * 1024

# RFC 7230's rules for connection lifecycles:
# - If either side says they want to close the connection, then the connection
#   must close.
# - HTTP/1.1 defaults to keep-alive unless someone says Connection: close
# - HTTP/1.0 defaults to close unless both sides say Connection: keep-alive
#   (and even this is a mess -- e.g. if you're implementing a proxy then
#   sending Connection: keep-alive is forbidden).
#
# We simplify life by simply not supporting keep-alive with HTTP/1.0 peers. So
# our rule is:
# - If someone says Connection: close, we will close
# - If someone uses HTTP/1.0, we will close.
def _keep_alive(event):
    connection = get_comma_header(event.headers, "Connection")
    if b"close" in connection:
        return False
    if getattr(event, "http_version", b"1.1") < b"1.1":
        return False
    return True

def _body_framing(request_method, event):
    # Called when we enter SEND_BODY to figure out framing information for
    # this body.
    #
    # These are the only two events that can trigger a SEND_BODY state:
    assert type(event) in (Request, Response)
    # Returns one of:
    #
    #    ("content-length", count)
    #    ("chunked", ())
    #    ("http/1.0", ())
    #
    # which are (lookup key, *args) for constructing body reader/writer
    # objects.
    #
    # Reference: https://tools.ietf.org/html/rfc7230#section-3.3.3
    #
    # Step 1: some responses always have an empty body, regardless of what the
    # headers say.
    if type(event) is Response:
        if request_method == b"HEAD" or event.status_code in (204, 304):
            return ("content-length", (0,))
        # Section 3.3.3 also lists two other cases:
        # 1) Responses with status_code < 200. For us these are
        #    InformationalResponses, not Responses, so can't get here.
        assert event.status_code >= 200
        # 2) 2xx responses to CONNECT requests. We interpret things
        #    differently: it's not that successful CONNECT responses have an
        #    empty body, it's that the HTTP conversation stops after the 2xx
        #    CONNECT headers (we switch into SWITCH_PROTOCOL state), so the
        #    question of how to interpret the body framing headers never comes
        #    up. 2xx CONNECT responses can't reach here.
        assert not (request_method == b"CONNECT"
                    and 200 <= event.status_code < 300)

    # Step 2: check for Transfer-Encoding (T-E beats C-L):
    transfer_encodings = get_comma_header(event.headers, "Transfer-Encoding")
    if transfer_encodings:
        assert transfer_encodings == [b"chunked"]
        return ("chunked", ())

    # Step 3: check for Content-Length
    content_lengths = get_comma_header(event.headers, "Content-Length")
    if content_lengths:
        return ("content-length", (int(content_lengths[0]),))

    # Step 4: no applicable headers; fallback/default depends on type
    if type(event) is Request:
        return ("content-length", (0,))
    else:
        return ("http/1.0", ())

################################################################
#
# The main Connection class
#
################################################################

class Connection:
    def __init__(self, our_role, max_buffer_size=HTTP_DEFAULT_MAX_BUFFER_SIZE):
        self._max_buffer_size = HTTP_DEFAULT_MAX_BUFFER_SIZE
        # State and role tracking
        if our_role not in (CLIENT, SERVER):
            raise ValueError(
                "expected CLIENT or SERVER, not {!r}".format(our_role))
        self.our_role = our_role
        if our_role is CLIENT:
            self.their_role = SERVER
        else:
            self.their_role = CLIENT
        self._cstate = ConnectionState()

        # Callables for converting data->events or vice-versa given the
        # current state
        self._writer = self._get_io_object(self.our_role, None, WRITERS)
        self._reader = self._get_io_object(self.their_role, None, READERS)

        # Holds any unprocessed received data
        self._receive_buffer = ReceiveBuffer()
        # If this is true, then it indicates that the incoming connection was
        # closed *after* the end of whatever's in self._receive_buffer:
        self._receive_buffer_closed = False

        # Extra bits of state that don't fit into the state machine.
        #
        # These two are only used to interpret framing headers for figuring
        # out how to read/write response bodies. their_http_version is also
        # made available as a convenient public API:
        self.their_http_version = None
        self._request_method = None
        # This is pure flow-control and doesn't at all affect the set of legal
        # transitions, so no need to bother ConnectionState with it:
        self.client_is_waiting_for_100_continue = False

    def state_of(self, role):
        return self._cstate.states[role]

    @property
    def client_state(self):
        return self._cstate.states[CLIENT]

    @property
    def server_state(self):
        return self._cstate.states[SERVER]

    @property
    def our_state(self):
        return self._cstate.states[self.our_role]

    @property
    def their_state(self):
        return self._cstate.states[self.their_role]

    @property
    def they_are_waiting_for_100_continue(self):
        return (self.their_role is CLIENT
                and self.client_is_waiting_for_100_continue)

    def prepare_to_reuse(self):
        self._cstate.prepare_to_reuse()
        self._request_method = None
        # self.their_http_version gets left alone, since it presumably lasts
        # beyond a single request/response cycle
        assert not self.client_is_waiting_for_100_continue
        assert self._cstate.keep_alive
        assert not self._cstate.client_requested_protocol_switch_pending

    def _get_io_object(self, role, event, io_dict):
        state = self._cstate.states[role]
        if state is SEND_BODY:
            # Special case: the io_dict has a dict of reader/writer factories
            # that depend on the request/response framing.
            framing_type, args = _body_framing(self._request_method, event)
            return io_dict[SEND_BODY][framing_type](*args)
        else:
            # General case: the io_dict just has the appropriate reader/writer
            # for this state
            return io_dict.get((role, state))

    def _client_switch_events(self, event):
        if event.method == b"CONNECT":
            yield SWITCH_CONNECT
        if get_comma_header(event.headers, "Upgrade"):
            yield SWITCH_UPGRADE

    def _server_switch_event(self, event):
        if type(event) is InformationalResponse and event.status_code == 101:
            return SWITCH_UPGRADE
        if type(event) is Response:
            if (SWITCH_CONNECT in self._cstate.pending_switch_proposals
                and 200 <= event.status_code < 300):
                return SWITCH_CONNECT
        return None

    # All events and state machine updates go through here.
    def _process_event(self, role, event):
        # First, pass the event through the state machine to make sure it
        # succeeds.
        old_states = dict(self._cstate.states)
        if role is CLIENT and type(event) is Request:
            switch_event_iter = self._client_switch_events(event)
            self._cstate.process_client_switch_proposals(switch_event_iter)
        server_switch_event = None
        if role is SERVER and self._cstate.pending_switch_proposals:
            server_switch_event = self._server_switch_event(event)
        self._cstate.process_event(role, type(event), server_switch_event)

        # Then perform the updates triggered by it.

        # self._request_method
        if type(event) is Request:
            self._request_method = event.method

        # self.their_http_version
        if (role is self.their_role
            and type(event) in (Request, Response, InformationalResponse)):
            self.their_http_version = event.http_version

        # Keep alive handling
        #
        # RFC 7230 doesn't really say what one should do if Connection: close
        # shows up on a 1xx InformationalResponse. I think the idea is that
        # this is not supposed to happen. In any case, if it does happen, we
        # ignore it.
        if type(event) in (Request, Response) and not _keep_alive(event):
            self._cstate.process_keep_alive_disabled()

        # 100-continue
        if type(event) is Request and has_expect_100_continue(event):
            self.client_is_waiting_for_100_continue = True
        if type(event) in (InformationalResponse, Response):
            self.client_is_waiting_for_100_continue = False
        if role is CLIENT and type(event) in (Data, EndOfMessage):
            self.client_is_waiting_for_100_continue = False

        # Update reader/writer
        if self.our_state != old_states[self.our_role]:
            self._writer = self._get_io_object(self.our_role, event, WRITERS)
        if self.their_state != old_states[self.their_role]:
            print("their state changed to", self.their_state)
            self._reader = self._get_io_object(self.their_role, event, READERS)
            print("new reader is ", self._reader)

    @property
    def trailing_data(self):
        return bytes(self._receive_buffer)

    # Argument interpretation:
    # - b""  -> connection closed
    # - None -> no new data, just check for whether any events have become
    #           available (useful iff we were in Paused state)
    # - data -> bytes-like of data received
    # XX this method is a tangled bramble
    def receive_data(self, data):
        if data is not None:
            if data:
                self._receive_buffer += data
            else:
                self._receive_buffer_closed = True

        events = []
        while True:
            state = self.their_state
            print("Looping in state", state)
            # We don't pause immediately when they enter DONE, because even in
            # DONE state we can still process a ConnectionClosed() event. But
            # if we have data in our buffer, then we definitely aren't getting
            # a ConnectionClosed() immediately and we need to pause.
            if state is DONE and self._receive_buffer:
                # The Paused pseudo-event doesn't go through the state
                # machine, because it's purely a local signal.
                events.append(Paused(reason="need-reset"))
                break
            if state is MIGHT_SWITCH_PROTOCOL:
                events.append(Paused(reason="might-switch-protocol"))
                break
            if state is SWITCHED_PROTOCOL:
                events.append(Paused(reason="switched-protocol"))
                break
            if not self._receive_buffer and self._receive_buffer_closed:
                print("processing close")
                if hasattr(self._reader, "read_eof"):
                    print("{!r} has read_eof".format(self._reader))
                    event = self._reader.read_eof()
                    print("read_eof() returned", event)
                elif state is CLOSED:
                    break
                else:
                    print("{!r} does NOT have read_eof".format(self._reader))
                    event = ConnectionClosed()
            else:
                if self._reader is None:
                    if self._receive_buffer:
                        raise ProtocolError(
                            "unexpectedly received data in state {}"
                            .format(state))
                    else:
                        # Terminal state like MUST_CLOSE with no data... no
                        # problem, nothing to do, perhaps they'll close it in
                        # a moment.
                        break
                print("calling reader", self._reader)
                event = self._reader(self._receive_buffer)
                print("it returned:", event)
            if event is None:
                if len(self._receive_buffer) > self._max_buffer_size:
                    # 414 is "Request-URI Too Long" which is not quite
                    # accurate because we'll also issue this if someone tries
                    # to send e.g. a megabyte of headers, but it's probably
                    # more useful than 400 Bad Request?
                    raise ProtocolError("Receive buffer too long",
                                        error_status_hint=414)
                break
            self._process_event(self.their_role, event)
            events.append(event)
            if type(event) is ConnectionClosed:
                break
        self._receive_buffer.compress()
        print("Returning {} new events".format(len(events)))
        return events

    def send(self, event, *, combine=True):
        if type(event) is Response:
            self._clean_up_response_headers_for_sending(event)
        # We want to process the event locally before actually sending it, so
        # that if processing it throws an error then nothing happens. But
        # processing it may change self._writer. So we save self._writer now
        # and then call it after. Special case: sending ConnectionClosed()
        # skips the writer.
        writer = self._writer
        self._process_event(self.our_role, event)
        if type(event) is ConnectionClosed:
            return None
        else:
            data_list = []
            writer(event, data_list.append)
            if combine:
                return b"".join(data_list)
            else:
                return data_list

    # When sending a Response, we take responsibility for a few things:
    #
    # - Sometimes you MUST set Connection: close. We take care of those
    #   times. (You can also set it yourself if you want, and if you do then
    #   we'll respect that and close the connection at the right time. But you
    #   don't have to worry about that unless you want to.)
    #
    # - The user has to set Content-Length if they want it. Otherwise, for
    #   responses that have bodies (e.g. not HEAD), then we will automatically
    #   select the right mechanism for streaming a body of unknown length,
    #   which depends on depending on the peer's HTTP version.
    #
    # This function's *only* responsibility is making sure headers are set up
    # right -- everything downstream just looks at the headers. There are no
    # side channels. It mutates the response event in-place (but not the
    # response.headers list object).
    def _clean_up_response_headers_for_sending(self, response):
        assert type(response) is Response

        headers = list(response.headers)
        need_close = False

        framing_type, _ = _body_framing(self._request_method, response)
        if framing_type in ("chunked", "http/1.0"):
            # This response has a body of unknown length.
            # If our peer is HTTP/1.1, we use Transfer-Encoding: chunked
            # If our peer is HTTP/1.0, we use no framing headers, and close the
            # connection afterwards.
            #
            # Make sure to clear Content-Length (in principle user could have
            # set both and then we ignored Content-Length b/c
            # Transfer-Encoding overwrote it -- this would be naughty of them,
            # but the HTTP spec says that if our peer does this then we have
            # to fix it instead of erroring out, so we'll accord the user the
            # same respect).
            set_comma_header(headers, "Content-Length", [])
            if self.their_http_version < b"1.1":
                set_comma_header(headers, "Transfer-Encoding", [])
                # This is actually redundant ATM, since currently we
                # unconditionally disable keep-alive when talking to HTTP/1.0
                # peers. But let's be defensive just in case we add
                # Connection: keep-alive support later:
                need_close = True
            else:
                set_comma_header(headers, "Transfer-Encoding", ["chunked"])

        if not self._cstate.keep_alive or need_close:
            # Make sure Connection: close is set
            connection = set(get_comma_header(headers, "Connection"))
            if b"close" not in connection:
                connection.discard(b"keep-alive")
                connection.add(b"close")
                set_comma_header(headers, "Connection", sorted(connection))

        response.headers = headers

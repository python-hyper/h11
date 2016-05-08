# This contains the main Connection class. Everything in h11 revolves around
# this.

# Import all event types
from .events import *
# Import all state sentinels
from .state import *
# Import the internal things we need
from .util import ProtocolError
from .state import ConnectionState, _SWITCH_UPGRADE, _SWITCH_CONNECT
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
        if (event.status_code in (204, 304)
            or request_method == b"HEAD"
            or (request_method == b"CONNECT"
                and 200 <= event.status_code < 300)):
            return ("content-length", (0,))
        # Section 3.3.3 also lists another case -- responses with status_code
        # < 200. For us these are InformationalResponses, not Responses, so
        # they can't get into this function in the first place.
        assert event.status_code >= 200

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
        self._max_buffer_size = max_buffer_size
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
        # made available as a convenient public API.
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
        old_states = dict(self._cstate.states)
        self._cstate.prepare_to_reuse()
        self._request_method = None
        # self.their_http_version gets left alone, since it presumably lasts
        # beyond a single request/response cycle
        assert not self.client_is_waiting_for_100_continue
        self._respond_to_state_changes(old_states)

    def _process_error(self, role):
        old_states = dict(self._cstate.states)
        self._cstate.process_error(role)
        self._respond_to_state_changes(old_states)

    def _client_switch_events(self, event):
        if event.method == b"CONNECT":
            yield _SWITCH_CONNECT
        if get_comma_header(event.headers, "Upgrade"):
            yield _SWITCH_UPGRADE

    def _server_switch_event(self, event):
        if type(event) is InformationalResponse and event.status_code == 101:
            return _SWITCH_UPGRADE
        if type(event) is Response:
            if (_SWITCH_CONNECT in self._cstate.pending_switch_proposals
                and 200 <= event.status_code < 300):
                return _SWITCH_CONNECT
        return None

    # All events go through here
    def _process_event(self, role, event):
        # First, pass the event through the state machine to make sure it
        # succeeds.
        old_states = dict(self._cstate.states)
        if role is CLIENT and type(event) is Request:
            switch_event_iter = self._client_switch_events(event)
            self._cstate.process_client_switch_proposals(switch_event_iter)
        server_switch_event = None
        if role is SERVER:
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

        self._respond_to_state_changes(old_states, event)

    def _get_io_object(self, role, event, io_dict):
        # event may be None; it's only used when entering SEND_BODY
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

    # This must be called after any action that might have caused
    # self._cstate.states to change.
    def _respond_to_state_changes(self, old_states, event=None):
        # Update reader/writer
        if self.our_state != old_states[self.our_role]:
            self._writer = self._get_io_object(self.our_role, event, WRITERS)
        if self.their_state != old_states[self.their_role]:
            self._reader = self._get_io_object(self.their_role, event, READERS)

    @property
    def trailing_data(self):
        return (bytes(self._receive_buffer), self._receive_buffer_closed)

    # Argument interpretation:
    # - b""  -> connection closed
    # - None -> no new data, just check for whether any events have become
    #           available (useful iff we were in Paused state)
    # - data -> bytes-like of data received
    def receive_data(self, data):
        if self.their_state is ERROR:
            raise ProtocolError("Can't receive data when peer state is ERROR")
        try:
            # Update self._receive_buffer with new data
            if data is not None:
                if data:
                    if self._receive_buffer_closed:
                        raise RuntimeError(
                            "received close, then received more data?")
                    self._receive_buffer += data
                else:
                    self._receive_buffer_closed = True

            # Read out all the events we can
            events = []
            while True:
                event = self._next_receive_event()
                if event is None:
                    break
                events.append(event)
                # The Paused pseudo-event doesn't go through the state
                # machine, because it's purely a local signal.
                if type(event) is Paused:
                    break
                self._process_event(self.their_role, event)
                if type(event) is ConnectionClosed:
                    break

            # Buffer maintainence
            self._receive_buffer.compress()
            if len(self._receive_buffer) > self._max_buffer_size:
                # 414 is "Request-URI Too Long" which is not quite
                # accurate because we'll also issue this if someone tries
                # to send e.g. a megabyte of headers, but it's probably
                # more useful than 400 Bad Request?
                raise ProtocolError("Receive buffer too long",
                                    error_status_hint=414)

            # Return them
            return events
        except:
            self._process_error(self.their_role)
            raise

    def _next_receive_event(self):
        state = self.their_state
        # We don't pause immediately when they enter DONE, because even in
        # DONE state we can still process a ConnectionClosed() event. But
        # if we have data in our buffer, then we definitely aren't getting
        # a ConnectionClosed() immediately and we need to pause.
        if state is DONE and self._receive_buffer:
            return Paused(reason="pipelining")
        if state is MIGHT_SWITCH_PROTOCOL:
            return Paused(reason="might-switch-protocol")
        if state is SWITCHED_PROTOCOL:
            return Paused(reason="switched-protocol")
        #
        assert self._reader is not None
        event = self._reader(self._receive_buffer)
        if event is None:
            if not self._receive_buffer and self._receive_buffer_closed:
                # In some unusual cases (basically just HTTP/1.0 bodies), EOF
                # triggers an actual protocol event; in that case, we want to
                # return that event, and then the state will change and we'll
                # get called again to generate the actual ConnectionClosed().
                if hasattr(self._reader, "read_eof"):
                    event = self._reader.read_eof()
                else:
                    event = ConnectionClosed()
        return event

    def send(self, event, *, combine=True):
        if self.our_state is ERROR:
            raise ProtocolError("Can't send data when our state is ERROR")
        try:
            if type(event) is Response:
                self._clean_up_response_headers_for_sending(event)
            # We want to call _process_event before calling the writer,
            # because if someone tries to do something invalid then this will
            # give a sensible error message, while our writers all just assume
            # they will only receive valid events. But, _process_event might
            # change self._writer. So we have to do a little dance:
            writer = self._writer
            self._process_event(self.our_role, event)
            if type(event) is ConnectionClosed:
                return None
            else:
                # In any situation where writer is None, process_event should
                # have raised ProtocolError
                assert writer is not None
                data_list = []
                writer(event, data_list.append)
                if combine:
                    return b"".join(data_list)
                else:
                    return data_list
        except:
            self._process_error(self.our_role)
            raise

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
            if (self.their_http_version is None
                or self.their_http_version < b"1.1"):
                # Either we never got a valid request and are sending back an
                # error (their_http_version is None), so we assume the worst;
                # or else we did get a valid HTTP/1.0 request, so we know that
                # they don't understand chunked encoding.
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
            connection.discard(b"keep-alive")
            connection.add(b"close")
            set_comma_header(headers, "Connection", sorted(connection))

        response.headers = headers

# We model the joint state of the client and server as a pair of finite state
# automata: one for the client and one for the server. Transitions in each
# machine can be triggered by either local or remote events. (For example, the
# client sending a Request triggers both the client to move to SENDING-BODY
# and the server to move to SENDING-RESPONSE.)

# Import all event types
from .events import *
# Import all states
from .state import *
from .headers import (
    get_comma_header, set_comma_header, get_framing_headers, should_close,
    has_expect_100_continue,
)
from .receivebuffer import ReceiveBuffer
from .reading import READERS
from .writing import WRITERS

__all__ = ["Connection"]

# If we ever have this much buffered without it making a complete parseable
# event, we error out.
# Value copied from node.js's http_parser.c's HTTP_MAX_HEADER_SIZE (Headers
# are the only time we really buffer, so the effect is the same.)
HTTP_MAX_BUFFER_SIZE = 80 * 1024

# See https://tools.ietf.org/html/rfc7230#section-3.3.3
def _response_allows_body(response, *, response_to):
    if (response.status_code < 200
        or response.status_code in (204, 304)
        or response_to.method == b"HEAD"
        or (response_to.method == b"CONNECT"
            and 200 <= response.status_code < 300)):
        return False
    else:
        return True

# When sending a Response, we take responsibility for a few things:
#
# - Sometimes you MUST set Connection: close. We take care of those
#   times. (You can also set it yourself if you want, and if you do then we'll
#   respect that and close the connection at the right time. But you don't
#   have to worry about that unless you want to.)
#
# - The user has to set Content-Length if they want it. Otherwise, for
#   responses that have bodies (e.g. not HEAD), then we will automatically
#   select the right mechanism for streaming a body of unknown length, which
#   depends on depending on the peer's HTTP version.
#
# This function's *only* responsibility is making sure headers are set up
# right -- everything downstream just looks at the headers. There are no side
# channels. It mutates the response event in-place.
def _clean_up_response_headers_for_sending(response, *, response_to):
    assert type(response) is Response
    assert type(response_to) is Request

    do_close = should_close(response) or should_close(response_to)

    _, effective_content_length = get_framing_headers(response.headers)
    if (response_allows_body(response, response_to=response_to)
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
        set_comma_header(response.headers, "Content-Length", [])
        # If we're sending the response, the request came from the wire, so it
        # should have an attached http_version
        assert hasattr(response_to, "http_version")
        if response_to.http_version < "1.1":
            set_comma_header(response.headers, "Transfer-Encoding", [])
            # This is actually redundant ATM, since currently we always send
            # Connection: close when talking to HTTP/1.0 peers. But let's be
            # defensive just in case we add Connection: keep-alive support
            # later:
            do_close = True
        else:
            set_comma_header(response.headers,
                             "Transfer-Encoding", ["chunked"])

    # Set Connection: close if we need it.
    connection = set(get_comma_header(response.headers, "Connection"))
    if do_close and b"close" not in connection:
        connection.discard(b"keep-alive")
        connection.add(b"close")
        set_comma_header(response.headers, "Connection", sorted(connection))

################################################################
#
# The main Connection class
#
################################################################

class Connection:
    def __init__(self, *, client_side):
        self._client_side = client_side
        # The double-state-machine that tracks the state of the two sides
        self._cstate = ConnectionState()
        if client_side:
            self._us = CLIENT
            self._them = SERVER
        else:
            self._us = SERVER
            self._them = CLIENT
        # Callables for converting data->events or vice-versa given the
        # current state
        self._writer = self._get_state_obj(self._us, WRITERS)
        self._reader = self._get_state_obj(self._them, READERS)
        # Holds any unprocessed received data
        self._receive_buffer = ReceiveBuffer()
        self._receive_buffer_closed = False
        # The current request/response, since we need to refer to these in
        # various places
        self._request = None
        self._response = None
        # Public API
        self.client_waiting_for_100_continue = False

    def _get_state_obj(self, party, obj_dict):
        state = self._cstate.state(party)
        obj = obj_dict.get((party, state))
        if type(obj) is not dict:
            return obj
        # Otherwise, obj is a dict of factories for body readers/writers
        assert state is SEND_BODY
        request = self._request
        response = self._response
        if (party is SERVER
            and not _response_allows_body(response, response_to=request)):
            # Body is empty, no matter what headers say
            return obj["content-length"](0)
        event = response if party is SERVER else request
        transfer_encoding, content_length = get_framing_headers(event.headers)
        if transfer_encoding:
            assert transfer_encoding == b"chunked"
            return obj["chunked"]()
        if content_length:
            return obj["content-length"](content_length)
        if type(event) is Request:
            return obj["http/1.0"]()
        else:
            return obj["content-length"](0)

    @property
    def client_state(self):
        return self._cstate.state(CLIENT)

    @property
    def server_state(self):
        return self._cstate.state(SERVER)

    @property
    def our_state(self):
        return self._cstate.state(self._us)

    @property
    def their_state(self):
        return self._cstate.state(self._them)

    def _process_event(self, party, event):
        # First make sure that this change is going to succeed
        changed = self._cstate.process_event(party, event)

        # Then perform the updates triggered by it

        if type(event) is Request:
            self._request = event

        if type(event) is Response:
            self._response = event

        if type(event) is Request and has_expect_100_continue(event):
                self.client_waiting_for_100_continue = True
        if type(event) in (InformationalResponse, Response):
            self.client_waiting_for_100_continue = False

        if self._us in changed:
            self._writer = self._get_state_obj(self._us, WRITERS)

        if self._them in changed:
            self._reader = self._get_state_obj(self._them, READERS)

        # The two magical auto-close situations:
        if ((self.server_state is DONE
             and self.our_state is not CLOSED
             and (should_close(self._request) or should_close(self._response)))
            or
             self.our_state in (IDLE, DONE) and self.their_state is CLOSED):
            self.send(ConnectionClosed())

    @property
    def trailing_data(self):
        return bytes(self._receive_buffer)

    def receive_data(self, data):
        if data:
            self._receive_buffer += data
        else:
            self._receive_buffer_closed = True
        return self._process_receive_buffer()

    # Runs the reader without adding new data. The name is a reminder that
    # when implementing a server you have to call this after finishing a
    # request/response cycle, because the client might have sent a pipelined
    # request that got left sitting in our buffer until we were ready for it.
    def receive_pipelined_data(self):
        return self._process_receive_buffer()

    def _process_receive_buffer(self):
        events = []
        while True:
            state = self._cstate.state(self._them)
            if not self._receive_buffer and self._receive_buffer_closed:
                event = ConnectionClosed()
            else:
                if state is DONE:
                    # We stop reading the receive buffer while in state DONE
                    # so that pipelined requests can pile up without
                    # interfering with the current request/response. NB: we
                    # don't check for HTTP_MAX_BUFFER_SIZE in this state.
                    break
                if self._reader is None is self._receive_buffer:
                    raise ProtocolError(
                        "unexpectedly received data in state {}".format(state))
                event = self._reader(self._receive_buffer)
                if event is None:
                    if len(self._buf) > HTTP_MAX_BUFFER_SIZE:
                        # 414 is "Request-URI Too Long" which is not quite
                        # accurate because we'll also issue this if someone
                        # tries to send e.g. a megabyte of headers, but
                        # whatever.
                        raise ProtocolError("Receive buffer too long",
                                            error_status_hint=414)
                    break
            self._process_event(self._them, event)
            events.append(event)
        self._receive_buffer.compress()
        return events

    def send(self, event, *, combine=True):
        if type(event) is Response:
            _clean_up_response_headers_for_sending(
                event, response_to=self._request)
        # We want to process the event locally before actually sending it, so
        # that if processing it throws an error then nothing happens. But
        # processing it may change self._writer. So we save self._writer now
        # and then call it after. Special case: sending ConnectionClosed()
        # skips the writer.
        writer = self._writer
        self._process_event(self._us, event)
        if type(event) is ConnectionClosed:
            return None
        else:
            data_list = []
            writer(event, data_list.append)
            if combine:
                return b"".join(data_list)
            else:
                return data_list

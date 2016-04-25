import io

from _http_parser import HttpParser

# backpressure for pipelining?
# it sounds like pipelining is just a bad idea that will never be used or
# supported.
#   https://www.chromium.org/developers/design-documents/network-stack/http-pipelining

# basically what we want to do on the server side is make it an error to feed
# any data into receive_data if the connection is not in a state where it's
# receiving stuff.
# this is like the super simple version of http/2's flow control rules

# states we need to care about:
#   connection dead (closed, errored)
#   transfer-encoding
#   tracking switch from one request/response pair to another
#     which means tracking whether we've read all of the request body

# keep-alive:
#

# header data limit: count all url-data, header-field / header-value bytes,
#   and error out if they get too big

# in the high-level stuff, have helpers like:
#   async def read_json(body_iter, max_size)
#   async def read_form_data(body_iter, max_size)
# where the max_size is a mandatory parameter -- if you want arbitrary size
# then you have to use the streaming API.

# state:
#   WAIT
#     |
#     message-begin
#     |
#   FIRST-LINE
#     |
#     url-data
#     |
#   HEADER-FIELD  -- header-field, header-value
#     |      |
#   HEADER-VALUE
#     |
#     headers-complete
#     |
#   BODY  -- body-data
#   | |
#   | header-field / header-value
#   | |
#   | TRAILING-HEADER-{FIELD,VALUE}
#   | |
#   message-complete
#   |
#   WAIT
#

# rule:
# - we can yield None, in which case we'll get sent back the next event
# - we can yield something else, in which case we'll get sent back None

def valid_event_types(event_types, event):
    if event[0] not in event_types:
        raise ValueError("expected event of type {}, not {}"
                         .format(" or ".join(event_type), event[0]))

# must see at least one event_type or fails
def collect_data(event_type, event):
    valid_event_types([event_type], event)
    data = io.BytesIO()
    while event[0] == event_type:
        data.write(event[-1])
        event = (yield)
    # returns data + next event
    return data.getvalue(), event

def decode_headers(event):
    headers = []
    # loop over headers
    while True:
        if event[0] != "header-field-data":
            return headers, event
        field, event = yield from collect_data("header-field-data", event)
        value, event = yield from collect_data("header-value-data", event)
        headers.append((field, value))

def decode_message():
    event = (yield)
    valid_event_types(["message-begin"], event)
    url, event = yield from collect_data("url-data", event)
    headers, event = yield from decode_headers(event)
    valid_event_types(["headers-complete"], event)
    _, headers_complete_info = event
    yield HeadersComplete(headers, headers_complete_info)
    del headers
    event = (yield)
    while event[0] == "body-data":
        yield BodyData(event[-1])
    yield EndOfBody()
    # body is followed by optional trailing headers
    if event[0] == "header-field-data":
        trailing_headers, event = yield from decode_headers(event)
        yield TrailingHeaders(trailing_headers)
        del trailing_headers
    # and then either end-of-message or upgrade
    valid_event_types(["end-of-message", "upgraded"])
    if event[0] == "end-of-message":
        yield EndOfMessage(event[-1])
    else:
        yield Upgraded(event[-1])

class ParserDriver:
    def __init__(self, *, client_side):
        self.parser = HttpParser(client_side=client_side)
        self.decoder = decode_message()

    def event_recieved(self, in_event):
        out_events = []
        result = self.decoder.send(in_event)
        while result is not None:
            out_events.append(result)
            result = next(self.decoder)
        return out_events

class _Machine:
    def __init__(self, *,
                 initial,
                 states,
                 transitions,
                 data_accumulation_states,
                 callback_obj,
                 ):
        self.state = initial
        self.states = states
        self.transitions = transitions
        self.data_accumulation_states = data_accumulation_states
        self.callback_obj = callback_obj

    def receive_event(self, in_event):
        in_event_type = in_event[0]
        state_transitions = self.transitions[self._state]
        if in_event_type not in state_transitions:
            raise ValueError("event {} illegal in state {}"
                             .format(in_event_type, self._state))
        new_state = state_transitions[in_event_type]
        out_events = []
        if new_state is not None and new_state != self.state:
            out_events.extend(self._exit(self.state, in_event))
            out_events.extend(self._enter(new_state, in_event))
            self.state = new_state


class _HeaderDecoder:
    states = set("HEADER-FIELD HEADER-VALUE DONE".split())
    transitions = {
        "HEADERS-WAIT": {
            "header-field-data": "HEADER-FIELD",
        },
        "HEADER-FIELD": {
            "header-field-data": None,
            "header-value-data": "HEADER-VALUE",
        },
        "HEADER-VALUE": {
            "header-field-data": "HEADER-FIELD",
            "header-value-data": None,
            "done": "DONE",
        },
    data_accumulation_states = set("HEADER-FIELD HEADER-VALUE".split())

    def __init__(self):
        self._machine = _HttpMachine(
            initial="HEADERS-WAIT",
            states=self.states,
            transitions=self.transitions,
            data_accumulation_states=data_accumulation_states,
            callback_obj=self)


class _HttpMachine:
    states = set("IDLE URL HEADER-FIELD HEADER-VALUE BODY "
                 "TRAILING-HEADER-FIELD TRAILING-HEADER-VALUE "
                 "UPGRADED"
                 .split())

    transitions = {
        "IDLE": {
            "message-begin": "URL",
        },
        "URL": {
            "url-data": None,
            "header-field-data": "HEADER-FIELD",
        },
        "HEADER-FIELD": {
            "header-field-data": None,
            "header-value-data": "HEADER-VALUE",
        },
        "HEADER-VALUE": {
            "header-field-data": "HEADER-FIELD",
            "header-value-data": None,
            "headers-complete": "BODY",
        },
        "BODY": {
            "body-data": None,
            "header-field-data": "TRAILING-HEADER-FIELD",
            "message-complete": "IDLE",
            "upgraded": "UPGRADED",
        },
        # Probably there is some clever way to reduce duplication between
        # leading and trailing headers, but for now this works.
        "TRAILING-HEADER-FIELD": {
            "header-field-data": None,
            "header-value-data": "TRAILING-HEADER-VALUE",
        },
        "TRAILING-HEADER-VALUE": {
            "header-field-data": "TRAILING-HEADER-FIELD",
            "header-value-data": None,
            "message-complete": "IDLE",
            "upgraded": "UPGRADED",
        },
        "UPGRADED": {
            # absorbing state
        },
    }

    # These states get special-cased becaues they all have the same behavior:
    # - on enter, creates a BytesIO
    # - on None transitions, write the event data to this BytesIO
    # - on exit, store the data somewhere and throw away the BytesIO
    data_accumulation_states = {
        "URL",
        "HEADER-FIELD", "HEADER-VALUE",
        # not body, body data gets streamed out
        "TRAILING-HEADER-FIELD", "TRAILING-HEADER-VALUE",
    }

    def __init__(self):
        self._state = "IDLE"
        #
        self._data_accumulator = None
        self._outbox = []

    def receive_event(self, in_event):
        in_event_type = in_event[0]
        state_transitions = self.transitions[self._state]
        if in_event_type not in state_transitions:
            raise ValueError("event {} illegal in state {}"
                             .format(in_event_type, self._state))
        new_state = state_transitions[in_event_type]

        # transition, and do transition callbacks
        # things that might need to happen:
        # - do something with event data
        # - clean up old state data
        # - mutate current state data (on None transitions)
        # - initialize new state data
        # - report back a high-level event

        outbox = self._outbox
        self._outbox = []
        return outbox

    def _emit(self, out_event):
        self._outbox.append(out_event)

    def _enter(self, state, in_event):
        if state in self.data_accumulation_states:
            self._data_accumulator = io.BytesIO()
            self._data_accumulator.write(in_event[-1])
        getattr(self, "enter_" + state, lambda x: None)(in_event)

    def _exit(self, state, in_event):
        getattr(self, "exit_" + state, lambda x: None)(in_event)
        if state in self.data_accumulation_states:
            self._data_accumulator = None

    def enter_IDLE(self, _):
        self._url = None
        self._headers = []
        self._next_header_field = None

    def exit_URL(self, _):
        self._url = self._data_accumulator.getvalue()

    def exit_HEADER_FIELD(self, _):
        assert self._next_header_field is None
        self._next_header_field = self._data_accumulator.getvalue()

    def exit_HEADER_VALUE(self, _):
        assert self._next_header_field is not None
        header = (self._next_header_field, self._data_accumulator().getvalue())
        self._headers.append(header)
        self._next_header_field = None

    def enter_BODY(self, in_event):
        assert in_event[0] == "headers-complete"
        XX emit something

    XX body self transitions need to emit

    exit_TRAILING_HEADER_FIELD = exit_HEADER_FIELD
    exit_TRAILING_HEADER_VALUE = exit_HEADER_VALUE

    def enter_UPGRADE(self, in_event):
        assert in_event[0] == "upgraded"
        self._emit(XX upgraded event)

class HttpConnection:
    def __init__(self, *, client_side):
        self._client_side = client_side
        if self._client_side:
            raise NotImplementedError(
                "only server side is implemented so far")
        mode = "response" if self._client_side else "request"
        # XX FIXME: need to do something about header_only handling for
        # client-side HEAD requests...
        self._parser = HttpParser(mode=mode)

    def receive_data(self, data):
        # may throw HttpParseError
        self._parser.feed(data)

        for event in self._parser.events:
            event_type = event[0]

        # returns list of new events


    def data_to_send(self, amt=None):
        XX

    # special headers:
    #   :status, :path, :method, :scheme, :authority
    # as a server you can call send_headers:
    #   0 or more times with :status 1XX
    #   once with any other :status header
    #   zero or one time for trailers
    # clients send one request block, and optionally one trailer block
    def send_headers(self, headers):
        # encode to UTF-8
        XX

    def set_headers_only(self):
        # or should send_headers with :method HEAD do this automatically?
        XX

    def send_data(self, data, end_body=False):
        XX

    def send_headers_and_data(self, headers, data):
        XX

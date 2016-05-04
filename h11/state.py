from .events import *
from .util import ProtocolError, Sentinel

# Everything in __all__ gets re-exported as part of the h11 public API.
__all__ = []

sentinels = ("CLIENT SERVER "
             "IDLE SEND_RESPONSE SEND_BODY DONE MUST_CLOSE CLOSED "
             "MIGHT_SWITCH_PROTOCOL SWITCHED_PROTOCOL").split()
for token in sentinels:
    globals()[token] = Sentinel(token)

__all__ += sentinels

# Rule 1: everything that affects the state machine and state transitions must
# live here in this file. As much as possible goes into the FSA
# representation, but for the bits that don't quite fit, the actual code and
# state must nonetheless live here.
#
# Rule 2: this file does not know about what role we're playing; it only knows
# about HTTP request/response cycles in the abstract. This ensures that we
# don't cheat and apply different rules to local and remote parties.

EVENT_TRIGGERED_TRANSITIONS = {
    CLIENT: {
        IDLE: {
            Request: SEND_BODY,
            ConnectionClosed: CLOSED,
        },
        SEND_BODY: {
            Data: SEND_BODY,
            EndOfMessage: DONE,
        },
        DONE: {
            ConnectionClosed: CLOSED,
        },
        MUST_CLOSE: {
            ConnectionClosed: CLOSED,
        },
        CLOSED: {
            ConnectionClosed: CLOSED,
        },
        MIGHT_SWITCH_PROTOCOL: {},
        SWITCHED_PROTOCOL: {},
    },

    SERVER: {
        IDLE: {
            ConnectionClosed: CLOSED,
            # Special case triggered by CLIENT -- see discussion below:
            Request: SEND_RESPONSE,
            # This is needed solely to allow for 400 Bad Request responses to
            # requests that we errored out on, and thus never made it through
            # the state machine.
            Response: SEND_BODY,
        },
        SEND_RESPONSE: {
            InformationalResponse: SEND_RESPONSE,
            Response: SEND_BODY,
        },
        SEND_BODY: {
            Data: SEND_BODY,
            EndOfMessage: DONE,
        },
        DONE: {
            ConnectionClosed: CLOSED,
        },
        MUST_CLOSE: {
            ConnectionClosed: CLOSED,
        },
        CLOSED: {
            ConnectionClosed: CLOSED,
        },
        SWITCHED_PROTOCOL: {},
    },
}

# NB: there are also some special-case state-triggered transitions hard-coded
# into _fire_state_triggered_transitions below.
STATE_TRIGGERED_TRANSITIONS = {
    # (Client state, Server state) -> (new Client state, new Server state)
    # Protocol negotiation
    (MIGHT_SWITCH_PROTOCOL, SWITCHED_PROTOCOL):
        (SWITCHED_PROTOCOL, SWITCHED_PROTOCOL),
    (MIGHT_SWITCH_PROTOCOL, SEND_BODY): (DONE, SEND_BODY),
    # Socket shutdown
    (CLOSED, DONE): (CLOSED, MUST_CLOSE),
    (CLOSED, IDLE): (CLOSED, MUST_CLOSE),
    (DONE, CLOSED): (MUST_CLOSE, CLOSED),
    (IDLE, CLOSED): (MUST_CLOSE, CLOSED),
}

class ConnectionState:
    def __init__(self):
        # Extra bits of state that don't quite fit into the state model.

        # If this is False then it enables the automatic DONE -> MUST_CLOSE
        # transition. The only place this setting can change is when seeing a
        # Request or a Response (so in IDLE or SEND_RESPONSE), so changes in
        # it can never trigger a state transition -- we only need to check for
        # it when entering DONE.
        self.keep_alive = True

        # The client Request might suggest switching protocols, but even if
        # this takes effect it doesn't happen until after the client has
        # finished sending their full message. So we need some way to carry
        # that information forward from the Request to the EndOfMessage, and
        # that's what this variable is for. If True, it enables the automatic
        # DONE -> MIGHT_SWITCH_PROTOCOL transition for the client only, and
        # then unsets itself.
        #
        # The only place this setting can change is when seeing a Request, so
        # the client cannot already be in DONE when it is set.
        self.client_requested_protocol_switch_pending = False

        self.states = {CLIENT: IDLE, SERVER: IDLE}

    def process_event(self, role, event_type, server_switched_protocol):
        self._fire_event_triggered_transitions(role, event_type)
        # Special case: the server state does get to see Request events. This
        # is necessary because a server in a hasn't-recieved-a-request state
        # is different from one in a has-received-a-request state. For example:
        # - if we've haven't received a Request, closing our end is fine; if
        #   we have, then it's a protocol error
        # - if we haven't received a Request, and the remote end closes, then
        #   we will never receive a Request so we should go to MUST_CLOSE. If
        #   we have received a Request and the remote end closes, we should
        #   continue responding as normal.
        # - if we send a Response (e.g 400 can't parse your request), and then
        #   get a Request, then something has gone horrible wrong and we
        #   should raise an error.
        # XX FIXME: if this second transition errors out, then the first
        # transition currently does *not* get unwound -- is this a problem?
        if event_type is Request:
            assert role is CLIENT
            self._fire_event_triggered_transitions(SERVER, event_type)
        self._fire_state_triggered_transitions(server_switched_protocol)

    def _fire_event_triggered_transitions(self, role, event_type):
        state = self.states[role]
        try:
            new_state = EVENT_TRIGGERED_TRANSITIONS[role][state][event_type]
        except KeyError:
            raise ProtocolError(
                "can't handle event type {} for {} in state {}"
                .format(event_type.__name__, role, self.states[role]))
        self.states[role] = new_state

    def _fire_state_triggered_transitions(self, server_switched_protocol):
        # We apply these rules repeatedly until converging on a fixed point
        while True:
            start_states = dict(self.states)

            # Special cases that don't fit into the FSA formalism.
            # If you change these, make sure to also update _make_dot below.

            if server_switched_protocol:
                assert self.states[SERVER] in (SEND_RESPONSE, SEND_BODY)
                self.states[SERVER] = SWITCHED_PROTOCOL
                server_switched_protocol = False

            # It could happen that both these special-case transitions are
            # enabled at the same time:
            #
            #    DONE -> MIGHT_SWITCH_PROTOCOL
            #    DONE -> MUST_CLOSE
            #
            # For example, this will always be true of a HTTP/1.0 client
            # requesting CONNECT.  If this happens, the protocol switch takes
            # priority. From there the client will either go to
            # SWITCHED_PROTOCOL, in which case it's none of our business when
            # they close the connection, or else the server will deny the
            # request, in which case the client will go back to DONE and then
            # from there to MUST_CLOSE.

            if self.client_requested_protocol_switch_pending:
                if self.states[CLIENT] is DONE:
                    self.states[CLIENT] = MIGHT_SWITCH_PROTOCOL
                    self.client_requested_protocol_switch_pending = False

            if not self.keep_alive:
                for r in (CLIENT, SERVER):
                    if self.states[r] is DONE:
                        self.states[r] = MUST_CLOSE

            # State-triggered transitions
            old = (self.states[CLIENT], self.states[SERVER])
            new = STATE_TRIGGERED_TRANSITIONS.get(old, old)
            (self.states[CLIENT], self.states[SERVER]) = new

            if self.states == start_states:
                # Fixed point reached
                return

    @property
    def can_reuse(self):
        if not self.keep_alive:
            # We will definitely end up in MUST_CLOSE; DONE is unreachable
            return "never"
        states = set(self.states.values())
        doomed_states = {MUST_CLOSE, CLOSED, SWITCHED_PROTOCOL}
        if states.intersection(doomed_states):
            return "never"
        if states == {DONE}:
            return "now"
        return "maybe-later"

    def prepare_to_reuse(self):
        if self.can_reuse != "now":
            raise ProtocolError("not in a reusable state")

        assert self.states == {CLIENT: DONE, SERVER: DONE}
        assert self.keep_alive
        assert not self.client_requested_protocol_switch_pending

        self.states = {CLIENT: IDLE, SERVER: IDLE}


_EVENT_COLOR = "#002092"
_STATE_COLOR = "#017517"
_SPECIAL_COLOR = "#7600a1"
def _make_dot(role, out_path):
    with open(out_path, "w") as f:
        f.write(
"""digraph {
  graph [fontname = "Lato"]
  node [fontname = "Lato"]
  edge [fontname = "Lato"]

  IDLE [label=<IDLE<BR/><i>start state</i>>]
""")

        def edge(source, target, label, color, italicize=False, weight=1):
            if italicize:
                quoted_label = "<<i>{}</i>>".format(label)
            else:
                quoted_label = '"{}"'.format(label)
            f.write(
                '{source} -> {target} [\n'
                '  label={quoted_label},\n'
                '  color="{color}", fontcolor="{color}",\n'
                '  weight={weight},\n'
                ']\n'
                .format(**locals()))

        CORE_EVENTS = {Request, InformationalResponse,
                       Response, Data, EndOfMessage}

        for (source_state, t) in EVENT_TRIGGERED_TRANSITIONS[role].items():
            for (event_type, target_state) in t.items():
                weight = 1
                color = _EVENT_COLOR
                if (event_type in CORE_EVENTS
                    and source_state is not target_state):
                    weight = 10
                # exception
                if (event_type is Response and source_state is IDLE):
                    weight = 1
                if role is SERVER and event_type is Request:
                    # The weird special case
                    color = _SPECIAL_COLOR
                    weight = 5
                edge(source_state, target_state, event_type.__name__,
                     color, weight=weight)

        for source_pair, target_pair in STATE_TRIGGERED_TRANSITIONS.items():
            if role is CLIENT:
                (our_source, their_source) = source_pair
                (our_target, their_target) = target_pair
            else:
                (their_source, our_source) = source_pair
                (their_target, our_target) = target_pair
            if our_source is our_target:
                continue
            edge(our_source, our_target,
                 "peer in {}".format(their_source),
                 _STATE_COLOR)

        if role is CLIENT:
            edge(DONE, MIGHT_SWITCH_PROTOCOL,
                 "Upgrade: or <BR/>CONNECT",
                 _SPECIAL_COLOR,
                 italicize=True)
        else:
            edge(SEND_RESPONSE, SWITCHED_PROTOCOL, "101 Switching protocols",
                 _SPECIAL_COLOR, italicize=True)
            edge(SEND_BODY, SWITCHED_PROTOCOL, "2xx CONNECT",
                 _SPECIAL_COLOR, italicize=True)

        edge(DONE, MUST_CLOSE, "keep-alive<BR/>disabled", _SPECIAL_COLOR,
             italicize=True)
        edge(DONE, IDLE, "prepare_to_reuse()", _SPECIAL_COLOR,
             italicize=True)

        f.write("\n}\n")

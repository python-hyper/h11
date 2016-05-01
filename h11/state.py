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
        CLOSED: {},
        MIGHT_SWITCH_PROTOCOL: {},
        SWITCHED_PROTOCOL: {},
    },

    SERVER: {
        IDLE: {
            InformationalResponse: SEND_RESPONSE,
            Response: SEND_BODY,
            ConnectionClosed: CLOSED,
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
        CLOSED: {},
        SWITCHED_PROTOCOL: {},
    },
}

# NB: there are also some special-case state-triggered transitions hard-coded
# into _fire_state_triggered_transitions below.
STATE_TRIGGERED_TRANSITIONS = {
    # (Client state, Server state) -> (new Client state, new Server state)
    (MIGHT_SWITCH_PROTOCOL, SWITCHED_PROTOCOL):
        (SWITCHED_PROTOCOL, SWITCHED_PROTOCOL),
    (MIGHT_SWITCH_PROTOCOL, SEND_BODY): (DONE, SEND_BODY),
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

        # If this is True, then it enables the automatic DONE ->
        # MIGHT_SWITCH_PROTOCOL transition for the client only. The only place
        # this setting can change is when seeing a Request, so the client
        # cannot already be in DONE when it is set.
        self.client_requested_protocol_switch = False

        self.states = {CLIENT: IDLE, SERVER: IDLE}

    def process_event(self, role, event_type, server_switched_protocol):
        # Handle event-triggered transitions
        state = self.states[role]
        try:
            new_state = EVENT_TRIGGERED_TRANSITIONS[role][state][event_type]
        except KeyError:
            raise ProtocolError(
                "can't handle event type {} for {} in state {}"
                .format(event_type.__name__, role, self.states[role]))
        self.states[role] = new_state

        self._fire_state_triggered_transitions(server_switched_protocol)

    def _fire_state_triggered_transitions(self, server_switched_protocol):
        # We apply these rules repeatedly until converging on a fixed point
        while True:
            start_states = dict(self.states)

            # Special cases that don't fit into the FSA formalism

            if server_switched_protocol:
                assert role is SERVER
                assert self.states[SERVER] in (SEND_RESPONSE, SEND_BODY)
                self.states[SERVER] = SWITCHED_PROTOCOL

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

            if self.client_requested_protocol_switch:
                if self.states[CLIENT] is DONE:
                    self.states[CLIENT] = MIGHT_SWITCH_PROTOCOL

            if not self.keep_alive:
                for r in (CLIENT, SERVER):
                    if self.states[r] is DONE:
                        self.states[r] = MUST_CLOSE

            # State-triggered transitions
            old_states = (self.states[CLIENT], self.states[SERVER])
            new_states = STATE_TRIGGERED_TRANSITIONS.get(old_states, old_states)
            (self.states[CLIENT], self.states[SERVER]) = new_states

            if self.states == start_states:
                # Fixed point reached
                return

    @property
    def can_reuse(self):
        if not self.keep_alive:
            # We will definitely end up in MUST_CLOSE; DONE is unreachable
            return "never"
        states = {m.state for m in self._machines.values()}
        doomed_states = {MUST_CLOSE, CLOSED}
        if states.intersection(doomed_states):
            return "never"
        if states == {DONE}:
            return "now"
        return "maybe-later"

    def prepare_to_reuse(self):
        if self.can_reuse != "now":
            raise ProtocolError("not in a reusable state")
        for machine in self._machines.values():
            assert machine.state is DONE
            machine.state = IDLE
        assert self.keep_alive
        assert not self.client_requested_protocol_switch

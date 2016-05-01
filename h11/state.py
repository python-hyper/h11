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

# Convention:
#
# Both machines see all events (i.e., client machine sees both client and
# server events, server machine sees both client and server events). But their
# default handling of an event depends on whose event it is: if the client
# machine sees a client event that it has no transition for, that's an
# error. But if the client machine sees a server event that it has no
# transition for, then that's ignored. And similarly for the server.
class RoleMachine:
    def __init__(self, role, initial_state, transitions):
        self.role = role
        self.state = initial_state
        self.transitions = transitions

    # Returns True if state changed
    def process_event(self, event_type):
        old_state = self.state
        new_state = self.transitions[self.state].get(event_type)
        if new_state is None:
            raise ProtocolError(
                "illegal event type {} for {} in state {}"
                .format(event_type.__name__, self.role, self.state))
        self.state = new_state
        return (old_state is not new_state)

# Rule 1: everything that affects state and state transitions must live
# here. As much as possible goes into the FSA representation, but for the bits
# that don't quite fit, the actual code and state must nonetheless live here.
#
# Rule 2: this class does not know about what role we're playing; it only
# knows about HTTP request/response cycles in the abstract. This ensures that
# we don't cheat and apply different rules to local and remote parties.
class ConnectionState:
    def __init__(self):
        # Extra bits of state that don't quite fit into the state model.

        # If this is False then it enables the automatic DONE -> MUST_CLOSE
        # transition. The only place this can change is when seeing a Request
        # or a Response (so in IDLE or SEND_RESPONSE), so changes in it can
        # never trigger a state transition -- we only need to check for it
        # when entering DONE.
        self.keep_alive = True

        # If this is True, then it enables the automatic DONE ->
        # MIGHT_SWITCH_PROTOCOL transition for the client only. The only
        # place this can change is when seeing a Request, so the client cannot
        # already be in DONE when it is set.
        self.client_requested_protocol_switch = False

        # The state machines
        self._machines = {
            CLIENT: RoleMachine(
                role=CLIENT,
                initial_state=IDLE,
                transitions={
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
                }),
            SERVER: RoleMachine(
                role=SERVER,
                initial_state=IDLE,
                transitions={
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
                }),
        }

    def state(self, role):
        return self._machines[role].state

    # Returns set of parties who entered a new state
    def process_event(self, role, event_type, server_switched_protocol):
        state_changes = set()
        machine = self._machines[role]

        if machine.process_event(event_type):
            state_changes.add(role)

        if server_switched_protocol:
            assert role is SERVER
            assert machine.state in (SEND_RESPONSE, SEND_BODY)
            machine.state = SWITCHED_PROTOCOL
            state_changes.add(role)

        if state_changes:
            # Check for state-based transitions
            if self.state(CLIENT) is MIGHT_SWITCH_PROTOCOL:
                server_state = self.state(SERVER)

                if server_state is SWITCHED_PROTOCOL:
                    self._machines[CLIENT].state = SWITCHED_PROTOCOL
                    state_changes.add(CLIENT)

                # This can put us in DONE, so it should come before the checks
                # below that can trigger on DONE
                if server_state is SEND_BODY:
                    self._machines[CLIENT].state = DONE
                    state_changes.add(CLIENT)

            if (role is CLIENT and machine.state is DONE
                and self.client_requested_protocol_switch):
                machine.state = MIGHT_SWITCH_PROTOCOL
                self.client_requested_protocol_switch = False

            if machine.state is DONE and not self.keep_alive:
                machine.state = MUST_CLOSE

            # If at any point, one peer is DONE or IDLE and the other is CLOSED,
            # then the DONE/IDLE peer goes to MUST_CLOSE
            for a, b in [(CLIENT, SERVER), (SERVER, CLIENT)]:
                if self.state(a) is CLOSED and self.state(b) in (DONE, IDLE):
                    self._machines[b].state = MUST_CLOSE
                    state_changes.add(b)

        return state_changes

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

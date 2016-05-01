from .events import *
from .util import ProtocolError, Sentinel

__all__ = ["ConnectionState"]

sentinels = ("CLIENT SERVER"
             "IDLE SEND_RESPONSE SEND_BODY DONE MUST_CLOSE CLOSED").split()
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
    def process_event(self, role, event):
        old_state = self.state
        key = (role, type(event))
        new_state = self.transitions[self.state].get(key)
        if new_state is None:
            if role is not self.role:
                new_state = old_state
            else:
                raise ProtocolError(
                    "illegal event {} in state {}:{}"
                    .format(key, self.role, self.state))
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
        # An extra bit of state that doesn't quite fit into the model. If this
        # is False then it means enables the automatic DONE -> MUST_CLOSE
        # transition. The only place this can change is when seeing a Request
        # or a Response (so in IDLE or SEND_RESPONSE), so changes in it can
        # never trigger a state transition -- we only need to check for it
        # when entering DONE.
        self.keep_alive = True
        # The state machines
        self._machines = {
            CLIENT: RoleMachine(
                role=CLIENT,
                initial_state=IDLE,
                transitions={
                    IDLE: {
                        (CLIENT, Request): SEND_BODY,
                        (CLIENT, ConnectionClosed): CLOSED,
                    },
                    SEND_BODY: {
                        (CLIENT, Data): SEND_BODY,
                        (CLIENT, EndOfMessage): DONE,
                    },
                    DONE: {
                        (CLIENT, ConnectionClosed): CLOSED,
                    },
                    MUST_CLOSE: {
                        (CLIENT, ConnectionClosed): CLOSED,
                    },
                    CLOSED: {},
                }),
            SERVER: RoleMachine(
                role=SERVER,
                initial_state=IDLE,
                transitions={
                    IDLE: {
                        (CLIENT, Request): SEND_RESPONSE,
                        (SERVER, ConnectionClosed): CLOSED,
                    },
                    SEND_RESPONSE: {
                        (SERVER, InformationalResponse): SEND_RESPONSE,
                        (SERVER, Response): SEND_BODY,
                    },
                    SEND_BODY: {
                        (SERVER, Data): SEND_BODY,
                        (SERVER, EndOfMessage): DONE,
                    },
                    DONE: {
                        (SERVER, ConnectionClosed): CLOSED,
                    },
                    MUST_CLOSE: {
                        (SERVER, ConnectionClosed): CLOSED,
                    },
                    CLOSED: {},
                }),
        }

    def state(self, role):
        return self._machines[role].state

    # Returns set of parties who entered a new state
    def process_event(self, role, event):
        state_changes = set()
        for machine in self._machines.values():
            if machine.process_event(role, event):
                state_changes.add(machine.role)
                # If a machine is in DONE and self.must_close is set, then it
                # jumps straight to MUST_CLOSE
                if machine.state is DONE and not self.keep_alive:
                    machine.state = MUST_CLOSE
        # If at any point, one peer is DONE or IDLE and the other is CLOSED,
        # then the DONE/IDLE peer goes to MUST_CLOSE
        if state_changes:
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

    def prepare_for_reuse(self):
        if self.can_reuse != "now":
            raise ProtocolError("not in a reusable state")
        for machine in self._machines.values():
            assert machine.state is DONE
            machine.state = IDLE

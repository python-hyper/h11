from .events import *
from .util import ProtocolError, Sentinel

__all__ = ["ConnectionState"]

sentinels = "CLIENT SERVER IDLE SEND_RESPONSE SEND_BODY DONE CLOSED".split()
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
class PartyMachine:
    def __init__(self, party, initial_state, transitions):
        self.party = party
        self.state = initial_state
        self.transitions = transitions

    # Returns True if state changed
    def process_event(self, party, event):
        old_state = self.state
        key = (party, type(event))
        new_state = self.transitions[self.state].get(key)
        if new_state is None:
            if party is not self.party:
                new_state = old_state
            else:
                raise ProtocolError(
                    "illegal event {} in state {}:{}"
                    .format(key, self.party, self.state))
        self.state = new_state
        return (old_state is not new_state)

class ConnectionState:
    def __init__(self):
        self._machines = {
            CLIENT: PartyMachine(
                party=CLIENT,
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
                    CLOSED: {},
                }),
            SERVER: PartyMachine(
                party=SERVER,
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
                    CLOSED: {},
                }),
        }

    def state(self, party):
        return self._machines[party].state

    def reset(self, new_state):
        state_changes = []
        for machine in self._machines.values():
            if machine.state != new_state:
                machine.state = new_state
                state_changes.append(machine.party)
        return state_changes

    # Returns set of parties who entered a new state
    def process_event(self, party, event):
        state_changes = set()
        for machine in self._machines.values():
            if machine.process_event(party, event):
                state_changes.add(machine.party)
        return state_changes

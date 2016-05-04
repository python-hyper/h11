from ..events import *
from ..state import *
from ..connection import Connection

# Merges adjacent Data events, and converts payloads to bytestrings
def normalize_data_events(in_events):
    out_events = []
    for event in in_events:
        if type(event) is Data:
            event.data = bytes(event.data)
        if out_events and type(out_events[-1]) is type(event) is Data:
            out_events[-1].data += event.data
        else:
            out_events.append(event)
    return out_events

# Given that we want to write tests that push some events through a Connection
# and check that its state updates appropriately... we might as make a habit
# of pushing them through two Connections with a fake network link in
# between.
class ConnectionPair:
    def __init__(self):
        self.conn = {CLIENT: Connection(CLIENT), SERVER: Connection(SERVER)}
        self.other = {CLIENT: SERVER, SERVER: CLIENT}

    @property
    def conns(self):
        return self.conn.values()

    def send(self, role, event, expect_match=True):
        data = self.conn[role].send(event)
        events = self.conn[self.other[role]].receive_data(data)
        if expect_match:
            assert events == [event]
        return (data, events)

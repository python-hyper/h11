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

    # expect=None to disable checking, expect=[...] to say what expected
    def send(self, role, send_events, expect="match"):
        if not isinstance(send_events, list):
            send_events = [send_events]
        data = b""
        for send_event in send_events:
            data += self.conn[role].send(send_event)
        # send uses b"" to mean b"", and None to mean closed
        # receive uses b"" to mean closed, and None to mean "try again"
        # so we have to translate between the two conventions
        if data == b"":
            got_events = []
        else:
            if data is None:
                data = b""
            got_events = self.conn[self.other[role]].receive_data(data)
        if expect == "match":
            expect = send_events
        if expect is not None:
            if not isinstance(expect, list):
                expect = [expect]
            assert got_events == expect
        return data

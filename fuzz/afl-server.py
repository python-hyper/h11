# Invariant tested: No matter what random garbage a client throws at us, we
# either successfully parse it, or else throw a RemoteProtocolError, never any
# other error.

import os
import sys

import afl

import h11


def process_all(c):
    while True:
        event = c.next_event()
        if event is h11.NEED_DATA or event is h11.PAUSED:
            break
        if type(event) is h11.ConnectionClosed:
            break


afl.init()

data = sys.stdin.detach().read()

# one big chunk
server1 = h11.Connection(h11.SERVER)
try:
    server1.receive_data(data)
    process_all(server1)
    server1.receive_data(b"")
    process_all(server1)
except h11.RemoteProtocolError:
    pass

# byte at a time
server2 = h11.Connection(h11.SERVER)
try:
    for i in range(len(data)):
        server2.receive_data(data[i : i + 1])
        process_all(server2)
    server2.receive_data(b"")
    process_all(server2)
except h11.RemoteProtocolError:
    pass

# Suggested by the afl-python docs -- this substantially speeds up fuzzing, at
# the risk of missing bugs that would cause the interpreter to crash on
# exit. h11 is pure python, so I'm pretty sure h11 doesn't have any bugs that
# would cause the interpreter to crash on exit.
os._exit(0)

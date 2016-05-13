# Invariant:
#   No matter what random garbage a client throws at us, we either
#   successfully parse it, or else throw a ProtocolError, never any other
#   error.

import sys
import os

import afl

import h11

if sys.version_info[0] >= 3:
    in_file = sys.stdin.detach()
else:
    in_file = sys.stdin

afl.init()

data = in_file.read()

# one big chunk
server1 = h11.Connection(h11.SERVER)
try:
    server1.receive_data(data)
    server1.receive_data(b"")
except h11.ProtocolError:
    pass

# byte at a time
server2 = h11.Connection(h11.SERVER)
for i in range(len(data)):
    try:
        server2.receive_data(data[i:i + 1])
    except h11.ProtocolError:
        pass
try:
    server2.receive_data(b"")
except h11.ProtocolError:
    pass

# Suggested by the afl-python docs -- this substantially speeds up fuzzing, at
# the risk of missing bugs that would cause the interpreter to crash on
# exit. h11 is pure python, so I'm pretty sure h11 doesn't have any bugs that
# would cause the interpreter to crash on exit.
os._exit(0)

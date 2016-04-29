# Extra unfinished

import collections

import h11

class Connection:
    def __init__(self, sock, *, is_client=False):
        self._c = h11.Connection(is_client=is_client)
        self._sock = sock
        self._received = collections.deque()

    async def next_event(self):
        if self._received:
            return self._received.popleft()
        # if

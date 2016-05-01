# WIP

# Curio issues:
# - missing socket.shutdown
# - maybe a context manager for settimeout? maybe settimeout itself could be a
#   context manager?
#   - or at the very least, a way to read old timeout
#   - regular socket has gettimeout
# - in fact curio sockets are missing a ton of attributes
# - curio could support sendfile via os.sendfile (not socket.socket.sendfile),
#   that would be cute

# I think really what I want for a timeout manager is some ability to say
# "this whole section should time out after $TIMEOUT seconds", and then every
# time I block inside that section, the timeout on that operation is
# automagically set to ($TIMEOUT - (now - start)). (Or even better: min(that,
# any per-operation timeout).) With a bit of help from the curio kernel this
# could totally be implemented as an async context manager.

from contextlib import contextmanager
import socket
import curio
from async_generator import async_generator, yield_
import h11

@contextmanager
def timeout(sock, t):
    # Curio bug: need to access private variable to get current timeout
    old = sock._timeout
    try:
        sock.settimeout(t)
    finally:
        sock.settimeout(old)

class CurioHttpConnection(h11.Connection):
    def __init__(self, our_role, sock, *, max_recv=65536):
        super().__init__(our_role)
        self.__sock = sock
        self.__max_recv = max_recv

    async def send(self, *args, **kwargs):
        data = super().send(*args, **kwargs)
        if data is None:
            with self.__sock.blocking() as real_sock:
                # Curio bug: doesn't expose shutdown()
                real_sock.shutdown(socket.SHUT_WR)
        else:
            await self.__sock.sendall(data)

    async def get_remote_events(self):
        if self.they_are_waiting_for_100_continue:
            r = h11.InformationalResponse(status_code=100, headers=[])
            await self.send(r)
        data = await self.__sock.recv(self.__max_recv)
        # XX what happens after close? error out?
        return super().receive_data(data)

    @async_generator
    async def events(self, *, timeout):
        with timeout(self.__sock, timeout):
            while True:
                for event in (await self.get_remote_events()):
                    await yield_(event)
                    if type(event) in (h11.EndOfMessage, h11.Paused):
                        return


# Server:
# - make socket
# - set timeout
# - wait for Request event, invoke handler
# - handle exceptions (ProtocolError, TimeoutError)
#   sending 400 or whatever if possible
#     408 Request Timeout
# - check if connection can be re-used, and either handle shutdown logic or
#   loop around waiting for another Request (taking care to shut down idle
#   connections after some timeout)

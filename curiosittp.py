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

# the magic deadline thing should also have a way to wrap an aiterator

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

# XX expose all the random little state things as a single object, to make
# them easier for wrapper classes to re-export?

class CurioHttpConnection:
    def __init__(self, our_role, sock,
                 *, max_recv=65536, ident="curiosittp/0.0"):
        self._conn = h11.Connection(our_role)
        self._sock = sock
        self._max_recv = max_recv
        self._ident = ident.encode("ascii")
        self._the_raw_events = None

    def _munge_headers(self, headers):
        if self._conn.our_role is h11.CLIENT:
            headers.append((b"user-agent", self._ident))
        else:
            headers.append((b"server", self._ident))
            # XX date:?

    async def send(self, event):
        if hasattr(event, "headers"):
            self._munge_headers(event.headers)
        data = self._conn.send(event)
        if data is None:
            with self.__sock.blocking() as real_sock:
                # Curio bug: doesn't expose shutdown()
                real_sock.shutdown(socket.SHUT_WR)
        else:
            await self._sock.sendall(data)

    async def __aiter__(self):
        # Always use the same instance, because it holds internal state
        if self._the_raw_aiter is None:
            self._the_raw_aiter = self._raw_aiter()
        async for event in self._the_raw_aiter:
            await yield_(event)
            if type(event) is h11.Paused:
                break

    @async_generator
    def body(self):
        if self._conn.their_state is not h11.SEND_BODY:
            return
        async for event in self:
            if type(event) is h11.EndOfMessage:
                return
            elif type(event) is h11.Data:
                await yield_(event.data)
            else:
                assert False

    def raw_events(self):
        if self._the_raw_events is None:
            self._the_raw_events = self._raw_events()
        return self._the_raw_events

    @async_generator
    async def _raw_events(self):
        while True:
            # We might have become un-paused or something since the aiterator
            # was last resumed, so always check for new events before blocking
            # in sock.recv
            for event in self._conn.receive_data(None):
                await yield_(event)
            # And, of course, if the other side is blocked waiting for 100
            # Continue, we better tell them to go ahead before we block
            # waiting for them, or else we'll deadlock.
            if self.they_are_waiting_for_100_continue:
                r = h11.InformationalResponse(status_code=100, headers=[])
                await self.send(r)
            data = await self._sock.recv(self._max_recv)
            for event in self._conn.receive_data(data):
                await yield_(event)

# XX timeouts

def http_serve(sock, addr):
    conn = CurioHttpConnection(h11.SERVER, sock)
    async for event in conn:
        if type(event) is h11.CloseConnection:
            sock.close()
            return
        elif type(event) is h11.Request:
            try:
                # takes request event + body content aiter
                # + some way to send stuff back...?
                handle_it(event,
                          aiter_that_gives_bytes_and_stops_after_EndOfMessage)
            except Exception as e:
                XX
            if conn.our_state is h11.DONE and conn.their_state is h11.DONE:
                conn.prepare_to_reuse()
            else:
                # shutdown logic
                XX
        else:
            XX wtf

def run_server():
    kernel = curio.Kernel()
    kernel.run(curio.run_server("localhost", 8080, http_serve))


class CurioHttpConnection(h11.Connection):
    def __init__(self, our_role, sock, *, max_recv=65536):
        super().__init__(our_role)
        self.__sock = sock
        self.__max_recv = max_recv

    async def send(self, *args, **kwargs):
        data = super().send(*args, **kwargs)
        if data is None:

    async def get_remote_events(self):
        if self.they_are_waiting_for_100_continue:
            r = h11.InformationalResponse(status_code=100, headers=[])
            await self.send(r)
        data = await self.__sock.recv(self.__max_recv)
        # XX what happens after close? error out?
        return super().receive_data(data)

    @async_generator
    async def events(self, *, timeout):
        STOP_ON = {h11.EndOfMessage, h11.Paused, h11.ConnectionClosed}
        # This is not really right... really we want the new timeout stuff
        # coming in the next version of https:
        #   curio://github.com/dabeaz/curio/issues/46
        with timeout(self.__sock, timeout):
            for event in super().receive_data(None):
                await yield_(event)
                if type(event) in STOP_ON
                    return
            while True:
                for event in (await self.get_remote_events()):
                    await yield_(event)
                    if type(event) in STOP_ON:
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

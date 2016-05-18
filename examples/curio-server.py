# A simple HTTP server implemented using h11 and Curio:
#   http://curio.readthedocs.org/
# (so requires python 3.5+).
#
# All requests get echoed back a JSON document containing information about
# the request.

import socket
from collections import deque
from wsgiref.handlers import format_date_time
import json
from itertools import count

import curio
import h11

MAX_RECV = 2 ** 16
TIMEOUT = 10

class CurioServerTransport:
    _next_id = count()

    def __init__(self, sock):
        self.sock = sock
        self.conn = h11.Connection(h11.SERVER)
        self.ident = " ".join([
            "h11-demo-curio-server/{}".format(h11.__version__),
            h11.PRODUCT_ID,
            ]).encode("ascii")
        self.received = deque()
        self._obj_id = next(CurioServerTransport._next_id)

    async def send(self, event):
        # The code below doesn't send ConnectionClosed, so we don't bother
        # handling it here either -- it would require that we do something
        # appropriate when 'data' is None.
        assert type(event) is not h11.ConnectionClosed
        data = self.conn.send(event)
        await self.sock.sendall(data)

    def basic_headers(self):
        # Required in all responses:
        return [
            ("Date", format_date_time(None).encode("ascii")),
            ("Server", self.ident),
        ]

    async def receive_next_event(self):
        while True:
            if self.received:
                return self.received.popleft()
            # In case we just got un-paused, check for new events before doing
            # a blocking read
            self.received.extend(self.conn.receive_data(None))
            if self.received:
                return self.received.popleft()
            # And if the client is blocked waiting for 100 Continue, we better
            # tell them to go ahead before we block waiting for them, or else
            # we'll deadlock.
            if self.conn.they_are_waiting_for_100_continue:
                go_ahead = h11.InformationalResponse(
                    status_code=100,
                    headers=self.basic_headers())
                await self.send(go_ahead)
            try:
                data = await self.sock.recv(MAX_RECV)
            except ConnectionError:
                # I don't know why BrokenPipeError, ConnectionResetError,
                # etc. are different from remote EOF, but apparently they
                # are. We just pretend the remote returned EOF.
                data = b""
            self.received.extend(self.conn.receive_data(data))

    async def shutdown_and_clean_up(self):
        # When this method is called, it's because we definitely want to kill
        # this connection, either as a clean shutdown or because of some kind
        # of error or loss-of-sync bug, and we don't care if that violates the
        # protocol or not. So we ignore the state of self.conn, and just go
        # ahead and do the shutdown on the socket directly.
        # Curio bug: doesn't expose shutdown()
        with self.sock.blocking() as real_sock:
            try:
                real_sock.shutdown(socket.SHUT_WR)
            except OSError:
                # Already closed, I guess
                return
        # Wait for a bit to give them a chance to see that we closed things, but
        # eventually give up and just close the socket.
        async with curio.ignore_after(TIMEOUT):
            try:
                while True:
                    # Attempt to read until EOF
                    got = await self.sock.recv(MAX_RECV)
                    if not got:
                        break
            finally:
                await self.sock.close()

    def info(self, *args):
        print(self._obj_id, *args)

async def send_simple_response(transport, status_code, content_type, body):
    transport.info("Sending", status_code,
                   "response with", len(body), "bytes")
    headers = transport.basic_headers()
    headers.append(("Content-Type", content_type))
    headers.append(("Content-Length", str(len(body))))
    res = h11.Response(status_code=status_code, headers=headers)
    await transport.send(res)
    await transport.send(h11.Data(data=body))
    await transport.send(h11.EndOfMessage())

async def maybe_send_error_response(transport, exc):
    # If we can't send an error, oh well, nothing to be done
    transport.info("trying to send error response...")
    if transport.conn.our_state not in {h11.IDLE, h11.SEND_RESPONSE}:
        transport.info("...but I can't, because our state is",
                       transport.conn.our_state)
        return
    if isinstance(exc, h11.RemoteProtocolError):
        status_code = exc.error_status_hint
    else:
        status_code = 500
    body = str(exc).encode("utf-8")
    await send_simple_response(transport,
                               status_code,
                               "text/plain; charset=utf-8",
                               body)

async def http_serve(sock, addr):
    transport = CurioServerTransport(sock)
    while True:
        assert transport.conn.states == {
            h11.CLIENT: h11.IDLE, h11.SERVER: h11.IDLE}
        transport.info("Entering server main loop; waiting for event")
        event = await transport.receive_next_event()
        transport.info("Server main loop got event:", event)
        if type(event) is h11.Request:
            try:
                await send_echo_response(transport, event)
            except Exception as exc:
                transport.info("Oops:", exc)
                await maybe_send_error_response(transport, exc)
        if transport.conn.our_state is h11.MUST_CLOSE:
            transport.info("connection is not reusable, so shutting down")
            await transport.shutdown_and_clean_up()
            return
        if transport.conn.states == {
                h11.CLIENT: h11.DONE, h11.SERVER: h11.DONE}:
            # Ready to re-use; loop back around
            transport.info("connection reusable and all is well, so looping")
            transport.conn.prepare_to_reuse()
        else:
            # something has gone wrong
            transport.info("Response handler left us with unexpected state",
                           transport.conn.states,
                           "bailing out")
            await maybe_send_error_response(
                transport,
                RuntimeError(
                    "unexpected states {}".format(transport.conn.states)))
            await transport.shutdown_and_clean_up()
            return

async def send_echo_response(transport, request):
    transport.info("Preparing echo response")
    response_json = {
        "method": request.method.decode("ascii"),
        "target": request.target.decode("ascii"),
        "headers": [(name.decode("ascii"), value.decode("ascii"))
                    for (name, value) in request.headers],
        "body": "",
    }
    while True:
        event = await transport.receive_next_event()
        if type(event) is h11.EndOfMessage:
            break
        assert type(event) is h11.Data
        response_json["body"] += event.data.decode("ascii")
    response_body_unicode = json.dumps(response_json,
                                       sort_keys=True,
                                       indent=4,
                                       separators=(",", ": "))
    response_body_bytes = response_body_unicode.encode("utf-8")
    await send_simple_response(transport,
                               200,
                               "application/json; charset=utf-8",
                               response_body_bytes)

if __name__ == "__main__":
    kernel = curio.Kernel()
    print("Listening on http://localhost:8080")
    kernel.run(curio.tcp_server("localhost", 8080, http_serve))

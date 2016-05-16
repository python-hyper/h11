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

import curio
import h11

MAX_RECV = 2 ** 16
TIMEOUT = 10

class CurioServerTransport:
    def __init__(self, sock):
        self.sock = sock
        self.conn = h11.Connection(h11.SERVER)
        self.ident = " ".join([
            "h11-demo-curio-server/{}".format(h11.__version__),
            h11.PRODUCT_ID,
            ]).encode("ascii")
        self.received = deque()

    async def send(self, event):
        data = self.conn.send(event)
        if data is None:
            with self.sock.blocking() as real_sock:
                # Curio bug: doesn't expose shutdown()
                real_sock.shutdown(socket.SHUT_WR)
        else:
            await self.sock.sendall(data)

    def basic_headers(self):
        return [("Server", self.ident),
                ("Date", format_date_time(None).encode("ascii")),
                ]

    async def receive_next_event(self):
        while True:
            if self.received:
                return self.received.pop_left()
            # In case we just got un-paused, check for new events before doing
            # a blocking read
            self.received.extend(self.conn.receive_data(None))
            if self.received:
                return self.received.pop_left()
            # And if the client is blocked waiting for 100 Continue, we better
            # tell them to go ahead before we block waiting for them, or else
            # we'll deadlock.
            if self.conn.they_are_waiting_for_100_continue:
                go_ahead = h11.InformationalResponse(
                    status_code=100,
                    headers=self.basic_headers())
                await self.send(go_ahead)
            data = await self.sock.recv(MAX_RECV)
            self.received.extend(self.conn.receive_data(data))

async def send_error_response(transport, exc):
    if isinstance(exc, h11.RemoteProtocolError):
        status_code = exc.error_status_hint
    else:
        status_code = 500
    body = str(exc).encode("utf-8")
    headers = transport.basic_headers()
    headers.append(("Content-Length", len(body)))
    headers.append(("Content-Type", "text/plain; charset=utf-8"))
    res = h11.Response(status_code=status_code, headers=headers)
    await transport.send(res)
    await transport.send(h11.Data(data=body))
    await transport.send(h11.EndOfMessage())

async def wait_for_shutdown(transport):
    assert transport.our_state is h11.CLOSED
    # Wait for a bit to give them a chance to see that we closed things, but
    # eventually give up and just close the socket.
    with curio.ignore_after(TIMEOUT):
        while transport.their_state is not h11.CLOSED:
            await transport.receive_next_event()

def handle_next_request(transport):
    while True:
        request = await conn.receive_next_event()
        if type(request) is h11.Paused:
            continue

def http_serve(sock, addr):
    transport = CurioServerTransport(sock)
    while True:
        assert transport.conn.states == {
            h11.CLIENT: h11.IDLE, h11.SERVER: h11.IDLE}


        event = await conn.receive_next_event()
        if type(event) is h11.Request:
            try:
                await respond(event, t)
            except Exception as exc:
                print("Oops:", exc)
                try_send_error(transport, exc)
        if transport.conn.our_state is h11.MUST_CLOSE:
            await transport.send(h11.ConnectionClosed())
        if transport.conn.our_state is h11.CLOSED:
            await wait_for_shutdown(transport)
            await sock.close()
            return
        if transport.conn.states == {
                h11.CLIENT: h11.DONE, h11.SERVER: h11.DONE}:
            # Ready to re-use; loop back around
            transport.conn.prepare_to_reuse()
        else:
            # something has gone wrong
            try_send_error(RuntimeError(
                "unexpected states {}".format(transport.conn.states)))

async def send_echo_response(request, transport):
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
    response_headers = transport.basic_headers()
    response_headers += [
        ("Content-Length", len(response_body_bytes)),
        ("Content-Type", "application/json; charset=utf-8"),
    ]
    await transport.send(
        h11.Response(status_code=200, headers=response_headers))
    await transport.send(h11.Data(data=response_body_bytes))
    await transport.send(h11.EndOfMessage())

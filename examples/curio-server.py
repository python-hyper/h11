# A simple HTTP server implemented using h11 and Curio:
#   http://curio.readthedocs.org/
# (so requires python 3.5+).
#
# All requests get echoed back a JSON document containing information about
# the request.
#
# This is a rather involved example, since it attempts to both be
# fully-HTTP-compliant and also demonstrate error handling.
#
# The main difference between an HTTP client and an HTTP server is that in a
# client, if something goes wrong, you can just throw away that connection and
# make a new one. In a server, you're expected to handle all kinds of garbage
# input and internal errors and recover with grace and dignity. And that's
# what this code does.
#
# I recommend pushing on it to see how it works -- e.g. watch what happens if
# you visit http://localhost:8080 in a webbrowser that supports keep-alive,
# hit reload a few times, and then wait for the keep-alive to time out on the
# server.
#
# Or try using curl to start a chunked upload and then hit control-C in the
# middle of the upload:
#
#    (for CHUNK in $(seq 10); do echo $CHUNK; sleep 1; done) \
#      | curl -T - http://localhost:8080/foo
#
# (Note that curl will send Expect: 100-Continue, too.)
#
# Or, heck, try letting curl complete successfully ;-).

# Some potential improvements, if you wanted to try and extend this to a real
# general-purpose HTTP server (and to give you some hints about the many
# considerations that go into making a robust HTTP server):
#
# - The timeout handling is rather crude -- we impose a flat 10 second timeout
#   on each request (starting from the end of the previous
#   response). Something finer-grained would be better. Also, if a timeout is
#   triggered we unconditionally send a 500 Internal Server Error; it would be
#   better to keep track of whether the timeout is the client's fault, and if
#   so send a 408 Request Timeout.
#
# - The error handling policy here is somewhat crude as well. It handles a lot
#   of cases perfectly, but there are corner cases where the ideal behavior is
#   more debateable. For example, if a client starts uploading a large
#   request, uses 100-Continue, and we send an error response, then we'll shut
#   down the connection immediately (for well-behaved clients) or after
#   spending TIMEOUT seconds reading and discarding their upload (for
#   ill-behaved ones that go on and try to upload their request anyway). And
#   for clients that do this without 100-Continue, we'll send the error
#   response and then shut them down after TIMEOUT seconds. This might or
#   might not be your preferred policy, though -- maybe you want to shut such
#   clients down immediately (even if this risks their not seeing the
#   response), or maybe you're happy to let them continue sending all the data
#   and wasting your bandwidth if this is what it takes to guarantee that they
#   see your error response. Up to you, really.
#
# - Another example of a debateable choice: if a response handler errors out
#   without having done *anything* -- hasn't started responding, hasn't read
#   the request body -- then this connection actually is salvagable, if the
#   server sends an error response + reads and discards the request body. This
#   code sends the error response, but it doesn't try to salvage the
#   connection by reading the request body, it just closes the
#   connection. This is quite possibly the best option, but again this is a
#   policy decision.
#
# - Our error pages always include the exception text. In real life you might
#   want to log the exception but not send that information to the client.
#
# - Our error responses perhaps should include Connection: close when we know
#   we're going to close this connection.
#
# - We don't support the HEAD method, but ought to.
#
# - We should probably do something cleverer with buffering responses and
#   TCP_CORK and suchlike.

import json
from itertools import count
from socket import SHUT_WR
from wsgiref.handlers import format_date_time

import curio
import h11

MAX_RECV = 2 ** 16
TIMEOUT = 10

################################################################
# I/O adapter: h11 <-> curio
################################################################

# The core of this could be factored out to be usable for curio-based clients
# too, as well as servers. But as a simplified pedagogical example we don't
# attempt this here.
class CurioHTTPWrapper:
    _next_id = count()

    def __init__(self, sock):
        self.sock = sock
        self.conn = h11.Connection(h11.SERVER)
        # Our Server: header
        self.ident = " ".join([
            "h11-example-curio-server/{}".format(h11.__version__),
            h11.PRODUCT_ID,
            ]).encode("ascii")
        # A unique id for this connection, to include in debugging output
        # (useful for understanding what's going on if there are multiple
        # simultaneous clients).
        self._obj_id = next(CurioHTTPWrapper._next_id)

    async def send(self, event):
        # The code below doesn't send ConnectionClosed, so we don't bother
        # handling it here either -- it would require that we do something
        # appropriate when 'data' is None.
        assert type(event) is not h11.ConnectionClosed
        data = self.conn.send(event)
        await self.sock.sendall(data)

    async def _read_from_peer(self):
        if self.conn.they_are_waiting_for_100_continue:
            self.info("Sending 100 Continue")
            go_ahead = h11.InformationalResponse(
                status_code=100,
                headers=self.basic_headers())
            await self.send(go_ahead)
        try:
            data = await self.sock.recv(MAX_RECV)
        except ConnectionError:
            # They've stopped listening. Not much we can do about it here.
            data = b""
        self.conn.receive_data(data)

    async def next_event(self):
        while True:
            event = self.conn.next_event()
            if event is h11.NEED_DATA:
                await self._read_from_peer()
                continue
            return event

    async def shutdown_and_clean_up(self):
        # When this method is called, it's because we definitely want to kill
        # this connection, either as a clean shutdown or because of some kind
        # of error or loss-of-sync bug, and we no longer care if that violates
        # the protocol or not. So we ignore the state of self.conn, and just
        # go ahead and do the shutdown on the socket directly. (If you're
        # implementing a client you might prefer to send ConnectionClosed()
        # and let it raise an exception if that violates the protocol.)
        #
        # Curio bug: doesn't expose shutdown()
        with self.sock.blocking() as real_sock:
            try:
                real_sock.shutdown(SHUT_WR)
            except OSError:
                # They're already gone, nothing to do
                return
        # Wait and read for a bit to give them a chance to see that we closed
        # things, but eventually give up and just close the socket.
        async with curio.ignore_after(TIMEOUT):
            try:
                while True:
                    # Attempt to read until EOF
                    got = await self.sock.recv(MAX_RECV)
                    if not got:
                        break
            finally:
                await self.sock.close()

    def basic_headers(self):
        # HTTP requires these headers in all responses (client would do
        # something different here)
        return [
            ("Date", format_date_time(None).encode("ascii")),
            ("Server", self.ident),
        ]

    def info(self, *args):
        # Little debugging method
        print("{}:".format(self._obj_id), *args)

################################################################
# Server main loop
################################################################

# General theory:
#
# If everything goes well:
# - we'll get a Request
# - our response handler will read the request body and send a full response
# - that will either leave us in MUST_CLOSE (if the client doesn't
#   support keepalive) or DONE/DONE (if the client does).
#
# But then there are many, many different ways that things can go wrong
# here. For example:
# - we don't actually get a Request, but rather a ConnectionClosed
# - exception is raised from somewhere (naughty client, broken
#   response handler, whatever)
#   - depending on what went wrong and where, we might or might not be
#     able to send an error response, and the connection might or
#     might not be salvagable after that
# - response handler doesn't fully read the request or doesn't send a
#   full response
#
# But these all have one thing in common: they involve us leaving the
# nice easy path up above. So we can just proceed on the assumption
# that the nice easy thing is what's happening, and whenever something
# goes wrong do our best to get back onto that path, and h11 will keep
# track of how successful we were and raise new errors if things don't work
# out.
async def http_serve(sock, addr):
    wrapper = CurioHTTPWrapper(sock)
    while True:
        assert wrapper.conn.states == {
            h11.CLIENT: h11.IDLE, h11.SERVER: h11.IDLE}

        try:
            async with curio.timeout_after(TIMEOUT):
                wrapper.info("Server main loop waiting for request")
                event = await wrapper.next_event()
                wrapper.info("Server main loop got event:", event)
                if type(event) is h11.Request:
                    await send_echo_response(wrapper, event)
        except Exception as exc:
            wrapper.info("Error during response handler:", exc)
            await maybe_send_error_response(wrapper, exc)

        if wrapper.conn.our_state is h11.MUST_CLOSE:
            wrapper.info("connection is not reusable, so shutting down")
            await wrapper.shutdown_and_clean_up()
            return
        else:
            try:
                wrapper.info("trying to re-use connection")
                wrapper.conn.start_next_cycle()
            except h11.ProtocolError:
                states = wrapper.conn.states
                wrapper.info("unexpected state", states, "-- bailing out")
                await maybe_send_error_response(
                    wrapper,
                    RuntimeError("unexpected state {}".format(states)))
                await wrapper.shutdown_and_clean_up()
                return

################################################################
# Actual response handlers
################################################################

# Helper function
async def send_simple_response(wrapper, status_code, content_type, body):
    wrapper.info("Sending", status_code,
                 "response with", len(body), "bytes")
    headers = wrapper.basic_headers()
    headers.append(("Content-Type", content_type))
    headers.append(("Content-Length", str(len(body))))
    res = h11.Response(status_code=status_code, headers=headers)
    await wrapper.send(res)
    await wrapper.send(h11.Data(data=body))
    await wrapper.send(h11.EndOfMessage())

async def maybe_send_error_response(wrapper, exc):
    # If we can't send an error, oh well, nothing to be done
    wrapper.info("trying to send error response...")
    if wrapper.conn.our_state not in {h11.IDLE, h11.SEND_RESPONSE}:
        wrapper.info("...but I can't, because our state is",
                     wrapper.conn.our_state)
        return
    try:
        if isinstance(exc, h11.RemoteProtocolError):
            status_code = exc.error_status_hint
        else:
            status_code = 500
        body = str(exc).encode("utf-8")
        await send_simple_response(wrapper,
                                   status_code,
                                   "text/plain; charset=utf-8",
                                   body)
    except Exception as exc:
        wrapper.info("error while sending error response:", exc)

async def send_echo_response(wrapper, request):
    wrapper.info("Preparing echo response")
    if request.method not in {b"GET", b"POST"}:
        # Laziness: we should send a proper 405 Method Not Allowed with the
        # appropriate Accept: header, but we don't.
        raise RuntimeError("unsupported method")
    response_json = {
        "method": request.method.decode("ascii"),
        "target": request.target.decode("ascii"),
        "headers": [(name.decode("ascii"), value.decode("ascii"))
                    for (name, value) in request.headers],
        "body": "",
    }
    while True:
        event = await wrapper.next_event()
        if type(event) is h11.EndOfMessage:
            break
        assert type(event) is h11.Data
        response_json["body"] += event.data.decode("ascii")
    response_body_unicode = json.dumps(response_json,
                                       sort_keys=True,
                                       indent=4,
                                       separators=(",", ": "))
    response_body_bytes = response_body_unicode.encode("utf-8")
    await send_simple_response(wrapper,
                               200,
                               "application/json; charset=utf-8",
                               response_body_bytes)

################################################################
# Run the server
################################################################

if __name__ == "__main__":
    kernel = curio.Kernel()
    print("Listening on http://localhost:8080")
    kernel.run(curio.tcp_server("localhost", 8080, http_serve))

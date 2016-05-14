.. _API-documentation:

API documentation
=================

.. module:: h11

.. contents::

h11 has a fairly small public API, with all public symbols available
directly at the top level:

.. ipython::

   In [2]: import h11

   @verbatim
   In [3]: h11.<TAB>
   h11.CLIENT                 h11.MUST_CLOSE
   h11.CLOSED                 h11.Paused
   h11.Connection             h11.PRODUCT_ID
   h11.ConnectionClosed       h11.ProtocolError
   h11.Data                   h11.Request
   h11.DONE                   h11.Response
   h11.EndOfMessage           h11.SEND_BODY
   h11.ERROR                  h11.SEND_RESPONSE
   h11.IDLE                   h11.SERVER
   h11.InformationalResponse  h11.SWITCHED_PROTOCOL
   h11.MIGHT_SWITCH_PROTOCOL

These symbols fall into three main categories: event classes, special
constants used to track different connection states, and the
:class:`Connection` class itself. We'll describe them in that order.

.. _events:

Events
------

*Events* are the core of h11: the whole point of h11 is to let you
think about HTTP transactions as being a series of events sent back
and forth between a client and a server, instead of thinking in terms
of bytes.

All events behave in essentially similar ways. Let's take
:class:`Request` as an example. Like all events, this is a "final"
class -- you cannot subclass it. And like all events, it has several
fields. For :class:`Request`, there are four of them:
:attr:`~.Request.method`, :attr:`~.Request.target``,
:attr:`~.Request.headers``, and
:attr:`~.Request.http_version``. :attr:`~.Request.http_version``
defaults to ``b"1.1"``; the rest have no default, so to create a
:class:`Request` you have to specify their values:

.. ipython:: python

   req = h11.Request(method="GET",
                     target="/",
                     headers=[("Host", "example.com")])

Event constructors accept only keyword arguments, not positional arguments.

Events have a useful repr:

.. ipython:: python

   req

And their fields are available as regular attributes:

.. ipython:: python

   req.method
   req.target
   req.headers
   req.http_version

Notice that these attributes have been normalized to byte-strings. In
general, events normalize and validate their fields when they're
constructed. Some of these normalizations and checks are specific to a
particular event -- for example, :class:`Request` enforces RFC 7230's
requirement that HTTP/1.1 requests must always contain a ``"Host"``
header:

.. ipython:: python

   # HTTP/1.0 requests don't require a Host: header
   h11.Request(method="GET", target="/", headers=[], http_version="1.0")

.. ipython:: python
   :okexcept:

   # But HTTP/1.1 requests do
   h11.Request(method="GET", target="/", headers=[])

This helps protect you from accidentally violating the protocol, and
also helps protect you from remote peers who attempt to violate the
protocol.

A few of these normalization rules are standard across multiple
events, so we document them here:

.. _headers-format:

:attr:`headers`: In h11, headers are represented internally as a list
of (*name*, *value*) pairs, where *name* and *value* are both
byte-strings, *name* is always lowercase, and *name* and *value* are
both guaranteed not to have any leading or trailing whitespace. When
constructing an event, we accept any iterable of pairs like this, and
will automatically convert native strings containing ascii or
:term:`bytes-like object`\s to byte-strings, convert names to
lowercase, and strip whitespace from values:

.. ipython:: python

   original_headers = [("HOST", bytearray(b"  example.com   "))]
   req = h11.Request(method="GET", target="/", headers=original_headers)
   original_headers
   req.headers

If any names are detected with leading or trailing whitespace, then
this is an error ("in the past, differences in the handling of such
whitespace have led to security vulnerabilities" -- `RFC 7230
<https://tools.ietf.org/html/rfc7230#section-3.2.4>`_). We also check
for other protocol violations, e.g. ``Content-Length: hello`` is an
error. We may add additional checks in the future.

.. _http_version-format:

It's not just headers we normalize to being byte-strings: the same
type-conversion logic is also applied to the :attr:`Request.method`
and :attr:`Request.target` field, and -- for consistency -- all
:attr:`http_version` fields. In particular, we always represent HTTP
version numbers as byte-strings like ``b"1.1"``. :term:`Bytes-like
object`\s and native strings will be automatically converted to byte
strings. Note that the HTTP standard `specifically guarantees
<https://tools.ietf.org/html/rfc7230#section-2.6>`_ that all HTTP
version numbers will consist of exactly two digits separated by a dot,
so comparisons like ``req.http_version < b"1.1"`` are safe and valid.

When manually constructing an event, you generally shouldn't specify
:attr:`http_version`, because it defaults to ``b"1.1"``, and if you
attempt to override this to some other value then
:meth:`Connection.send` will reject your event -- h11 only speaks
HTTP/1.1. But it does understand other versions of HTTP, so you might
receive events with other ``http_version`` values from remote peers.

Here's the complete set of events supported by h11:

.. autoclass:: Request

.. autoclass:: InformationalResponse

.. autoclass:: Response

.. autoclass:: Data

.. autoclass:: EndOfMessage

.. autoclass:: ConnectionClosed

.. autoclass:: Paused


.. _state-machine:

The state machine
-----------------

Now that you know what the different events are, the next question is:
what can you do with them?

A basic HTTP request/response cycle looks like this:

* The client sends:

  * one :class:`Request` event with request metadata and headers,
  * zero or more :class:`Data` events with the request body (if any),
  * and an :class:`EndOfMessage` event.

* And then the server replies with:

  * zero or more :class:`InformationalResponse` events,
  * one :class:`Response` event,
  * zero or more :class:`Data` events with the response body (if any),
  * and a :class:`EndOfMessage` event.

And once that's finished, both sides either close the connection, or
they go back to the top and re-use it for another request/response
cycle.

To coordinate this interaction, the h11 :class:`Connection` object
maintains several state machines: one that tracks what the client is
doing, one that tracks what the server is doing, and a few more tiny
ones to track whether :ref:`keep-alive <keepalive-and-pipelining>` is
enabled and whether the client has proposed to :ref:`switch protocols
<switching-protocols>`. h11 always keeps track of all of these state
machines, regardless of whether it's currently playing the client or
server role.

The state machines look like this (click on each to expand):

.. ipython:: python
   :suppress:

   import sys
   import subprocess
   subprocess.check_call([sys.executable,
                          sys._h11_hack_docs_source_path
                          + "/make-state-diagrams.py"])

.. |client-image| image:: _static/CLIENT.svg
      :target: _static/CLIENT.svg
      :width: 100%
      :align: top

.. |server-image| image:: _static/SERVER.svg
      :target: _static/SERVER.svg
      :width: 100%
      :align: top

.. |special-image| image:: _static/special-states.svg
   :target: _static/special-states.svg
   :width: 100%

+----------------+----------------+
| |client-image| | |server-image| |
+----------------+----------------+
|        |special-image|          |
+---------------------------------+

If you squint at the first two diagrams, you can see the client's IDLE
-> SEND_BODY -> DONE path and the server's IDLE -> SEND_RESPONSE ->
SEND_BODY -> DONE path, which encode the basic sequence of events we
described above. But there's a fair amount of other stuff going on
here as well.

The first thing you should notice is the different colors. These
correspond to the different ways that our state machines can change
state.

* Dark blue arcs are *event-triggered transitions*: if we're in state
  A, and this event happens, when we switch to state B. For the client
  machines, these transitions always happen when the client *sends* an
  event. For the server machine, most of them involve the server
  sending an event, except that the server also goes from IDLE ->
  SEND_RESPONSE when the client sends a :class:`Request`.

* Green arcs are *state-triggered transitions*: these are somewhat
  unusual, and are used to couple together the different state
  machines -- if, at any moment, one machine is in state A and another
  machine is in state B, then the first machine immediately
  transitions to state C. For example, if the CLIENT machine is in
  state DONE, and the SERVER machine is in the CLOSED state, then the
  CLIENT machine transitions to MUST_CLOSE. And the same thing happens
  if the CLIENT machine is in the state DONE and the keep-alive
  machine is in the state disabled.

* There are also two purple arcs labeled
  :meth:`~Connection.prepare_to_send`: these correspond to an explicit
  method call documented below.

Here's why we have all the stuff in those diagrams above, beyond
what's needed to handle the basic request/response cycle:

* Server sending a :class:`Response` directly from :data:`IDLE`: This
  is used for error responses, when the client's request never arrived
  (e.g. 408 Request Timed Out) or was unparseable gibberish (400 Bad
  Request) and thus didn't register with our state machine as a real
  :class:`Request`.

* The transitions involving :data:`MUST_CLOSE` and :data:`CLOSE`:
  keep-alive and shutdown handling; see
  :ref:`keepalive-and-pipelining` and :ref:`closing`.

* The transitions involving :data:`MIGHT_SWITCH_PROTOCOL` and
  :data:`SWITCHED_PROTOCOL`: See :ref:`switching-protocols`.

* That weird :data:`ERROR` state hanging out all lonely on the bottom:
  to avoid cluttering the diagram, we don't draw any arcs coming into
  this node, but that doesn't mean it can't be entered. In fact, it
  can be entered from any state: if any exception occurs while trying
  to send/receive data, then the corresponding machine will transition
  directly to this state. Once there, though, it can never leave --
  that part of the diagram is accurate. See :ref:`error-handling`.

And finally, note that in these diagrams, all the labels that are in
*italics* are informal English descriptions of things that happen in
the code, while the labels in upright text correspond to actual
objects in the public API. You've already seen the event objects like
:class:`Request` and :class:`Response`; there are also a set of opaque
sentinel values that you can use to track and query the client and
server's states:

.. data:: IDLE
.. data:: SEND_RESPONSE
.. data:: SEND_BODY
.. data:: DONE
.. data:: MUST_CLOSE
.. data:: CLOSED
.. data:: MIGHT_SWITCH_PROTOCOL
.. data:: SWITCHED_PROTOCOL
.. data:: ERROR

For example, we can see that initially the client and server start in
state :data:`IDLE` / :data:`IDLE`:

.. ipython:: python

   conn = h11.Connection(our_role=h11.CLIENT)
   conn.states

And then if the client sends a :class:`Request`, then the client
switches to state :data:`SEND_BODY`, while the server switches to
state :data:`SEND_RESPONSE`:

.. ipython:: python

   conn.send(h11.Request(method="GET", target="/", headers=[("Host", "example.com")]));
   conn.states

And we can test these values directly using constants like :data:`SEND_BODY`:

.. ipython:: python

   conn.states[h11.CLIENT] is h11.SEND_BODY

This shows how the :class:`Connection` type tracks these state
machines and lets you query their current state.


The Connection object
---------------------

There are two special constants used to indicate the two different
roles that a peer can play in an HTTP connection:

.. data:: CLIENT
.. data:: SERVER

When creating a :class:`Connection` object, you need to pass one of
these constants to indicate which side of the HTTP conversation you
want to implement:

.. autoclass:: Connection

   .. automethod:: receive_data
   .. automethod:: send
   .. automethod:: send_with_data_passthrough

   .. automethod:: prepare_to_reuse

   .. attribute:: our_role

      :data:`CLIENT` if this is a client; :data:`SERVER` if this is a server.

   .. attribute:: their_role

      :data:`SERVER` if this is a client; :data:`CLIENT` if this is a server.

   .. autoattribute:: states
   .. autoattribute:: our_state
   .. autoattribute:: their_state

   .. attribute:: their_http_version

      The version of HTTP that our peer claims to support. ``None`` if
      we haven't yet received a request/response.

      This is preserved by :meth:`prepare_to_reuse`, so it can be
      handy for a client making multiple requests on the same
      connection: normally you don't know what version of HTTP the
      server supports until after you do a request and get a response
      -- so on an initial request you might have to assume the
      worst. But on later requests on the same connection, the
      information will be available here.

   .. attribute:: client_is_waiting_for_100_continue

      True if the client sent a request with the ``Expect:
      100-continue`` header, and is still waiting for a response
      (i.e., the server has not sent a 100 Continue or any other kind
      of response, and the client has not gone ahead and started
      sending the body anyway).

      See `RFC 7231 section 5.1.1
      <https://tools.ietf.org/html/rfc7231#section-5.1.1>`_ for details.

   .. attribute:: they_are_waiting_for_100_continue

      True if :attr:`their_role` is :data:`CLIENT` and
      :attr:`client_is_waiting_for_100_continue`.

   .. autoattribute:: trailing_data


.. _error-handling:

Error handling
--------------

Given the vagaries of networks and the folks on the other side of
them, it's extremely important to be prepared for errors.

Most errors in h11 are signaled by raising :exc:`ProtocolError`:

.. autoexception:: ProtocolError

There are four cases where this exception might be raised:

* When trying to instantiate an event object: This indicates that
  something about your event is invalid. Your event wasn't
  constructed, but there are no other consequences -- feel free to try
  again.

* When calling :meth:`Connection.prepare_to_reuse`: This indicates
  that the connection is not ready to be re-used, because one or both
  of the peers are not in the :data:`DONE` state. The
  :class:`Connection` object remains usable, and you can try again
  later.

* When calling :meth:`Connection.receive_data`: This indicates that
  the remote peer has violated our protocol assumptions. This is
  unrecoverable -- we don't know what they're doing and we cannot
  safely proceed. :attr:`Connection.their_state` immediately becomes
  :data:`ERROR`, and all further calls to
  :meth:`~.Connection.receive_data` will also raise
  :exc:`ProtocolError`. :meth:`Connection.send` still works as normal,
  so if you're implementing a server and this happens then you have an
  opportunity to send back a 400 Bad Request response. Your only other
  real option is to close your socket and make a new connection.

* When calling :meth:`Connection.send`: This indicates that *you*
  violated our protocol assumptions. This is also unrecoverable -- h11
  doesn't know what you're doing, its internal state may be
  inconsistent, and we cannot safely
  proceed. :attr:`Connection.our_state` immediately becomes
  :data:`ERROR`, and all further calls to :meth:`~.Connection.send`
  will also raise :exc:`ProtocolError`. The only thing you can
  reasonably due at this point is to close your socket and make a new
  connection.


.. _framing:

Message body framing: ``Content-Length`` and all that
-----------------------------------------------------

There are two different headers that HTTP/1.1 uses to indicate a
framing mechanism for request/response bodies: ``Content-Length`` and
``Transfer-Encoding``. Our general philosophy is that the way you tell
h11 what configuration you want to use is by setting the appropriate
headers in your request / response, and then h11 will both pass those
headers on to the peer and encode the body appropriately.

Currently, the only supported ``Transfer-Encoding`` is ``chunked``.

On requests, this means:

* No ``Content-Length`` or ``Transfer-Encoding``: no body, equivalent
  to ``Content-Length: 0``.

* ``Content-Length: ...``: You're going to send exactly the specified
  number of bytes. h11 will keep track and signal an error if your
  :class:`EndOfMessage` doesn't happen at the right place.

* ``Transfer-Encoding: chunked``: You're going to send a variable /
  not yet known number of bytes.

  Note 1: only HTTP/1.1 servers are required to supported
  ``Transfer-Encoding: chunked``, and as a client you have to either
  send this header or not before you get to see what protocol version
  the server is using.

  Note 2: even though HTTP/1.1 servers are required to support
  ``Transfer-Encoding: chunked``, this doesn't mean that they actually
  do -- e.g., applications using Python's standard WSGI API cannot
  accept chunked requests.

  Nonetheless, this is the only way to send request where you don't
  know the size of the body ahead of time, so you might as well go
  ahead and hope.

On responses, things are a bit more subtle. There are effectively two
cases:

* ``Content-Length: ...``: You're going to send exactly the specified
  number of bytes. h11 will keep track and signal an error if your
  :class:`EndOfMessage` doesn't happen at the right place.

* ``Transfer-Encoding: chunked``, *or*, neither framing header is
  provided: These two cases are handled differently at the wire level,
  but as far as the application is concerned they provide (almost)
  exactly the same semantics: in either case, you'll send a variable /
  not yet known number of bytes. The difference between them is that
  ``Transfer-Encoding: chunked`` works better (compatible with
  keep-alive, allows trailing headers, clearly distinguishes between
  successful completion and network errors), but requires an HTTP/1.1
  client; for HTTP/1.0 clients the only option is the no-headers
  close-socket-to-indicate-completion approach.

  Since this is (almost) entirely a wire-level-encoding concern, h11
  abstracts it: when sending a response you can set either
  ``Transfer-Encoding: chunked`` or leave off both framing headers,
  and h11 will treat both cases identically: it will automatically
  pick the best option given the client's advertised HTTP protocol
  level.

  You need to watch out for this if you're using trailing headers
  (i.e., a non-empty ``headers`` attribute on :class:`EndOfMessage`),
  since trailing headers are only legal if we actually ended up using
  ``Transfer-Encoding: chunked``. Trying to send a non-empty set of
  trailing headers to a HTTP/1.0 client will raise a
  :exc:`ProtocolError`. If this use case is important to you, check
  :attr:`Connection.their_http_version` to confirm that the client
  speaks HTTP/1.1 before you attempt to send any trailing headers.


.. _keepalive-and-pipelining:

Re-using a connection: keep-alive and pipelining
------------------------------------------------

HTTP/1.1 allows a connection to be re-used for multiple
request/response cycles (also known as "keep-alive"). This can make
things faster by letting us skip the costly connection setup, but it
does create some complexities: we have to keep track of whether a
connection is reusable, and when there are multiple requests and
responses flowing through the same connection we need to be careful
not to get confused about which request goes with which response.

h11 considers a connection to be reusable if, and only if, both
sides (a) speak HTTP/1.1 (HTTP/1.0 did have some complex and fragile
support for keep-alive bolted on, but h11 currently doesn't support
that -- possibly this will be added in the future), and (b) neither
side has explicitly disabled keep-alive by sending a ``Connection:
close`` header.

If you plan to make only a single request or response and then close
the connection, you should manually set the ``Connection: close``
header in your request/response. h11 will notice and update its state
appropriately.

There are also some situations where you are required to send a
``Connection: close`` header, e.g. if you are a server talking to a
client that doesn't support keep-alive. You don't need to worry about
these cases -- h11 will automatically add this header when
necessary. Just worry about setting it when it's actually something
that you're actively choosing.

If you want to re-use a connection, you have to wait until both the
request and the response have been completed, bringing both the client
and server to the :data:`DONE` state. Once this has happened, you can
explicitly call :meth:`Connection.prepare_to_reuse` to reset both
sides back to the :data:`IDLE` state. This makes sure that the client
and server remain synched up.

If keep-alive is disabled for whatever reason -- explicit headers,
lack of protocol support, one of the sides just unilaterally closed
the connection -- then the state machines will skip past the
:data:`DONE` state directly to the :data:`MUST_CLOSE` or
:data:`CLOSED` states. In this case, trying to call
:meth:`~.Connection.prepare_to_use` will raise an error, and the only
thing you can legally do is to close this connection and make a new
one.

HTTP/1.1 also allows for a more aggressive form of connection re-use,
in which a client sends multiple requests in quick succession, and
then waits for the responses to stream back in order
("pipelining"). This is generally considered to have been a bad idea,
because it makes things like error recovery very complicated.

As a client, h11 does not support pipelining. This is enforced by the
structure of the state machine: after sending one :class:`Request`,
you can't send another until after calling
:meth:`~.Connection.prepare_to_reuse`, and you can't call
:meth:`~.Connection.prepare_to_reuse` until the server has entered the
:data:`DONE` state, which requires reading the server's full
response.

As a server, h11 provides the minimal support for pipelining required
to comply with the HTTP/1.1 standard: if the client sends multiple
pipelined requests, then we the first request until we reach the
:data:`DONE` state, and then :meth:`~.Connection.receive_data` will
pause and refuse to parse any more events until the response is
completed and :meth:`~.Connection.prepare_to_reuse` is called. See the
next section for more details.


.. _flow-control:

Flow control
------------

h11 always does the absolute minimum of buffering that it can get away
with: :meth:`~.Connection.send` always returns the full data to send
immediately, and :meth:`~.Connection.recieve_data` always greedily
parses and returns as many events as possible from its current
buffer. So you can be sure that no data or events will suddenly appear
and need processing, except when you call these methods. And
presumably you know when you want to send things. But there is one
thing you still need to know: you don't want to read data from the
remote peer if it can't be processed (i.e., you want to apply
backpressure and avoid building arbitrarily large buffers), and you
definitely don't want to block waiting on data from the remote peer at
the same time that it's blocked waiting for you, because that will
cause a deadlock.

We assume that if you're implementing a client then you're clever
enough not to sit around trying to read more data from the server when
there's no response pending. But there are a few more subtle ways that
reading in HTTP can go wrong, and h11 provides two ways to help you
avoid these situations.

First, it keeps track of the `client's ``Expect: 100-continue`` status
<https://tools.ietf.org/html/rfc7231#section-5.1.1>`_. you can read
the spec for details, but basically the way this works if that
sometimes clients will send a :class:`Request` with an ``Expect:
100-continue`` header, and then they will stop there, before sending
the body, until they see some response from the server (or possibly
some timeout occurs). The server's response can be an
:class:`InformationalResponse` with status ``100 Continue``, or
anything really (e.g. a full :class:`Response` with an error
code). The crucial thing as a server, though, is that you should never
block trying to read a request body if the client is blocked waiting
for you to tell them to send the request body.

The simple way to avoid this is to make sure that before you block
waiting to read data, always execute some code like:

.. code-block:: python

   if conn.they_are_waiting_for_100_continue:
       send(conn, h11.InformationalResponse(100, headers=[...]))
   do_read(...)

The other mechanism h11 provides to help you manage read flow control
is the :class:`Paused` pseudo-event. Unlike other events, the
:class:`Paused` event doesn't contain information sent from the remote
peer; if :meth:`~.Connection.receive_data` returns one of these, it
means that :meth:`~.Connection.receive_data` has stopped processing
its data buffer and isn't going to process any more until the remote
peer's state (:attr:`Connection.their_state`) changes to something
different.

There are three possible reasons to enter a paused state:

* The remote peer is in the :data:`DONE` state, but sent more data,
  i.e., a client is attempting to :ref:`pipeline requests
  <keepalive-and-pipelining>`. In the :data:`DONE` state,
  :meth:`~.Connection.receive_data` can return :class:`ConnectionClosed`
  events, but if any actual data is received then it will pause, and
  stay that way until a successful call to
  :meth:`~.Connection.prepare_to_reuse`.

* The remote client is in the :data:`MIGHT_SWITCH_PROTOCOL` state (see
  :ref:`switching-protocols`). This really shouldn't happen, because
  they don't know yet whether the protocol switch will actually happen,
  but OTOH it certainly isn't correct for us to go ahead and parse the
  data they sent as if it were HTTP, when it might not be. So if this
  happens, we pause.

* The remote peer is in the :data:`SWITCHED_PROTOCOL` state (see
  :ref:`switching-protocols`). We certainly aren't going to try to
  parse their data -- it's not HTTP, or at least not HTTP directed at
  us. If this happens, we pause.

Once the connection has entered a paused state, then it's safe to keep
calling :meth:`~.Connection.receive_data` -- it will just keep
returning new :class:`Paused` events -- but instead you should
probably stop reading from the network; all you're going to accomplish
is to shove more and more data into our internal buffers, where it's
just going to there using more and more memory. (And we do *not*
enforce the regular maximum buffer size limits when in a paused state
-- if we did then you might go over the limit in a single call to
:meth:`~.Connection.receive_data`, not because you or the remote peer
did anything wrong, but just because a fair amount of data all came in
at the same time we entered the paused state.) And simply reading more
data will never trigger an unpause -- for that something external has
to happen, usually a call to :meth:`~.Connection.prepare_to_reuse`.

And that's the other tricky moment: when you come out of a paused
state, you shouldn't immediately read from the network. Consider the
situation where a client sends two pipelined requests, and then blocks
waiting for the two responses. It's possible the two requests will
arrive together, and be enqueued into our receive buffer together:

.. ipython:: python

   conn = h11.Connection(our_role=h11.SERVER)
   conn.receive_data(
       b"GET /1 HTTP/1.1\r\nHost: example.com\r\n\r\n"
       b"GET /1 HTTP/1.1\r\nHost: example.com\r\n\r\n"
   )

Notice how we get back only the first :class:`Request` and its (empty)
body, then a :class:`Paused` event.

We process the first request:

.. ipython:: python

   conn.send(h11.Response(status_code=200, headers=[]))
   conn.send(h11.EndOfMessage())

And then reset the connection to handle the next:

.. ipython:: python

   conn.prepare_to_reuse()

This has unpaused our receive buffer, so now we're ready to read more
data from the network right? Well, no-- the client is done sending
data, we already have all their data, so if we block waiting for more
data now, then we'll be waiting forever.

That would be bad.

Instead, what we have to do after unpausing is make an explicit call
to :meth:`~.Connection.receive_data` with ``None`` as the argument,
which means "I don't have any more data for you, but could you check
the data you already have buffered in case there's anything else you
can parse now that you couldn't before?". And once we've done this and
processed the events we get back, we can continue as normal:

.. ipython:: python

   conn.receive_data(None)

It is always safe to call ``conn.receive_data(None)``; if there aren't
any new events to return, it will simply return ``[]``, and if the
connection is paused, it will return a :class:`Paused` event. If you
want to be conservative, you can defensively call this immediately
before issuing any blocking read.


.. _closing:

Closing connections
-------------------

h11 represents a connection shutdown with the special event type
:class:`ConnectionClosed`. You can send this event, in which case
:meth:`~.Connection.send` will simply update the state machine and
then return ``None``. You can receive this event, if you call
``conn.receive_data(b"")``. (The actual receipt might be delayed if
the connection is :ref:`paused <flow-control>`.) It's safe and legal
to call ``conn.receive_data(b"")`` multiple times, and once you've
done this once, then all future calls to
:meth:`~.Connection.receive_data` will also return
``ConnectionClosed()``:

.. ipython:: python

   conn = h11.Connection(our_role=h11.CLIENT)
   conn.receive_data(b"")
   conn.receive_data(b"")
   conn.receive_data(None)

(Or if you try to actually pass new data in after calling
``conn.receive_data(b"")``, that will raise an exception.)

h11 is careful about interpreting connection closure in a *half-duplex
fashion*. TCP sockets pretend to be a two-way connection, but really
they're two one-way connections. In particular, it's possible for one
party to shut down their sending connection -- which causes the other
side to be notified that the connection has closed via the usual
``socket.recv(...) -> b""`` mechanism -- while still being able to
read from their receiving connection. (On Unix, this is generally
accomplished via the ``shutdown(2)`` system call.) So, for example, a
client could send a request, and then close their socket for writing
to indicate that they won't be sending any more requests, and then
read the response. It's this kind of closure that is indicated by
h11's :class:`ConnectionClosed`: it means that this party will not be
sending any more data -- nothing more, nothing less. You can see this
reflected in the :ref:`state machine <state-machine>`, in which one
party transitioning to :data:`CLOSED` doesn't immediately halt the
connection, but merely prevents it from continuing for another
request/response cycle.

The state machine also indicates that :class:`ConnectionClosed` events
can only happen in certain states. This isn't true, of course -- any
party can close their connection at any time, and h11 can't stop
them. But what h11 can do is distinguish between clean and unclean
closes. For example, if both sides complete a request/response cycle
and then close the connection, that's a clean closure and everyone
will transition to the :data:`CLOSED` state in an orderly fashion. On
the other hand, if one party suddenly closes the connection while
they're in the middle of sending a chunked response body, or when they
promised a ``Content-Length:`` of 1000 bytes but have only sent 500,
then h11 knows that this is a violation of the HTTP protocol, and will
raise a :exc:`ProtocolError`. Basically h11 treats an unexpected
close the same way it would treat unexpected, uninterpretable data
arriving -- it lets you know that something has gone wrong.

As a client, the proper way to perform a single request and then close
the connection is:

1) Send a :class:`Request` with ``Connection: close``

2) Send the rest of the request body

3) Read the server's :class:`Response` and body

4) ``conn.our_state is h11.MUST_CLOSE`` will now be true. Call
   ``conn.send(ConnectionClosed())`` and then close the socket. Or
   really you could just close the socket -- the thing calling
   ``send`` will do is raise an error if you're not in
   :data:`MUST_CLOSE` as expected. So it's between you and your
   conscience and your code reviewers.

(Technically it would also be legal to shutdown your socket for
writing as step 2.5, but this doesn't serve any purpose and some
buggy servers might get annoyed, so it's not recommended.)

As a server, the proper way to perform a response is:

1) Send your :class:`Response` and body

2) Check if ``conn.our_state is h11.MUST_CLOSE``. This might happen
   for a variety of reasons; for example, if the response had unknown
   length and the client speaks only HTTP/1.0, then the client will
   not consider the connection complete until we issue a close.

You should be particularly careful to take into consideration the
following note fromx `RFC 7230 section 6.6
<https://tools.ietf.org/html/rfc7230#section-6.6>`_:

   If a server performs an immediate close of a TCP connection, there is
   a significant risk that the client will not be able to read the last
   HTTP response.  If the server receives additional data from the
   client on a fully closed connection, such as another request that was
   sent by the client before receiving the server's response, the
   server's TCP stack will send a reset packet to the client;
   unfortunately, the reset packet might erase the client's
   unacknowledged input buffers before they can be read and interpreted
   by the client's HTTP parser.

   To avoid the TCP reset problem, servers typically close a connection
   in stages.  First, the server performs a half-close by closing only
   the write side of the read/write connection.  The server then
   continues to read from the connection until it receives a
   corresponding close by the client, or until the server is reasonably
   certain that its own TCP stack has received the client's
   acknowledgement of the packet(s) containing the server's last
   response.  Finally, the server fully closes the connection.


.. _switching-protocols:

Switching protocols
-------------------

h11 supports two kinds of "protocol switches": requests with method
``CONNECT``, and the newer ``Upgrade:`` header, most commonly used for
negotiating WebSocket connections. Both follow the same pattern: the
client proposes that they switch from regular HTTP to some other kind
of interaction, and then the server either rejects the suggestion --
in which case we return to regular HTTP rules -- or else accepts
it. (For ``CONNECT``, acceptance means a response with 2xx status
code; for ``Upgrade:``, acceptance means an
:class:`InformationalResponse` with status ``101 Switching
Protocols``) If the proposal is accepted, then both sides switch to
doing something else with their socket, and h11's job is done.

As a developer using h11, it's your responsibility to send and
interpret the actual ``CONNECT`` or ``Upgrade:`` request and response,
and to figure out what to do after the handover; it's h11's job to
understand what's going on, and help you make the handover
smoothly.

Specifically, what h11 does is :ref:`pause <flow-control>` parsing
incoming data at the boundary between the two protocols, and then you
can retrieve any unprocessed data from the
:attr:`Connection.trailing_data` attribute.


.. _sendfile:

Support for ``sendfile()``
--------------------------

Many networking APIs provide some efficient way to send particular
data, e.g. asking the operating system to stream files directly off of
the disk and into a socket without passing through userspace.

It's possible to use these APIs together with h11. The basic strategy
is:

* Create some placeholder object representing the special data, that
  your networking code knows how to "send" by invoking whatever the
  appropriate underlying APIs are.

* Make sure your placeholder object implements a ``__len__`` method
  returning its size in bytes.

* Call ``conn.send_with_data_passthrough(Data(data=<your placeholder
  object>))``

* This returns a list whose contents are a mixture of (a) bytes-like
  objects, and (b) your placeholder object. You should send them to
  the network in order.

Here's a sketch of what this might look like:

.. code-block:: python

   class FilePlaceholder:
       def __init__(self, file, offset, count):
           self.file = file
           self.offset = offset
           self.count = count

       def __len__(self):
           return self.count

   def send_data(sock, data):
       if isinstance(data, FilePlaceholder):
           # socket.sendfile added in Python 3.5
           sock.sendfile(data.file, data.offset, data.count)
       else:
           sock.sendfile(data)

   placeholder = FilePlaceholder(open("...", "rb"), 0, 200)
   for data in conn.send_with_data_passthrough(Data(data=placeholder)):
       send_data(sock, data)

This works with all the different framing modes (``Content-Length``,
``Transfer-Encoding: chunked``, etc.) -- h11 will add any necessary
framing data, update its internal state, and away you go.


Identifying h11 in requests and responses
-----------------------------------------

According to RFC 7231, client requests are supposed to include a
``User-Agent:`` header identifying what software they're using, and
servers are supposed to respond with a ``Server:`` header doing the
same. h11 doesn't construct these headers for you, but to make it
easier for you to construct this header, it provides:

.. data:: PRODUCT_ID

   A string suitable for identifying the current version of h11 in a
   ``User-Agent:`` or ``Server:`` header.

   The version of h11 that was used to build these docs identified
   itself as:

   .. ipython:: python

      h11.PRODUCT_ID

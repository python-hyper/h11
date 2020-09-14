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
   h11.CLOSED                 h11.NEED_DATA
   h11.Connection             h11.PAUSED
   h11.ConnectionClosed       h11.PRODUCT_ID
   h11.Data                   h11.ProtocolError
   h11.DONE                   h11.RemoteProtocolError
   h11.EndOfMessage           h11.Request
   h11.ERROR                  h11.Response
   h11.IDLE                   h11.SEND_BODY
   h11.InformationalResponse  h11.SEND_RESPONSE
   h11.LocalProtocolError     h11.SERVER
   h11.MIGHT_SWITCH_PROTOCOL  h11.SWITCHED_PROTOCOL

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
:attr:`~Request.method`, :attr:`~Request.target`,
:attr:`~Request.headers`, and
:attr:`~Request.http_version`. :attr:`~Request.http_version`
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
:term:`bytes-like object`\s to byte-strings and convert names to
lowercase:

.. ipython:: python

   original_headers = [("HOST", bytearray(b"Example.Com"))]
   req = h11.Request(method="GET", target="/", headers=original_headers)
   original_headers
   req.headers

If any names are detected with leading or trailing whitespace, then
this is an error ("in the past, differences in the handling of such
whitespace have led to security vulnerabilities" -- `RFC 7230
<https://tools.ietf.org/html/rfc7230#section-3.2.4>`_). We also check
for certain other protocol violations, e.g. it's always illegal to
have a newline inside a header value, and ``Content-Length: hello`` is
an error because `Content-Length` should always be an integer. We may
add additional checks in the future.

While we make sure to expose header names as lowercased bytes, we also
preserve the original header casing that is used. Compliant HTTP
agents should always treat headers in a case insensitive manner, but
this may not always be the case. When sending bytes over the wire we
send headers preserving whatever original header casing was used.

It is possible to access the headers in their raw original casing,
which may be useful for some user output or debugging purposes.

.. ipython:: python

    original_headers = [("Host", "example.com")]
    req = h11.Request(method="GET", target="/", headers=original_headers)
    req.headers.raw_items()

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
  machine, these transitions always happen when the client *sends* an
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
  :meth:`~Connection.start_next_cycle`: these correspond to an explicit
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
server's states.


Special constants
-----------------

h11 exposes some special constants corresponding to the different
states in the client and server state machines described above. The
complete list is:

.. data:: IDLE
          SEND_RESPONSE
          SEND_BODY
          DONE
          MUST_CLOSE
          CLOSED
          MIGHT_SWITCH_PROTOCOL
          SWITCHED_PROTOCOL
          ERROR

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

The above also showed the special constants that can be used to
indicate the two different roles that a peer can play in an HTTP
connection:

.. data:: CLIENT
          SERVER

And finally, there are also two special constants that can be returned
from :meth:`Connection.next_event`:

.. data:: NEED_DATA
          PAUSED

All of these behave the same, and their behavior is modeled after
:data:`None`: they're opaque singletons, their :meth:`__repr__` is
their name, and you compare them with ``is``.

.. _sentinel-type-trickiness:

Finally, h11's constants have a quirky feature that can sometimes be
useful: they are instances of themselves.

.. ipython:: python

   type(h11.NEED_DATA) is h11.NEED_DATA
   type(h11.PAUSED) is h11.PAUSED

The main application of this is that when handling the return value
from :meth:`Connection.next_event`, which is sometimes an instance of
an event class and sometimes :data:`NEED_DATA` or :data:`PAUSED`, you
can always call ``type(event)`` to get something useful to dispatch
one, using e.g. a handler table, :func:`functools.singledispatch`, or
calling ``getattr(some_object, "handle_" +
type(event).__name__)``. Not that this kind of dispatch-based strategy
is always the best approach -- but the option is there if you want it.


The Connection object
---------------------

.. autoclass:: Connection

   .. automethod:: receive_data
   .. automethod:: next_event
   .. automethod:: send
   .. automethod:: send_with_data_passthrough
   .. automethod:: send_failed

   .. automethod:: start_next_cycle

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

      This is preserved by :meth:`start_next_cycle`, so it can be
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

Most errors in h11 are signaled by raising one of
:exc:`ProtocolError`'s two concrete base classes,
:exc:`LocalProtocolError` and :exc:`RemoteProtocolError`:

.. autoexception:: ProtocolError
.. autoexception:: LocalProtocolError
.. autoexception:: RemoteProtocolError

There are four cases where these exceptions might be raised:

* When trying to instantiate an event object
  (:exc:`LocalProtocolError`): This indicates that something about
  your event is invalid. Your event wasn't constructed, but there are
  no other consequences -- feel free to try again.

* When calling :meth:`Connection.start_next_cycle`
  (:exc:`LocalProtocolError`): This indicates that the connection is
  not ready to be re-used, because one or both of the peers are not in
  the :data:`DONE` state. The :class:`Connection` object remains
  usable, and you can try again later.

* When calling :meth:`Connection.next_event`
  (:exc:`RemoteProtocolError`): This indicates that the remote peer
  has violated our protocol assumptions. This is unrecoverable -- we
  don't know what they're doing and we cannot safely
  proceed. :attr:`Connection.their_state` immediately becomes
  :data:`ERROR`, and all further calls to
  :meth:`~Connection.next_event` will also raise
  :exc:`RemoteProtocolError`. :meth:`Connection.send` still works as
  normal, so if you're implementing a server and this happens then you
  have an opportunity to send back a 400 Bad Request response. But
  aside from that, your only real option is to close your socket and
  make a new connection.

* When calling :meth:`Connection.send` or
  :meth:`Connection.send_with_data_passthrough`
  (:exc:`LocalProtocolError`): This indicates that *you* violated our
  protocol assumptions. This is also unrecoverable -- h11 doesn't know
  what you're doing, its internal state may be inconsistent, and we
  cannot safely proceed. :attr:`Connection.our_state` immediately
  becomes :data:`ERROR`, and all further calls to
  :meth:`~Connection.send` will also raise
  :exc:`LocalProtocolError`. The only thing you can reasonably due at
  this point is to close your socket and make a new connection.

So that's how h11 tells you about errors that it detects. In some
cases, it's also useful to be able to tell h11 about an error that you
detected. In particular, the :class:`Connection` object assumes that
after you call :meth:`Connection.send`, you actually send that data to
the remote peer. But sometimes, for one reason or another, this
doesn't actually happen.

Here's a concrete example. Suppose you're using h11 to implement an
HTTP client that keeps a pool of connections so it can re-use them
when possible (see :ref:`keepalive-and-pipelining`). You take a
connection from the pool, and start to do a large upload... but then
for some reason this gets cancelled (maybe you have a GUI and a user
clicked "cancel"). This can cause h11's model of this connection to
diverge from reality: for example, h11 might think that you
successfully sent the full request, because you passed an
:class:`EndOfMessage` object to :meth:`Connection.send`, but in fact
you didn't, because you never sent the resulting bytes. And then –
here's the really tricky part! – if you're not careful, you might
think that it's OK to put this connection back into the connection
pool and re-use it, because h11 is telling you that a full
request/response cycle was completed. But this is wrong; in fact you
have to close this connection and open a new one.

The solution is simple: call :meth:`Connection.send_failed`, and now
h11 knows that your send failed. In this case,
:attr:`Connection.our_state` immediately becomes :data:`ERROR`, just
like if you had tried to do something that violated the protocol.


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

  Note 1: only HTTP/1.1 servers are required to support
  ``Transfer-Encoding: chunked``, and as a client you have to decide
  whether to send this header before you get to see what protocol
  version the server is using.

  Note 2: even though HTTP/1.1 servers are required to support
  ``Transfer-Encoding: chunked``, this doesn't necessarily mean that
  they actually do -- e.g., applications using Python's standard WSGI
  API cannot accept chunked requests.

  Nonetheless, this is the only way to send request where you don't
  know the size of the body ahead of time, so if that's the situation
  you find yourself in then you might as well try it and hope.

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
  approach where you have to close the socket to indicate completion.

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
  :exc:`LocalProtocolError`. If this use case is important to you, check
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
explicitly call :meth:`Connection.start_next_cycle` to reset both
sides back to the :data:`IDLE` state. This makes sure that the client
and server remain synched up.

If keep-alive is disabled for whatever reason -- someone set
``Connection: close``, lack of protocol support, one of the sides just
unilaterally closed the connection -- then the state machines will
skip past the :data:`DONE` state directly to the :data:`MUST_CLOSE` or
:data:`CLOSED` states. In this case, trying to call
:meth:`~Connection.start_next_cycle` will raise an error, and the only
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
:meth:`~Connection.start_next_cycle`, and you can't call
:meth:`~Connection.start_next_cycle` until the server has entered the
:data:`DONE` state, which requires reading the server's full
response.

As a server, h11 provides the minimal support for pipelining required
to comply with the HTTP/1.1 standard: if the client sends multiple
pipelined requests, then we handle the first request until we reach the
:data:`DONE` state, and then :meth:`~Connection.next_event` will
pause and refuse to parse any more events until the response is
completed and :meth:`~Connection.start_next_cycle` is called. See the
next section for more details.


.. _flow-control:

Flow control
------------

Presumably you know when you want to send things, and the
:meth:`~Connection.send` interface is very simple: it just immediately
returns all the data you need to send for the given event, so you can
apply whatever send buffer strategy you want. But reading from the
remote peer is a bit trickier: you don't want to read data from the
remote peer if it can't be processed (i.e., you want to apply
backpressure and avoid building arbitrarily large in-memory buffers),
and you definitely don't want to block waiting on data from the remote
peer at the same time that it's blocked waiting for you, because that
will cause a deadlock.

One complication here is that if you're implementing a server, you
have to be prepared to handle :class:`Request`\s that have an
``Expect: 100-continue`` header. You can `read the spec
<https://tools.ietf.org/html/rfc7231#section-5.1.1>`_ for the full
details, but basically what this header means is that after sending
the :class:`Request`, the client plans to pause and wait until they
see some response from the server before they send that request's
:class:`Data`. The server's response would normally be an
:class:`InformationalResponse` with status ``100 Continue``, but it
could be anything really (e.g. a full :class:`Response` with a 4xx
status code). The crucial thing as a server, though, is that you
should never block trying to read a request body if the client is
blocked waiting for you to tell them to send the request body.

Fortunately, h11 makes this easy, because it tracks whether the client
is in the waiting-for-100-continue state, and exposes this as
:attr:`Connection.they_are_waiting_for_100_continue`. So you don't
have to pay attention to the ``Expect`` header yourself; you just have
to make sure that before you block waiting to read a request body, you
execute some code like:

.. code-block:: python

   if conn.they_are_waiting_for_100_continue:
       do_send(conn, h11.InformationalResponse(100, headers=[...]))
   do_read(...)

In fact, if you're lazy (and what programmer isn't?) then you can just
do this check before all reads -- it's mandatory before blocking to
read a request body, but it's safe at any time.

And the other thing you want to pay attention to is the special values
that :meth:`~Connection.next_event` might return: :data:`NEED_DATA`
and :data:`PAUSED`.

:data:`NEED_DATA` is what it sounds like: it means that
:meth:`~Connection.next_event` is guaranteed not to return any more
real events until you've called :meth:`~Connection.receive_data` at
least once.

:data:`PAUSED` is a little more subtle: it means that
:meth:`~Connection.next_event` is guaranteed not to return any more
real events until something else has happened to clear up the paused
state. There are three cases where this can happen:

1) We received a full request/response from the remote peer, and then
   we received some more data after that. (The main situation where
   this might happen is a server responding to a pipelining client.)
   The :data:`PAUSED` state will go away after you call
   :meth:`~Connection.start_next_cycle`.

2) A successful ``CONNECT`` or ``Upgrade:`` request has caused the
   connection to switch to some other protocol (see
   :ref:`switching-protocols`). This :data:`PAUSED` state is
   permanent; you should abandon this :class:`Connection` and go do
   whatever it is you're going to do with your new protocol.

3) We're a server, and the client we're talking to proposed to switch
   protocols (see :ref:`switching-protocols`), and now is waiting to
   find out whether their request was successful or not. Once we
   either accept or deny their request then this will turn into one of
   the above two states, so you probably don't need to worry about
   handling it specially.

Putting all this together --

If your I/O is organized around a "pull" strategy, where your code
requests events as its ready to handle them (e.g. classic synchronous
code, or asyncio's ``await loop.sock_recv(...)``, or `Trio's streams
<http://https://trio.readthedocs.io/en/latest/reference-io.html#the-abstract-stream-api>`__),
then you'll probably want logic that looks something like:

.. code-block:: python

   # Replace do_sendall and do_recv with your I/O code
   def get_next_event():
       while True:
           event = conn.next_event()
           if event is h11.NEED_DATA:
               if conn.they_are_waiting_for_100_continue:
                   do_sendall(conn, h11.InformationalResponse(100, ...))
               conn.receive_data(do_recv())
               continue
           return event

And then your code that calls this will need to make sure to call it
only at appropriate times (e.g., not immediately after receiving
:class:`EndOfMessage` or :data:`PAUSED`).

If your I/O is organized around a "push" strategy, where the network
drives processing (e.g. you're using `Twisted
<https://twistedmatrix.com/>`_, or implementing an
:class:`asyncio.Protocol`), then you'll want to internally apply
back-pressure whenever you see :data:`PAUSED`, remove back-pressure
when you call :meth:`~Connection.start_next_cycle`, and otherwise just
deliver events as they arrive. Something like:

.. code-block:: python

   class HTTPProtocol(asyncio.Protocol):
       # Save the transport for later -- needed to access the
       # backpressure API.
       def connection_made(self, transport):
           self._transport = transport

       # Internal helper function -- deliver all pending events
       def _deliver_events(self):
           while True:
               event = self.conn.next_event()
               if event is h11.NEED_DATA:
                   break
               elif event is h11.PAUSED:
                   # Apply back-pressure
                   self._transport.pause_reading()
                   break
               else:
                   self.event_received(event)

       # Called by "someone" whenever new data appears on our socket
       def data_received(self, data):
           self.conn.receive_data(data)
           self._deliver_events()

       # Called by "someone" whenever the peer closes their socket
       def eof_received(self):
           self.conn.receive_data(b"")
           self._deliver_events()
           # asyncio will close our socket unless we return True here.
           return True

       # Called by your code when its ready to start a new
       # request/response cycle
       def start_next_cycle(self):
           self.conn.start_next_cycle()
           # New events might have been buffered internally, and only
           # become deliverable after calling start_next_cycle
           self._deliver_events()
           # Remove back-pressure
           self._transport.resume_reading()

       # Fill in your code here
       def event_received(self, event):
           ...

And your code that uses this will have to remember to check for
:attr:`~Connection.they_are_waiting_for_100_continue` at the
appropriate time.


.. _closing:

Closing connections
-------------------

h11 represents a connection shutdown with the special event type
:class:`ConnectionClosed`. You can send this event, in which case
:meth:`~Connection.send` will simply update the state machine and
then return ``None``. You can receive this event, if you call
``conn.receive_data(b"")``. (The actual receipt might be delayed if
the connection is :ref:`paused <flow-control>`.) It's safe and legal
to call ``conn.receive_data(b"")`` multiple times, and once you've
done this once, then all future calls to
:meth:`~Connection.receive_data` will also return
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
           # data is a bytes-like object to be sent directly
           sock.sendall(data)

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


.. _chunk-delimiters-are-bad:

Chunked Transfer Encoding Delimiters
------------------------------------

.. versionadded:: 0.7.0

HTTP/1.1 allows for the use of Chunked Transfer Encoding to frame request and
response bodies. This form of transfer encoding allows the implementation to
provide its body data in the form of length-prefixed "chunks" of data.

RFC 7230 is extremely clear that the breaking points between chunks of data are
non-semantic: that is, users should not rely on them or assign any meaning to
them. This is particularly important given that RFC 7230 also allows
intermediaries such as proxies and caches to change the chunk boundaries as
they see fit, or even to remove the chunked transfer encoding entirely.

However, for some applications it is valuable or essential to see the chunk
boundaries because the peer implementation has assigned meaning to them. While
this is against the specification, if you do really need access to this
information h11 makes it available to you in the form of the
:data:`Data.chunk_start` and :data:`Data.chunk_end` properties of the
:class:`Data` event.

:data:`Data.chunk_start` is set to ``True`` for the first :class:`Data` event
for a given chunk of data. :data:`Data.chunk_end` is set to ``True`` for the
last :class:`Data` event that is emitted for a given chunk of data. h11
guarantees that it will always emit at least one :class:`Data` event for each
chunk of data received from the remote peer, but due to its internal buffering
logic it may return more than one. It is possible for a single :class:`Data`
event to have both :data:`Data.chunk_start` and :data:`Data.chunk_end` set to
``True``, in which case it will be the only :class:`Data` event for that chunk
of data.

Again, it is *strongly encouraged* that you avoid relying on this information
if at all possible. This functionality should be considered an escape hatch for
when there is no alternative but to rely on the information, rather than a
general source of data that is worth relying on.

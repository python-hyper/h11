.. _API-documentation:

API documentation
=================

.. module:: h11

.. ipython:: python
   :suppress:

   import h11

.. contents::

All of h11's public APIs are exposed directly in the top-level h11
module.


Events
------

*Events* are the core of h11: the whole point of h11 is to let you
reason about HTTP transactions as being a series of events sent back
and forth between a client and a server, instead of thinking in terms
of bytes.

All events behave in essentially similar ways. Let's take
:class:`Request` as an example. It has four fields:
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
bytes-like objects to byte-strings, convert names to lowercase, and
strip whitespace from values:

.. ipython:: python

   original_headers = [("HOST", bytearray(b"  example.com   "))]
   req = h11.Request(method="GET", target="/", headers=original_headers)
   original_headers
   req.headers

If any names are detected with leading or trailing whitespace, then
this is an error ("in the past, differences in the handling of such
whitespace have led to security vulnerabilities" -- `RFC 7230
<https://tools.ietf.org/html/rfc7230#section-3.2.4>`_). We also check
for other protocol violations like the presence of multiple
``Content-Length`` fields, and may add additional checks in the
future.

.. _http_version-format:

:attr:`http_version`: We always represent HTTP version numbers as
byte-strings like b"1.1". Bytes-like objects and native strings will
be automatically converted to byte strings. Note that the HTTP
standard `specifically guarantees
<https://tools.ietf.org/html/rfc7230#section-2.6>`_ that all HTTP
version numbers will consist of exactly two digits separated by a dot,
so comparisons like ``req.http_version < b"1.1"`` are safe and
valid.

When constructing an event, you generally shouldn't specify
:attr:`http_version`, because it defaults to ``b"1.1"``, and if you
attempt to override this to some other value then
:meth:`Connection.send` will reject your event as invalid. But you
might receive events with other values here from remote peers that do
not speak HTTP/1.1.

Here's the complete set of events supported by h11:

.. autoclass:: Request

.. autoclass:: InformationalResponse

.. autoclass:: Response

.. autoclass:: Data

.. autoclass:: EndOfMessage

.. autoclass:: ConnectionClosed

.. autoclass:: Paused


The state machine
-----------------

Important to realize that this isn't one state machine for when we're
a client and a different one for when we're a server: every
:class:`Connection`: object is always tracking *both* state machines.

.. ipython:: python
   :suppress:

   import sys
   import subprocess
   subprocess.check_call([sys.executable, "source/make-state-diagrams.py"])

.. figure:: _static/CLIENT.svg
   :target: _static/CLIENT.svg
   :width: 800px

   State machine for the **client**

.. figure:: _static/SERVER.svg
   :target: _static/SERVER.svg
   :width: 800px

   State machine for the **server**

IDLE, SEND_RESPONSE, SEND_BODY, DONE
MUST_CLOSE, CLOSED
MIGHT_SWITCH_PROTOCOL
SWITCHED_PROTOCOL


The connection object
---------------------

CLIENT, SERVER

Connection


Special topics
--------------

Error handling
..............

Most errors in h11 are signaled by raising :exc:`ProtocolError`:

.. autoexception:: ProtocolError

XX FIXME: add more discussion of what you can/should do after an error


.. _flow-control:

Flow control
............


Message body framing: ``Content-Length`` and all that
.........................................................

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


Re-using a connection (keep-alive)
..................................

Connection: close


.. _closing:

Closing a connection
....................



.. _switching-protocols:

Switching protocols
...................


.. _sendfile:

Sendfile
........

[please don't look at this it doesn't have tests yet. i feel like you
caught me without my clothes on.]

h11
===

This is a little HTTP/1.1 library written from scratch in Python,
inspired by `hyper-h2 <https://lukasa.co.uk/2015/10/The_New_Hyper/>`_.

This is a pure protocol library; like h2, it contains no IO code
whatsoever. (I highly recommend `that blog post for context on what
this means <https://lukasa.co.uk/2015/10/The_New_Hyper/>`_.) Working
with it involves:

1) Creating an ``h11.Connection`` object to track the state of a
   single HTTP/1.1 connection.

2) Writing some code that uses ``conn.data_to_send()`` and
   ``conn.receive_data()`` to shuffle bytes between the
   ``h11.Connection`` and whatever your favorite socket library is
   (could be synchronous, threaded, asynchronous, whatever -- h11
   won't judge you).

3) Then, sending and receiving high-level HTTP "events". (You send
   them with ``conn.send()``, and receive them as the return value
   from ``conn.receive_data()``.) For example, a client might
   instantiate and then send a ``h11.Request`` object, then zero or
   more ``h11.Data`` objects for the request body (e.g., a POST), and
   then a ``h11.EndOfMessage`` to indicate the end of the message, and
   the server would then send back a ``h11.Response``, some
   ``h11.Data``, and its own ``h11.EndOfMessage``. If either side
   tries to violate the protocol, you'll get an exception.

It's suitable for implementing both servers and clients, and has a
pleasingly symmetric API: the events you send as a client are exactly
the ones that you receive as a server and vice-versa.

`Here's an example of a tiny HTTP client
<https://github.com/njsmith/h11/blob/master/tiny-client-demo.py>`_


FAQ
---

*Whyyyyy?*

I got mildly annoyed at some trivial and probably easily fixable
issues in `aiohttp <https://aiohttp.readthedocs.io/>`_, so rather than
spend a few hours debugging them I spent a few days writing my own
HTTP stack from scratch.

*...that's a terrible answer.*

Also I wanted to play with `Curio
<https://curio.readthedocs.io/en/latest/tutorial.html>`_, which has no
HTTP library, and I was feeling inspired by Curio's elegantly
featureful minimalism and h2's elegant architecture.

Also, perhaps most importantly, I was sick and needed a gloriously
pointless yak-shaving project to distract me from all the things I
should have been doing instead.

*Should I use it?*

Probably not; it's just a few days hack at this point.

*Should I play with it?*

Please do! It's fun!

*What are the features/limitations?*

Roughly speaking, it's trying to be a rigorous and architecturally
solid implementation of the first "chapter" of the HTTP/1.1 spec: `RFC
7230: HTTP/1.1 Message Syntax and Routing
<https://tools.ietf.org/html/rfc7230>`_. That is, it mostly focuses on
implementing HTTP at the level of taking bytes on and off the wire,
and the headers related to that, and tries to be anal about spec
conformance. It doesn't know about conditional GETs, range requests,
content negotiation, or URL routing. But it does know how to take care
of framing and cross-version differences in keep-alive handling and
the "obsolete line folding" rule, so you can focus on that other
stuff. (Specifically, the headers it knows about are: ``Connection:``,
``Transfer-Encoding:``, ``Content-Length:``, and ``Expect:`` (which is
really from `RFC 7231
<https://tools.ietf.org/html/rfc7231#section-5.1.1>`_ but whatever).

It's pure Python. Currently it requires Python 3.5, though it wouldn't
be hard to expand this to support other versions, including
2.7. (Originally it had a Cython wrapper for `http-parser
<https://github.com/nodejs/http-parser>`_ and a beautiful nested state
machine implemented with ``yield from`` to postprocess the output. But
I had to take these out -- the new parser is fewer lines-of-code than
the old parser wrapper, is pure Python, uses no exotic language
syntax, and has more features. It's too bad really, that old state
machine was really slick.)

I don't know how fast it is. I haven't benchmarked or profiled it, so
it's probably got some stupid hot spots, and I've been trying to err
on the side of simplicity and robustness instead of
micro-optimization. But I did try to avoid fundamentally bad
decisions, e.g., I believe that all the parsing algorithms are
linear-time (even in the face of pathological input like slowloris)
and there are no byte-by-byte loops.

I worked hard to keep things simple. Currently it's ~700
lines-of-code, and I'm annoyed that I haven't figured out how to make
it simpler. You can easily read and understand the whole thing in less
than an hour.

*How do I try it?*

There's no setup.py or anything at the moment. I'd start with::

  $ git clone git@github.com:njsmith/h11
  $ cd h11
  $ python35 tiny-client-demo.py

and go from there.

*License?*

MIT


Technical minutia for HTTP nerds
--------------------------------

Transfer-Encoding support: we only know ``chunked``, not ``gzip`` or
``deflate``. We're in good company in this: node.js doesn't handle
anything besides ``chunked`` either. So I'm not too worried about
this being a problem in practice. I'm not majorly opposed to adding
support for more features here either, though.

Protocol changing/upgrading: we have full support for transitioning to
a new protocol (e.g. ``Upgrade: websocket`` or ``CONNECT``).

Currently we implement support for "obsolete line folding" when
reading HTTP headers. This is an optional part of the spec --
conforming HTTP/1.1 implementations MUST NOT send continuation lines,
and conforming HTTP/1.1 servers MAY send 400 Bad Request responses
back at clients who do send them (`ref
<https://tools.ietf.org/html/rfc7230#section-3.2.4>`_). I'm tempted to
remove it, though, since it adds some complicated and ugly code right
at the center of the request/response parsing loop, and I'm not sure
whether anyone actually needs it.


Connection lifecycle
....................

We fully support HTTP/1.1 keep-alive.

We have minimal support for HTTP/1.1 pipelining -- basically what's
required by the standard, i.e., in server mode we can handle pipelined
requests in a purely serial manner. Client mode doesn't support it at
all. This seems to be the state of the art in all the major HTTP
implementations; the consensus seems to be that HTTP/1.1 pipelining
was a nice try but broken in practice, and if you really need
pipelining then HTTP/2.0 is the way to go.

The obsolete HTTP/1.0 Connection: keep-alive pseudo-standard is
currently not supported. (Note that this only affects h11 as a server,
because h11 as a client always speaks HTTP/1.1.) Supporting this would
be possible, but it's fragile and finicky and I'm skeptical that
anyone cares. HTTP/1.1 is now almost old enough to vote in the United
States. I get that people sometimes write HTTP/1.0 clients because
they don't want to deal with stuff like chunked encoding, and I
completely sympathize with that, but I'm guessing that the
intersection of people who care desperately about keep-alive and the
people who are too lazy to implement Transfer-Encoding: chunked is
pretty small.

h11
===

.. image:: https://travis-ci.org/njsmith/h11.svg?branch=master
    :target: https://travis-ci.org/njsmith/h11

.. image:: https://codecov.io/gh/njsmith/h11/branch/master/graph/badge.svg
  :target: https://codecov.io/gh/njsmith/h11

This is a little HTTP/1.1 library written from scratch in Python,
heavily inspired by `hyper-h2
<https://lukasa.co.uk/2015/10/The_New_Hyper/>`_.

This is a pure protocol library; like h2, it contains no IO code
whatsoever. This means you can hook h11 up to your favorite network
API, and that could be anything you want: synchronous, threaded,
asynchronous, or your own implementation of `RFC 6214
<https://tools.ietf.org/html/rfc6214>`_ -- h11 won't judge you.
(Compare this to the current state of the art, where every time a `new
network API <https://curio.readthedocs.io/>`_ comes along then someone
gets to start over reimplementing the entire HTTP protocol from
scratch.) Cory Benfield made an `excellent blog post describing this
"bring-your-own I/O" approach
<https://lukasa.co.uk/2015/10/The_New_Hyper/>`_.

This also means that h11 is not immediately useful out of the box:
it's a toolkit for building programs that speak HTTP, not something
that could directly replace ``requests`` or ``twisted.web`` or
whatever. But h11 makes it much easier to implement something like
``requests`` or ``twisted.web``.

At a high level, working with h11 goes like this:

1) First, create an ``h11.Connection`` object to track the state of a
   single HTTP/1.1 connection.

2) When you read data off the network, pass it to
   ``conn.receive_data(...)``; you'll get back a list of objects
   representing high-level HTTP "events".

3) When you want to send a high-level HTTP event, create the
   corresponding "event" object and pass it to ``conn.send(...)``;
   this will give you back some bytes that you can then push out
   through the network.

For example, a client might instantiate and then send a
``h11.Request`` object, then zero or more ``h11.Data`` objects for the
request body (e.g., if this is a POST), and then a
``h11.EndOfMessage`` to indicate the end of the message. Then the
server would then send back a ``h11.Response``, some ``h11.Data``, and
its own ``h11.EndOfMessage``. If either side violates the protocol,
you'll get a ``h11.ProtocolError`` exception.

h11 is suitable for implementing both servers and clients, and has a
pleasantly symmetric API: the events you send as a client are exactly
the ones that you receive as a server and vice-versa.

`Here's an example of a tiny HTTP client
<https://github.com/njsmith/h11/blob/master/tiny-client-demo.py>`_


FAQ
---

*Whyyyyy?*

NIH is fun! Also I got mildly annoyed at some trivial and probably
easily fixable issues in `aiohttp <https://aiohttp.readthedocs.io/>`_,
so rather than spend a few hours debugging them I spent a few days
writing my own HTTP stack from scratch.

*...that's a terrible reason.*

Well, ok... I also wanted to play with `Curio
<https://curio.readthedocs.io/en/latest/tutorial.html>`_, which has no
HTTP library, and I was feeling inspired by Curio's elegantly
featureful minimalism and Cory's call-to-arms blog-post.

And, most importantly, I was sick and needed a gloriously pointless
yak-shaving project to distract me from all the things I should have
been doing instead. Perhaps it won't turn out to be quite as pointless
as all that, but either way at least I learned some stuff.

*Should I use it?*

Probably not; it's just a few-days-old hack at this point.

*Should I play with it?*

Please do! It's fun!

*What are the features/limitations?*

Roughly speaking, it's trying to be a robust, complete, and non-hacky
implementation of the first "chapter" of the HTTP/1.1 spec: `RFC 7230:
HTTP/1.1 Message Syntax and Routing
<https://tools.ietf.org/html/rfc7230>`_. That is, it mostly focuses on
implementing HTTP at the level of taking bytes on and off the wire,
and the headers related to that, and tries to be anal about spec
conformance. It doesn't know about higher-level concerns like URL
routing, conditional GETs, cross-origin cookie policies, or content
negotiation. But it does know how to take care of framing,
cross-version differences in keep-alive handling, and the "obsolete
line folding" rule, so you can focus your energies on the hard /
interesting parts for your application, and it tries to support the
full specification in the sense that any useful HTTP/1.1 conformant
application should be able to use h11.

It's pure Python, and has no dependencies outside of the standard
library.

It has a test suite with 100.0% coverage for both statements and
branches.

Currently it only supports Python 3.5, though it wouldn't be hard to
expand this to support other versions, including 2.7. (Originally it
had a Cython wrapper for `http-parser
<https://github.com/nodejs/http-parser>`_ and a beautiful nested state
machine implemented with ``yield from`` to postprocess the output. But
I had to take these out -- the new *parser* needs fewer lines-of-code
than the old *parser wrapper*, is written in pure Python, uses no
exotic language syntax, and has more features. It's sad, really; that
old state machine was really slick.)

I don't know how fast it is. I haven't benchmarked or profiled it yet,
so it's probably got a few pointless hot spots, and I've been trying
to err on the side of simplicity and robustness instead of
micro-optimization. But at the architectural level I tried hard to
avoid fundamentally bad decisions, e.g., I believe that all the
parsing algorithms remain linear-time even in the face of pathological
input like slowloris, and there are no byte-by-byte loops.

The whole library is ~800 lines-of-code. You can read and understand
the whole thing in less than an hour. Most of the energy invested in
this so far has been spent on trying to keep things simple by
minimizing special-cases and ad hoc state manipulation; even though it
is now quite small and simple, I'm still annoyed that I haven't
figured out how to make it even smaller and simpler. (Unfortunately,
HTTP does not lend itself to simplicity.)

At a more concrete, roadmappy kind of level, my current todo list is:

* Write a manual
* Try using it for some real things

The API is ~feature complete and I don't expect the general outlines
to change much, but you can't judge an API's ergonomics until you
actually document and use it, so I'd expect some changes in the
details.

*How do I try it?*

There's no setup.py or anything at the moment. I'd start with::

  $ git clone git@github.com:njsmith/h11
  $ cd h11
  $ python35 tiny-client-demo.py

and go from there.

*License?*

MIT


Some technical minutia for HTTP nerds
-------------------------------------

Of the headers defined in RFC 7230, the ones h11 knows and has some
special-case logic to care about are: ``Connection:``,
``Transfer-Encoding:``, ``Content-Length:``, ``Host:``, ``Upgrade:``,
and ``Expect:`` (which is really from `RFC 7231
<https://tools.ietf.org/html/rfc7231#section-5.1.1>`_ but
whatever). The other headers in RFC 7230 are ``TE:``, ``Trailer:``,
and ``Via:``; h11 also supports these in the sense that it ignores
them and that's really all it should be doing.

Transfer-Encoding support: we only know ``chunked``, not ``gzip`` or
``deflate``. We're in good company in this: node.js at least doesn't
handle anything besides ``chunked`` either. So I'm not too worried
about this being a problem in practice. But I'm not majorly opposed to
adding support for more features here either.

When parsing chunked encoding, we parse but discard "chunk
extensions". This is an extremely obscure feature that allows
arbitrary metadata to be interleaved into a chunked transfer
stream. This metadata has no standard uses, and proxies are allowed to
strip it out. I don't think anyone will notice this lack, but it could
be added if someone really wants it; I just ran out of energy for
implementing weirdo features no-one uses.

Protocol changing/upgrading: h11 has has full support for
transitioning to a new protocol, via either Upgrade: headers (e.g.,
``Upgrade: websocket``) or the ``CONNECT`` method. Note that this
*doesn't* mean that h11 actually *implements* the WebSocket protocol
-- though a bring-your-own-I/O WebSocket library would indeed be
pretty sweet, someone should definitely implement that. It just means
that h11 has the hooks needed to let you implement hand-off to a
different protocol.

Currently we implement support for "obsolete line folding" when
reading HTTP headers. This is an optional part of the spec --
conforming HTTP/1.1 implementations MUST NOT send continuation lines,
and conforming HTTP/1.1 servers MAY send 400 Bad Request responses
back at clients who do send them (`ref
<https://tools.ietf.org/html/rfc7230#section-3.2.4>`_). I'm tempted to
remove it, since it adds some complicated and ugly code right at the
center of the request/response parsing loop, and I'm not sure whether
anyone actually needs it. Unfortunately a few major implementations
that I spot-checked (node.js, go) do still seem to support it, so it
might or might not be obsolete in practice -- it's hard to know.

Cute trick: we also support ``sendfile``. Or at least, we give you the
tools you need to support ``sendfile``. Specifically, the payload of a
``Data`` event can be any object that has a ``__len__``, and we'll
pass it back out unchanged at the appropriate place in the output
stream. So this is useful for e.g. if you want to use ``os.sendfile``
to send some data: pass in a placeholder object like
``conn.send(Data(data=placeholder), combine=False)`` and you'll get
back a list of things-to-send, which will be a mixture ``bytes``-like
objects containing any framing stuff + your original object. Then your
write loop can be like::

    for piece in data_pieces:
        if isinstance(piece, FilePlaceholder):
            sock.sendfile(*piece.sendfile_args())
        else:
            sock.sendall(piece)


Connection lifecycle
....................

We fully support HTTP/1.1 keep-alive.

We have a little bit of support for HTTP/1.1 pipelining -- basically
the minimum that's required by the standard. In server mode we can
handle pipelined requests in a serial manner, responding completely to
each request before reading the next (and our API is designed to make
it easy for servers to keep this straight). Client mode doesn't
support pipelining at all. As far as I can tell, this matches the
state of the art in all the major HTTP implementations: the consensus
seems to be that HTTP/1.1 pipelining was a nice try but unworkable in
practice, and if you really need pipelining to work then instead of
trying to fix HTTP/1.1 you should switch to HTTP/2.0. (Now that I know
more about how HTTP works internally I'm inclined to agree.)

The HTTP/1.0 Connection: keep-alive pseudo-standard is currently not
supported. (Note that this only affects h11 as a server, because h11
as a client always speaks HTTP/1.1.) Supporting this would be
possible, but it's fragile and finicky and I'm suspicious that if we
leave it out then no-one will notice or care. HTTP/1.1 is now almost
old enough to vote in United States elections. I get that people
sometimes write HTTP/1.0 clients because they don't want to deal with
annoying stuff like chunked encoding, and I completely sympathize with
that, but I'm guessing that you're not going to find too many people
these days who care desperately about keep-alive *and at the same
time* are too lazy to implement Transfer-Encoding: chunked. Still,
this would be my bet as to the missing feature that people are most
likely to eventually complain about...


Trippy state machine diagrams
.............................

We model the state of a HTTP/1.1 connection as a pair of linked state
machines, one for each of the peers. Blue is an "event" sent by that
peer, green is a transition triggered by the (client state, server
state) tuple taking on a particular value, and purple is special
cases. (NB these are slightly out of date. TODO: make the doc build
automatically re-run the code that regenerates these from the
source. Once we have a doc build...)

Client side:

.. image:: https://vorpus.org/~njs/tmp/h11-client-2016-05-04.svg

Server side:

.. image:: https://vorpus.org/~njs/tmp/h11-server-2016-05-04.svg

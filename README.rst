[please don't look at this it doesn't have tests yet. i feel like you
caught me without my clothes on.]

h11
===

This is a little HTTP/1.1 library written from scratch in Python,
inspired by [hyper-h2](https://lukasa.co.uk/2015/10/The_New_Hyper/).

This is a pure protocol library; like h2, it contains no IO code
whatsoever. Working with it involves:

1) Creating an ``h11.Connection`` object to track the state of a
   single HTTP/1.1 connection.

2) Writing some code that uses ``conn.data_to_send()`` and
   ``conn.receive_data()`` to shuffle bytes between the
   ``h11.Connection`` and whatever your favorite socket library is
   (could be synchronous, threaded, asynchronous, whatever, go wild).

3) Sending and receiving high-level HTTP "events". For example, a
   client might send a ``h11.Request`` object, then send some
   ``h11.Data`` objects for the request body (e.g., a POST), and then
   a ``h11.EndOfMessage`` to indicate the end of the message, and the
   server would then send back a ``h11.Response``, some ``h11.Data``,
   and its own ``h11.EndOfMessage``.

It's suitable for implementing both servers and clients, and has a
pleasingly symmetric API: the events you send as a client are exactly
the ones that you receive as a server and vice-versa.

[Here's an example of a tiny HTTP
client](https://github.com/njsmith/h11/blob/master/tiny-client-demo.py)

(Note that the example is a bit sloppy about things like keep-alive,
in ways that you can get away with as a one-shot client.)


FAQ
---

*Whyyyyy?*

I got mildly annoyed at some trivial and probably easily fixable
issues in [aiohttp](https://aiohttp.readthedocs.io/), so rather than
spend a few hours debugging them I spent a few days writing my own
HTTP stack from scratch.

*...that's a terrible answer.*

Also I wanted to play with
[Curio](https://curio.readthedocs.io/en/latest/tutorial.html), which
has no HTTP library, and I was feeling inspired by Curio's elegantly
featureful minimalism and h2's elegant architecture.

Also, perhaps most importantly, I was sick and needed a good
yak-shaving project.

*Should I use it?*

Probably not; it's just a few days hack at this point.

*Should I play with it?*

Please do! It's fun!

*What are the features/limitations?*

It's trying to be a fairly rigorous and architecturally solid
implementation of [RFC 7230: HTTP/1.1 Message Syntax and
Routing]. That is, it mostly focuses on implementing HTTP at the level
of taking bytes on and off the wire, and the headers related to that,
and tries to be anal about spec conformance. It doesn't know about
conditional GETs, range requests, content negotiation, or URL
routing. But it does know how to take care of framing and keep-alive
rules and the obsolete line folding rule, so you can focus on that
other stuff. (Specifically, the headers it knows about are:
``Connection:``, ``Transfer-Encoding:``, ``Content-Length:``, and
``Expect:``.)

It's pure Python. Currently it requires Python 3.5, though it wouldn't
be hard to expand this to support other versions. (Originally it had a
Cython wrapper for
[http-parser](https://github.com/nodejs/http-parser) and a beautiful
nested state machine implemented with ``yield from`` to postprocess
the output. But I had to take these out -- the new parser is fewer
lines-of-code than the old parser wrapper, is pure Python, uses no
exotic language syntax, and has more features. It's too bad really,
that old state machine was really slick.)

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
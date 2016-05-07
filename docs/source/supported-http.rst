Details of our HTTP support for HTTP nerds
==========================================

.. currentmodule:: h11

h11 only speaks HTTP/1.1. It can talk to HTTP/1.0 clients and servers,
but it itself only does HTTP/1.1.

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
trying to fix HTTP/1.1 you should switch to HTTP/2.0.

The HTTP/1.0 ``Connection: keep-alive`` pseudo-standard is currently
not supported. (Note that this only affects h11 as a server, because
h11 as a client always speaks HTTP/1.1.) Supporting this would be
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

A quirk in our :class:`Response` encoding: we don't bother including
ascii status messages -- instead of ``200 OK`` we just say
``200``. This is totally legal and no program should care, and it lets
us skip carrying around a pointless table of status message strings,
but I suppose it might be worth fixing at some point.

When parsing chunked encoding, we parse but discard "chunk
extensions". This is an extremely obscure feature that allows
arbitrary metadata to be interleaved into a chunked transfer
stream. This metadata has no standard uses, and proxies are allowed to
strip it out. I don't think anyone will notice this lack, but it could
be added if someone really wants it; I just ran out of energy for
implementing weirdo features no-one uses.

Currently we *do* implement support for "obsolete line folding" when
reading HTTP headers. This is an optional part of the spec --
conforming HTTP/1.1 implementations MUST NOT send continuation lines,
and conforming HTTP/1.1 servers MAY send 400 Bad Request responses
back at clients who do send them (`ref
<https://tools.ietf.org/html/rfc7230#section-3.2.4>`_). I'm tempted to
remove this support, since it adds some complicated and ugly code
right at the center of the request/response parsing loop, and I'm not
sure whether anyone actually needs it. Unfortunately a few major
implementations that I spot-checked (node.js, go) do still seem to
support reading such headers (but not generating them), so it might or
might not be obsolete in practice -- it's hard to know.

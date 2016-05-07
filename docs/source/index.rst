h11: A pure-Python HTTP/1.1 protocol library
============================================

h11 is an HTTP/1.1 protocol library written in Python, heavily inspired
by `hyper-h2 <https://hyper-h2.readthedocs.io/>`_.

h11's goal is to be a simple, robust, complete, and non-hacky
implementation of the first "chapter" of the HTTP/1.1 spec: `RFC 7230:
HTTP/1.1 Message Syntax and Routing
<https://tools.ietf.org/html/rfc7230>`_. That is, it mostly focuses on
implementing HTTP at the level of taking bytes on and off the wire,
and the headers related to that, and tries to be picky about spec
conformance when possible. It doesn't know about higher-level concerns
like URL routing, conditional GETs, cross-origin cookie policies, or
content negotiation. But it does know how to take care of framing,
cross-version differences in keep-alive handling, and the "obsolete
line folding" rule, and to use bounded time and space to process even
pathological / malicious input, so that you can focus your energies on
the hard / interesting parts for your application. And it tries to
support the full specification in the sense that any useful HTTP/1.1
conformant application should be able to use h11.

This is a "bring-your-own-I/O" protocol library; like h2, it contains
no IO code whatsoever. This means you can hook h11 up to your favorite
network API, and that could be anything you want: synchronous,
threaded, asynchronous, or your own implementation of `RFC 6214
<https://tools.ietf.org/html/rfc6214>`_ -- h11 won't judge you.  This
is h11's main feature compared to the current state of the art, where
every HTTP library is tightly bound to a particular network framework,
and every time a `new network API <https://curio.readthedocs.io/>`_
comes along then someone has to start over reimplementing the entire
HTTP stack from scratch.  We highly recommend `Cory Benfield's
excellent blog post about the advantages of this approach
<https://lukasa.co.uk/2015/10/The_New_Hyper/>`_.

This also means that h11 is not immediately useful out of the box:
it's a toolkit for building programs that speak HTTP, not something
that could directly replace ``requests`` or ``twisted.web`` or
whatever. But h11 makes it much easier to implement something like
``requests`` or ``twisted.web``.

Vital statistics:

* Requirements: Python 3.5 (patches to support earlier versions
  welcome, we just haven't gotten around to it yet)

* Install: *not yet*

* Source: https://github.com/njsmith/h11

* Docs: https://h11.readthedocs.io

* License: MIT


Contents
--------

.. toctree::
   :maxdepth: 2

   basic-usage.rst
   api.rst
   supported-http.rst

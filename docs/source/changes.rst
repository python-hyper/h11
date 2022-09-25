History of changes
==================

.. currentmodule:: h11

.. towncrier release notes start

H11 0.14.0 (2022-09-25)
-----------------------

Features
~~~~~~~~

- Allow additional trailing whitespace in chunk headers for additional
  compatibility with existing servers. (`#133
  <https://github.com/python-hyper/h11/issues/133>`__)
- Improve the type hints for Sentinel types, which should make it
  easier to type hint h11 usage. (`#151
  <https://github.com/python-hyper/h11/pull/151>`__ & `#144
  <https://github.com/python-hyper/h11/pull/144>`__))

Deprecations and Removals
~~~~~~~~~~~~~~~~~~~~~~~~~

- Python 3.6 support is removed. h11 now requires Python>=3.7
  including PyPy 3.  Users running `pip install h11` on Python 2 will
  automatically get the last Python 2-compatible version. (`#138
  <https://github.com/python-hyper/h11/issues/138>`__)


v0.13.0 (2022-01-19)
--------------------

Features
~~~~~~~~

- Clarify that the Headers class is a Sequence and inherit from the
  collections Sequence abstract base class to also indicate this (and
  gain the mixin methods). See also #104. (`#112
  <https://github.com/python-hyper/h11/issues/112>`__)
- Switch event classes to dataclasses for easier typing and slightly
  improved performance. (`#124
  <https://github.com/python-hyper/h11/issues/124>`__)
- Shorten traceback of protocol errors for easier readability (`#132
  <https://github.com/python-hyper/h11/pull/132>`__).
- Add typing including a PEP 561 marker for usage by type checkers
  (`#135 <https://github.com/python-hyper/h11/pull/135>`__).
- Expand the allowed status codes to [0, 999] from [0, 600] (`#134
  https://github.com/python-hyper/h11/issues/134`__).

Backwards **in**\compatible changes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- Ensure request method is a valid token (`#141
  https://github.com/python-hyper/h11/pull/141>`__).


v0.12.0 (2021-01-01)
--------------------

Features
~~~~~~~~

- Added support for servers with broken line endings.

  After this change h11 accepts both ``\r\n`` and ``\n`` as a headers
  delimiter. (`#7 <https://github.com/python-hyper/h11/issues/7>`__)
- Add early detection of invalid http data when request line starts
  with binary (`#122
  <https://github.com/python-hyper/h11/issues/122>`__)


Deprecations and Removals
~~~~~~~~~~~~~~~~~~~~~~~~~

- Python 2.7 and PyPy 2 support is removed. h11 now requires
  Python>=3.6 including PyPy 3.  Users running `pip install h11` on
  Python 2 will automatically get the last Python 2-compatible
  version. (`#114 <https://github.com/python-hyper/h11/issues/114>`__)


v0.11.0 (2020-10-05)
--------------------

New features:

* h11 now stores and makes available the raw header name as
  received. In addition h11 will write out header names with the same
  casing as passed to it. This allows compatibility with systems that
  expect titlecased header names. See `#31
  <https://github.com/python-hyper/h11/issues/31>`__.
* Multiple content length headers are now merged into a single header
  if all the values are equal, if any are unequal a LocalProtocol
  error is raised (as before). See `#92
  <https://github.com/python-hyper/h11/issues/92>`__.

Backwards **in**\compatible changes:

* Headers added by h11, rather than passed to it, now have titlecased
  names. Whilst this should help compatibility it replaces the
  previous lowercased header names.

v0.10.0 (2020-08-14)
--------------------

Other changes:

* Drop support for Python 3.4.
* Support Python 3.8.
* Make error messages returned by match failures less ambiguous (`#98
  <https://github.com/python-hyper/h11/issues/98>`__).

v0.9.0 (2019-05-15)
-------------------

Bug fixes:

* Allow a broader range of characters in header values. This violates
  the RFC, but is apparently required for compatibility with
  real-world code, like Google Analytics cookies (`#57
  <https://github.com/python-hyper/h11/issues/57>`__, `#58
  <https://github.com/python-hyper/h11/issues/58>`__).
* Validate incoming and outgoing request paths for invalid
  characters. This prevents a variety of potential security issues
  that have affected other HTTP clients. (`#69
  <https://github.com/python-hyper/h11/pull/69>`__).
* Force status codes to be integers, thereby allowing stdlib
  HTTPStatus IntEnums to be used when constructing responses (`#72
  <https://github.com/python-hyper/h11/issues/72>`__).

Other changes:

* Make all sentinel values inspectable by IDEs, and split
  ``SEND_BODY_DONE`` into ``SEND_BODY``, and ``DONE`` (`#75
  <https://github.com/python-hyper/h11/pull/75>`__).
* Drop support for Python 3.3.
* LocalProtocolError raised in start_next_cycle now shows states for
  more informative errors (`#80
  <https://github.com/python-hyper/h11/issues/80>`__).

v0.8.1 (2018-04-14)
-------------------

Bug fixes:

* Always return headers as ``bytes`` objects (`#60
  <https://github.com/python-hyper/h11/issues/60>`__)

Other changes:

* Added proper license notices to the Javascript used in our
  documentation (`#61
  <https://github.com/python-hyper/h11/issues/60>`__)


v0.8.0 (2018-03-20)
-------------------

Backwards **in**\compatible changes:

* h11 now performs stricter validation on outgoing header names and
  header values: illegal characters are now rejected (example: you
  can't put a newline into an HTTP header), and header values with
  leading/trailing whitespace are also rejected (previously h11 would
  silently discard the whitespace). All these checks were already
  performed on incoming headers; this just extends that to outgoing
  headers.

New features:

* New method :meth:`Connection.send_failed`, to notify a
  :class:`Connection` object when data returned from
  :meth:`Connection.send` was *not* sent.

Bug fixes:

* Make sure that when computing the framing headers for HEAD
  responses, we produce the same results as we would for the
  corresponding GET.

* Error out if a request has multiple Host: headers.

* Send the Host: header first, as recommended by RFC 7230.

* The Expect: header `is case-insensitive
  <https://tools.ietf.org/html/rfc7231#section-5.1.1>`__, so use
  case-insensitive matching when looking for 100-continue.

Other changes:

* Better error messages in several cases.

* Provide correct ``error_status_hint`` in exception raised when
  encountering an invalid ``Transfer-Encoding`` header.

* For better compatibility with broken servers, h11 now tolerates
  responses where the reason phrase is missing (not just empty).

* Various optimizations and documentation improvements.


v0.7.0 (2016-11-25)
-------------------

New features (backwards compatible):

* Made it so that sentinels are :ref:`instances of themselves
  <sentinel-type-trickiness>`, to enable certain dispatch tricks on
  the return value of :func:`Connection.next_event` (see `issue #8
  <https://github.com/python-hyper/h11/issues/8>`__ for discussion).

* Added :data:`Data.chunk_start` and :data:`Data.chunk_end` properties
  to the :class:`Data` event. These provide the user information
  about where chunk delimiters are in the data stream from the remote
  peer when chunked transfer encoding is in use. You :ref:`probably
  shouldn't use these <chunk-delimiters-are-bad>`, but sometimes
  there's no alternative (see `issue #19
  <https://github.com/python-hyper/h11/issues/19>`__ for discussion).

* Expose :data:`Response.reason` attribute, making it possible to read
  or set the textual "reason phrase" on responses (`issue #13
  <https://github.com/python-hyper/h11/pull/13>`__).

Bug fixes:

* Fix the error message given when a call to an event constructor is
  missing a required keyword argument (`issue #14
  <https://github.com/python-hyper/h11/issues/14>`__).

* Fixed encoding of empty :class:`Data` events (``Data(data=b"")``)
  when using chunked encoding (`issue #21
  <https://github.com/python-hyper/h11/issues/21>`__).

v0.6.0 (2016-10-24)
-------------------

This is the first release since we started using h11 to write
non-trivial server code, and this experience triggered a number of
substantial API changes.

Backwards **in**\compatible changes:

* Split the old :meth:`receive_data` into the new
  :meth:`~Connection.receive_data` and
  :meth:`~Connection.next_event`, and replaced the old :class:`Paused`
  pseudo-event with the new :data:`NEED_DATA` and :data:`PAUSED`
  sentinels.

* Simplified the API by replacing the old :meth:`Connection.state_of`,
  :attr:`Connection.client_state`, :attr:`Connection.server_state` with
  the new :attr:`Connection.states`.

* Renamed the old :meth:`prepare_to_reuse` to the new
  :meth:`~Connection.start_next_cycle`.

* Removed the ``Paused`` pseudo-event.

Backwards compatible changes:

* State machine: added a :data:`DONE` -> :data:`MUST_CLOSE` transition
  triggered by our peer being in the :data:`ERROR` state.

* Split :exc:`ProtocolError` into :exc:`LocalProtocolError` and
  :exc:`RemoteProtocolError` (see :ref:`error-handling`). Use case: HTTP
  servers want to be able to distinguish between an error that
  originates locally (which produce a 500 status code) versus errors
  caused by remote misbehavior (which produce a 4xx status code).

* Changed the :data:`PRODUCT_ID` from ``h11/<verson>`` to
  ``python-h11/<version>``. (This is similar to what requests uses,
  and much more searchable than plain h11.)

Other changes:

* Added a minimal benchmark suite, and used it to make a few small
  optimizations (maybe ~20% speedup?).


v0.5.0 (2016-05-14)
-------------------

* Initial release.

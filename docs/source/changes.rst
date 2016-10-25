History of changes
==================

.. currentmodule:: h11

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

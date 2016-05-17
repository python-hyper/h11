History of changes
==================

.. currentmodule:: h11

vNEXT (????-??-??)
------------------

Backwards **in**\compatible changes:

* Simplified the API by replacing the old :meth:`Connection.state_of`,
  :attr:`Connection.client_state`, :attr:`Connection.server_state` with
  the new :attr:`Connection.states`.

* Removed the :class:`Paused` pseudo-event -- see :ref:`flow-control`
  for the new way things work.

Backwards compatible changes:

* State machine: added a :data:`DONE` -> :data:`MUST_CLOSE` transition
  triggered by our peer being in the :data:`ERROR` state.

* Split :exc:`ProtocolError` into :exc:`LocalProtocolError` and
  :exc:`RemoteProtocolError` (see :ref:`error-handling`). Use case: HTTP
  servers want to be able to distinguish between an error that
  originates locally (which produce a 500 status code) versus errors
  caused by remote misbehavior (which produce a 4xx status code).


v0.5.0 (2016-05-14)
-------------------

* Initial release.

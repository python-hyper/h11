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

Error reporting
---------------

.. autoexception:: ProtocolError


Events
------

General handling: headers, method / target / http_version

Request
Data
EndOfMessage

InformationalResponse
Response

ConnectionClosed
Paused


The state machine
-----------------

Important to realize that this isn't one state machine for when we're
a client and a different one for when we're a server: every
:class:`Connection`: object is always tracking *both* state machines.

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

Message body framing, or, ``Content-Length`` and all that
.........................................................




Re-using a connection (keep-alive)
..................................

Connection: close


Closing
.......



Switching protocols
...................




Sendfile
........

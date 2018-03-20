Examples
========

..
   If we add any more examples then we should probably split this out
   into separate pages for each example

You can also find these in the `examples/ directory of a source
checkout <https://github.com/python-hyper/h11/tree/master/examples>`_.

Minimal client, using synchronous I/O
-------------------------------------

.. literalinclude:: ../../examples/basic-client.py
   :language: python


Fairly complete server with error handling, using Trio for async I/O
--------------------------------------------------------------------

.. literalinclude:: ../../examples/trio-server.py
   :language: python

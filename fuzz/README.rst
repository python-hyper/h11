Some harness code for using `afl <http://lcamtuf.coredump.cx/afl/>`_
and `python-afl <http://jwilk.net/software/python-afl>`_ to fuzz-test
h11.

See `Alex Gaynor's tutorial
<https://alexgaynor.net/2015/apr/13/introduction-to-fuzzing-in-python-with-afl/>`_,
or just:

.. code-block:: sh

   sudo apt install afl
   pip install python-afl
   cd fuzz
   PYTHONPATH=.. py-afl-fuzz -o results -i afl-server-examples/ -- python ./afl-server.py

Note 1: You may need to add ``AFL_SKIP_CPUFREQ=1`` if you want to play
with it on a laptop and don't want to bother messing with your cpufreq
config.

Note 2: You may see some false "hangs" due to afl's aggressive default
timeouts. I think this might be intentional, and serve to discourage
afl from wasting time exploring arbitrarily longer and longer inputs?
Or you can set the timeout explicitly with ``-t $MILLISECONDS``.

Note 3: `Parallel fuzzing is a good thing
<https://github.com/mirrorer/afl/blob/master/docs/parallel_fuzzing.txt>`_.

Right now we just have a simple test that throws garbage at the server
``receive_data`` and makes sure that it's either accepted or raises
``ProtocolError``, never any other exceptions. (Here's a `bug in
gunicorn <https://github.com/benoitc/gunicorn/issues/1023>`_ that was
found by applying this technique to gunicorn.)

Ideas for further additions
---------------------------

* Teach afl-server.py to watch the state machine and send responses
  back to get things unpaused, to allow for fuzzing of pipelined and
  unsuccessful protocol switches

* Add a client-side fuzzer too

* Add a `dictionary
  <https://lcamtuf.blogspot.com/2015/01/afl-fuzz-making-up-grammar-with.html>`_
  tuned for HTTP

* add more seed examples: ``Connection: close``? more complicated chunked
  examples? pipelining and protocol switch examples?

* check that the all-at-once and byte-by-byte processing give the same
  event stream (modulo ``Paused``, and data splits)

* maybe should split apart fancy checks versus non-fancy checks b/c speed is
  important

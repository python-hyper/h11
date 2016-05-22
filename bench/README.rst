Benchmarking h11
================

See the `asv docs <http://asv.readthedocs.io/en/latest/>`_ for how to
run our (currently very simple) benchmark suite and track speed
changes over time.

E.g.:

* ``PYTHONPATH=.. asv bench``

Or for cases that asv doesn't handle too well (hit control-C when
bored of watching numbers scroll):

* ``PYTHONPATH=.. pypy benchmarks/benchmarks.py``

* ``PYTHONPATH=.. python -m vmprof --web benchmarks/benchmarks.py``

* ``PYTHONPATH=.. pypy -m vmprof --web benchmarks/benchmarks.py``

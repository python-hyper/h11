<!-- https://github.com/nayafia/contributing-template/blob/master/CONTRIBUTING-template.md -->

# Contributing to h11

Thanks for your interest in contributing to h11! Please take a moment
to review this document in order to make the contribution process easy
and effective for everyone involved.

Following these guidelines helps to communicate that you respect the
time of the developers managing and developing this free and open
source project. In return, they should reciprocate that respect in
addressing your issue, assessing changes, and helping you finalize
your pull requests.


## What we're looking for

h11 is largely feature-complete, in the sense that it has a fairly
well-defined scope and (as far as we know) implements pretty much
everything that fits within that scope. If we're wrong, please let us
know :-). But mostly we're not looking for major new features. On the
other hand, the following are all very welcome:

* Bug reports and bug fixes

* API feedback and suggestions, especially based on experience using
  h11

* Help making the docs more clear, complete, and generally useful

* Good examples of using h11 in different settings (e.g. with twisted,
  with asyncio, ...) to accomplish different tasks

* Improvements in test coverage

* Patches that make the code simpler

* Patches that make the code faster


## Contributor responsibilities

* Code should work across all currently supported Python releases.

* Code must be formatted using
  [black](https://github.com/python/black) and
  [isort](https://github.com/timothycrosley/isort) as configured in
  the project. With those projects installed the commands,

      black h11/ bench/ examples/ fuzz/
      isort --profile black --dt h11 bench examples fuzz

  will format your code for you.

* If you change the code, then you have to also add or fix at least
  one test. (See below for how to run the test suite.) This helps us
  make sure that we won't later accidentally break whatever you just
  fixed, and undo your hard work.

* [Statement and branch coverage](https://codecov.io/gh/python-hyper/h11)
  needs to remain at 100.0%. But don't stress too much about making
  this work up front -- if you post a pull request, then the codecov
  bot will automatically post a reply letting you know whether you've
  managed this, and you can iterate to improve it.

* The test suite needs to pass. The easy way to check is:

  ```
  pip install tox
  tox
  ```

  But note that: (1) this might print slightly misleading coverage
  statistics, because it only shows coverage for individual python
  versions, and there might be some lines that are only executed on some
  python versions or implementations, and (2) the full test suite will
  automatically get run when you submit a pull request, so you don't
  need to worry too much about tracking down a version of cpython 3.3
  or whatever just to run the tests.

* Proposed speedups require some profiling and benchmarks to justify
  the change.

* Generally each pull request should be self-contained and fix one bug
  or implement one new feature. If you can split it up, then you
  probably should. This makes changes easier to review, and helps us
  merge things as quickly as possible.

* Be welcoming to newcomers and encourage diverse new contributors
  from all backgrounds.

* Respect our
  [code of conduct](https://github.com/python-hyper/h11/blob/master/CODE_OF_CONDUCT.md>)
  in all project spaces.


## How to submit a contribution

You don't have to sign a license agreement or anything to contribute
to h11 -- just make your changes and submit a pull request! (Though
you should probably review the
[MIT license we use](https://github.com/python-hyper/h11/blob/master/LICENSE.txt)
and make sure you're happy licensing your contribution under those
terms.)

If you're new to Github and pull requests, then are some tutorials on
how to get started:

* [Make a pull request](http://makeapullrequest.com/)

* [How to contribute to an Open Source Project on GitHub](https://egghead.io/series/how-to-contribute-to-an-open-source-project-on-github)


### Release notes

We use towncrier to manage our release notes. Basically, every pull
request that has a user visible effect should add a short file to the
newsfragments/ directory describing the change, with a name like
<ISSUE NUMBER>.<TYPE>.rst. See newsfragments/README.rst for
details. This way we can keep a good list of changes as we go, which
makes the release manager happy, which means we get more frequent
releases, which means your change gets into users’ hands faster.


## After you submit a PR

We'll try to review it promptly and give feedback -- but if you
haven't heard from us after a week, please do send a ping! It's
totally fine and normal to post a comment that just says "ping".

If your PR needs further changes before it can be merged, just make
more changes in your branch and push them to Github -- Github will
automatically add your new commits to the existing PR. But Github
*won't* automatically *tell* anyone that new commits have been added,
so after you've fixed things and are ready for people to take another
look, then please post a comment saying so! That will send us a
notification so we know to take another look.

## And again, thanks!

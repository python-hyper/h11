import sys
from setuptools import setup

# defines __version__
exec(open("h11/_version.py").read())

setup(
    name="h11",
    version=__version__,
    description=
        "A pure-Python, bring-your-own-I/O implementation of HTTP/1.1",
    long_description=open("README.rst").read(),
    author="Nathaniel J. Smith",
    author_email="njs@pobox.com",
    license="MIT",
    packages=["h11"],
    url="https://github.com/njsmith/h11",
    classifiers = [
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Topic :: Internet :: WWW/HTTP",
        "Topic :: System :: Networking",
        ],
)

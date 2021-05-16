from setuptools import setup, find_packages

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
    packages=find_packages(),
    url="https://github.com/python-hyper/h11",
    # This means, just install *everything* you see under h11/, even if it
    # doesn't look like a source file, so long as it appears in MANIFEST.in:
    include_package_data=True,
    python_requires=">=3.6",
    install_requires=["dataclasses; python_version < '3.7'"],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Topic :: Internet :: WWW/HTTP",
        "Topic :: System :: Networking",
    ],
)

from setuptools import setup, find_packages

setup(
    # TODO: move h11.tests into a tests directory?
    packages=find_packages(exclude=["h11.tests"]),
)

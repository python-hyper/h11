# hack hack
# for now just expects you to have libhttp-parser-dev installed

# TODO:
# - make proper package (or move into rakaia or something)
# - vendor libhttp_parser
# - expose the version of vendored libhttp_parser

from setuptools import setup, Extension
from Cython.Distutils import build_ext
import os.path

setup(
    cmdclass = {'build_ext': build_ext},
    ext_modules = [
      Extension("h11._libhttp_parser",
                ["h11/_libhttp_parser.pyx"],
                language="c",
                libraries=["http_parser"],
                )
      ],
)

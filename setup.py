# hack hack
# for now just expects you to have libhttp-parser-dev installed

from setuptools import setup, Extension
from Cython.Distutils import build_ext
import os.path

setup(
    cmdclass = {'build_ext': build_ext},
    ext_modules = [
      Extension("_http_parser",
                ["_http_parser.pyx"],
                language="c",
                libraries=["http_parser"],
                )
      ],
)

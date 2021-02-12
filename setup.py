#!/usr/bin/env python

from distutils.core import setup

setup(name='tornettools',
      version="1.1.0",
      description='A utility to generate private Tor network configurations',
      author='Rob Jansen',
      url='https://github.com/shadow/tornettools',
      packages=['tornettools'],
      scripts=['tornettools/tornettools'],
     )

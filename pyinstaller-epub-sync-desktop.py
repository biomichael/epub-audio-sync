#!/usr/bin/env python
# coding=utf-8

from __future__ import absolute_import
from __future__ import print_function
import sys

from aeneas.epubsync.desktop_api import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

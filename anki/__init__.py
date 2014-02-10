# -*- coding: utf-8 -*-
# Copyright: Damien Elmes <anki@ichi2.net>
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import sys
import os
import platform

if sys.version_info[0] > 3:
    raise Exception("Anki should be run with Python 3")

import json as json

version="2.0.17" # build scripts grep this line, so preserve formatting
__all__ = []

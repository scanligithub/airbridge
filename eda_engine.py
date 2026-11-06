# eda_engine.py (v2.4 - 含多版本进程沙箱 + Tcl/SKILL 跨版本 API Polyfill 垫片层)
# -*- coding: utf-8 -*-
import ctypes
import glob
import os
import re
import shutil
import string
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

user32 = ctypes.windll.user32 if sys.platform == "win32" else None


class EDALanguage:
  ORCAD_TCL = "ORCAD_TCL"
  ALLEGRO_SKILL = "ALLEGRO_SKILL"
  UNKNOWN = "UNKNOWN"

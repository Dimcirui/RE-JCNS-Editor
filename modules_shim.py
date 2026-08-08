"""
modules_shim.py
---------------
Single place that puts `modules/` on sys.path and hands back the pure-Python
helpers living there.

Several files need jcns_mapping / jcns_parser and each used to repeat its own
_ensure_modules_path().  Centralising it means the path is inserted once, and
the driver-namespace function can grab the evaluator without paying for a path
check on every evaluation.
"""

import os
import sys

_MODULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules")
_mapping = None
_mirror = None
_flags = None


def ensure_path():
    if _MODULES not in sys.path:
        sys.path.insert(0, _MODULES)


def get_mapping():
    """modules/jcns_mapping.py, imported once and cached."""
    global _mapping
    if _mapping is None:
        ensure_path()
        import jcns_mapping
        _mapping = jcns_mapping
    return _mapping


def get_mirror():
    """modules/jcns_mirror.py, imported once and cached."""
    global _mirror
    if _mirror is None:
        ensure_path()
        import jcns_mirror
        _mirror = jcns_mirror
    return _mirror


def get_flags():
    """modules/jcns_flags.py, imported once and cached."""
    global _flags
    if _flags is None:
        ensure_path()
        import jcns_flags
        _flags = jcns_flags
    return _flags

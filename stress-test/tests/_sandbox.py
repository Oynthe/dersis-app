"""Sandbox bootstrap for DERSIS stress-test probes.

MUST be imported (and sandbox() called) BEFORE any scheduler_app import,
because scheduler_app.storage binds ~/Documents/Dersis at import time.

Usage (at the very top of every probe, before importing scheduler_app):

    import _sandbox
    _sandbox.enter()            # sets HOME/USERPROFILE to a fresh temp dir
    import sys
    sys.path.insert(0, r"C:\\dev\\dersis-app")
    sys.path.insert(0, r"C:\\dev\\dersis-app\\stress-test\\tests")
"""
import os
import sys
import tempfile


def enter():
    """Point HOME/USERPROFILE at a fresh temp dir so no real ~/Documents write."""
    d = tempfile.mkdtemp(prefix="dersis_audit_")
    os.environ["HOME"] = d
    os.environ["USERPROFILE"] = d
    # os.path.expanduser on Windows also consults HOMEDRIVE+HOMEPATH
    drive, tail = os.path.splitdrive(d)
    os.environ["HOMEDRIVE"] = drive
    os.environ["HOMEPATH"] = tail
    # Repo root + tests dir on path
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    tests = os.path.abspath(os.path.dirname(__file__))
    for p in (repo, tests):
        if p not in sys.path:
            sys.path.insert(0, p)
    return d

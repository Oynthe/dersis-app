"""Private sandbox bootstrap for the error-handling / edge-case probes.

Uniquely named (_eh_) so it does not collide with the shared _sandbox.py that
other audit agents mutate concurrently. Call enter() BEFORE importing
scheduler_app so storage._ROOT_DIR binds to a throwaway temp dir.
"""
import os
import sys
import tempfile


def enter():
    d = tempfile.mkdtemp(prefix="dersis_eh_audit_")
    os.environ["HOME"] = d
    os.environ["USERPROFILE"] = d
    drive, tail = os.path.splitdrive(d)
    os.environ["HOMEDRIVE"] = drive
    os.environ["HOMEPATH"] = tail
    here = os.path.dirname(__file__)
    repo = os.path.abspath(os.path.join(here, "..", ".."))
    tests = os.path.abspath(here)
    for p in (repo, tests):
        if p not in sys.path:
            sys.path.insert(0, p)
    return d

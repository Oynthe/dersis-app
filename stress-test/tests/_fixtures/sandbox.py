"""Sandbox bootstrap: isolate HOME/USERPROFILE before importing scheduler_app.

Import this FIRST in every probe (before any scheduler_app import) so that
storage._ROOT_DIR binds to a throwaway temp dir, never ~/Documents/Dersis.
"""
import os
import sys
import tempfile

_sandbox = tempfile.mkdtemp(prefix="dersis_audit_")
os.environ["HOME"] = _sandbox
os.environ["USERPROFILE"] = _sandbox

# Repo root + tests dir on path
_here = os.path.dirname(__file__)
_repo = os.path.abspath(os.path.join(_here, "..", "..", ".."))
_tests = os.path.abspath(os.path.join(_here, ".."))
for p in (_repo, _tests):
    if p not in sys.path:
        sys.path.insert(0, p)

SANDBOX_DIR = _sandbox

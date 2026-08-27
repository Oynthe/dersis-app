"""Risk 1 (Critical): Excel import UI success path calls undefined methods.

app.py:4525-4526 call self._on_state_changed() and self.refresh() after a
successful import. If SchedulerApp (and its Qt bases) do not define those,
a successful import raises AttributeError -> the app crashes on SUCCESS.

Static check only: import the class and inspect attributes. No QApplication
is needed to read class attributes / MRO.
"""
import os, sys, tempfile

# ── mandatory sandbox (storage binds ~/Documents/Dersis at import) ──
_sb = tempfile.mkdtemp(prefix="dersis_probe01_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")

from scheduler_app.ui.app import SchedulerApp

# Methods referenced on the import success path (app.py:4512-4531)
CANDIDATES = ["_on_state_changed", "refresh", "refresh_grid",
              "_update_status", "_push_undo"]

print("=== Risk 1: SchedulerApp attribute presence (import success path) ===")
print("MRO:", [c.__name__ for c in SchedulerApp.__mro__])
for name in CANDIDATES:
    present = hasattr(SchedulerApp, name)
    print(f"  hasattr(SchedulerApp, {name!r}) = {present}")

missing = [n for n in ("_on_state_changed", "refresh") if not hasattr(SchedulerApp, n)]
print("\nMISSING methods called at app.py:4525-4526:", missing)
print("VERDICT:", "CONFIRMED CRASH ON SUCCESS" if missing else "no missing methods")

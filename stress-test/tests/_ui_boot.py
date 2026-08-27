"""Shared bootstrap for UI-behavior stress probes.

Sets up a sandboxed HOME/USERPROFILE (so storage never touches the real
~/Documents/Dersis), builds a QApplication on the NATIVE platform, seeds
language + institutional tier, and constructs SchedulerApp WITHOUT show().

Every probe imports boot() from here.  Each probe passes a unique sandbox
sub-name so parallel runs never collide.
"""
import os
import sys
import tempfile


def make_sandbox(name):
    root = os.path.join(tempfile.gettempdir(), "dersis-audit-sandbox", name)
    os.makedirs(os.path.join(root, "Documents"), exist_ok=True)
    os.environ["HOME"] = root
    os.environ["USERPROFILE"] = root
    return root


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def boot(sandbox_name, lang="tr"):
    """Return (app, window, QtWidgets, QtCore) with a constructed SchedulerApp."""
    sandbox = make_sandbox(sandbox_name)
    root = repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    tests_dir = os.path.join(root, "stress-test", "tests")
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)

    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    from scheduler_app.app import SchedulerApp, apply_light_palette
    apply_light_palette(app)

    from scheduler_app.translations import set_language
    set_language(lang)

    from scheduler_app.ui.tier_enforcement import TierEnforcement
    from scheduler_app.plans import TIER_INSTITUTIONAL
    TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)

    window = SchedulerApp()
    for _ in range(3):
        app.processEvents()
    return app, window, sandbox


def load_state(app, window, state):
    """Install a fixture state into the window and do a full refresh."""
    window.state_data = state
    if hasattr(window, "_workflow"):
        window._workflow.state = state
    window._undo_stack.clear()
    window._redo_stack.clear()
    window.refresh_grid()
    for _ in range(3):
        app.processEvents()


def greedy_place(state, fraction=1.0):
    """Deterministically place classes onto (day, slot, room) with no overlap
    checking beyond simple room-time occupancy. Enough to exercise UI paths."""
    from scheduler_app.core.models import mark_placed
    days = state["days"]
    slots = state["slots"]
    rooms = state["classrooms"]
    occ = set()  # (day, slot, room)
    placed = 0
    target = int(len(state["classes"]) * fraction)
    for cls in state["classes"]:
        if placed >= target:
            break
        if cls.get("location_type") == "online":
            continue
        dur = max(1, int(cls.get("duration", 1)))
        done = False
        for d in days:
            for si in range(len(slots) - dur + 1):
                for r in rooms:
                    block = [(d, slots[si + k], r) for k in range(dur)]
                    if any(b in occ for b in block):
                        continue
                    for b in block:
                        occ.add(b)
                    mark_placed(cls, d, slots[si], r)
                    placed += 1
                    done = True
                    break
                if done:
                    break
            if done:
                break
    return placed

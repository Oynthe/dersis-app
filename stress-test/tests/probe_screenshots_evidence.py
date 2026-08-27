"""Capture evidence screenshots (native platform, grab(), no show()):
  1. main window with a loaded/placed schedule on the timetable grid tab
  2. the Dashboard tab
Saved under stress-test/evidence/.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ui_boot import boot, load_state, greedy_place, repo_root  # noqa: E402

sys.path.insert(0, os.path.join(repo_root(), "stress-test", "tests"))
from _fixtures.dataset_gen import make_preset  # noqa: E402


def main():
    app, window, sandbox = boot("screens")
    window.resize(1400, 900)
    st = make_preset("normal", seed=11)
    load_state(app, window, st)
    n = greedy_place(st, fraction=0.7)
    load_state(app, window, st)

    ev = os.path.join(repo_root(), "stress-test", "evidence")
    os.makedirs(ev, exist_ok=True)

    # 1) grid tab (classroom view)
    window.notebook.setCurrentIndex(0)
    for _ in range(6):
        app.processEvents()
    p1 = os.path.join(ev, "ui-main-window-loaded-schedule.png")
    window.grab().save(p1)

    # 2) dashboard tab
    dash_idx = window.notebook.count() - 1
    window.notebook.setCurrentIndex(dash_idx)
    for _ in range(6):
        app.processEvents()
    p2 = os.path.join(ev, "ui-dashboard-tab.png")
    window.grab().save(p2)

    print(f"placed={n}")
    print(f"screenshot1={p1} exists={os.path.exists(p1)} bytes={os.path.getsize(p1)}")
    print(f"screenshot2={p2} exists={os.path.exists(p2)} bytes={os.path.getsize(p2)}")
    window.close()


if __name__ == "__main__":
    main()

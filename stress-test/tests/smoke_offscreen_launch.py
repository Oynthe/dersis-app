"""ST smoke test: launch the full DERSIS main window offscreen in a sandboxed HOME.

Verifies the app can be instantiated headlessly (basis for all UI-level stress
tests), measures cold construction time, and captures a screenshot of the main
window as evidence.

Run from repo root:
    .venv-audit/Scripts/python.exe stress-test/tests/smoke_offscreen_launch.py
"""
import os
import sys
import tempfile
import time

# ── Sandbox: never touch the real ~/Documents/Dersis ─────────────────────
SANDBOX = os.path.join(tempfile.gettempdir(), "dersis-audit-sandbox", "smoke")
os.makedirs(os.path.join(SANDBOX, "Documents"), exist_ok=True)
os.environ["HOME"] = SANDBOX
os.environ["USERPROFILE"] = SANDBOX
os.environ["QT_QPA_PLATFORM"] = "offscreen"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main():
    t0 = time.perf_counter()
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    t_qt = time.perf_counter()

    from scheduler_app.app import SchedulerApp, apply_light_palette
    apply_light_palette(app)
    t_import = time.perf_counter()

    # Pre-seed language so the first-run language gate does not block.
    from scheduler_app.translations import set_language
    set_language("tr")

    from scheduler_app.ui.tier_enforcement import TierEnforcement
    from scheduler_app.plans import TIER_INSTITUTIONAL
    TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)

    window = SchedulerApp()
    window.show()
    t_window = time.perf_counter()

    for _ in range(5):
        app.processEvents()

    evidence = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evidence")
    os.makedirs(evidence, exist_ok=True)
    pm = window.grab()
    shot = os.path.join(evidence, "smoke-main-window.png")
    pm.save(shot)
    t_end = time.perf_counter()

    print(f"RESULT: OK")
    print(f"qt_app_init_s={t_qt - t0:.3f}")
    print(f"scheduler_app_import_s={t_import - t_qt:.3f}")
    print(f"main_window_construct_s={t_window - t_import:.3f}")
    print(f"total_s={t_end - t0:.3f}")
    print(f"window_size={window.width()}x{window.height()}")
    print(f"screenshot={shot}")
    print(f"sandbox_dirs={os.listdir(os.path.join(SANDBOX, 'Documents', 'Dersis')) if os.path.isdir(os.path.join(SANDBOX, 'Documents', 'Dersis')) else 'MISSING'}")
    window.close()
    app.quit()


if __name__ == "__main__":
    main()

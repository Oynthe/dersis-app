"""Load a realistic schedule into SchedulerApp and capture each major view as
PNG evidence for the UI/UX audit. Native platform, no show(), grab() renders
hidden widgets with real fonts.

    .venv-audit/Scripts/python.exe stress-test/tests/capture_screens.py
"""
import os
import sys
import tempfile

SANDBOX = os.path.join(tempfile.gettempdir(), "dersis-audit-sandbox", "screens")
os.makedirs(os.path.join(SANDBOX, "Documents"), exist_ok=True)
os.environ["HOME"] = SANDBOX
os.environ["USERPROFILE"] = SANDBOX

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "stress-test", "tests"))

EVID = os.path.join(REPO, "stress-test", "evidence")
os.makedirs(EVID, exist_ok=True)


def main():
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from scheduler_app.app import SchedulerApp, apply_light_palette
    apply_light_palette(app)
    from scheduler_app.translations import set_language
    set_language("tr")
    from scheduler_app.ui.tier_enforcement import TierEnforcement
    from scheduler_app.plans import TIER_INSTITUTIONAL
    TierEnforcement.instance().set_tier(TIER_INSTITUTIONAL)

    from _fixtures.dataset_gen import make_preset
    from scheduler_app.core.workflow import SchedulingWorkflow

    state = make_preset("normal", seed=7)
    wf = SchedulingWorkflow(state, lambda: None)
    res = wf.reschedule({}, use_cpsat=False)
    wf.apply_reschedule(res)
    placed = sum(1 for c in state["classes"] if c["placed"])
    print(f"loaded normal preset: {placed}/{len(state['classes'])} placed")

    w = SchedulerApp()
    w.state_data = state
    w._workflow.state = state
    w.resize(1400, 860)
    w.refresh_grid()
    for _ in range(8):
        app.processEvents()

    tabs = {0: "view-classroom", 1: "view-group", 2: "view-lecturer",
            3: "view-everything", 4: "dashboard"}
    for idx, name in tabs.items():
        try:
            w.notebook.setCurrentIndex(idx)
            w._render_current_tab()
        except Exception as e:
            print(f"tab {idx} render error: {type(e).__name__}: {e}")
        for _ in range(6):
            app.processEvents()
        path = os.path.join(EVID, f"screen-{idx}-{name}.png")
        w.grab().save(path)
        print("saved", path)

    # Sidebar: unplaced classes panel
    try:
        w.notebook.setCurrentIndex(0)
        app.processEvents()
        w.grab().save(os.path.join(EVID, "screen-main-loaded.png"))
        print("saved main-loaded")
    except Exception as e:
        print("main capture error", e)

    w.close()
    app.quit()
    print("DONE")


if __name__ == "__main__":
    main()

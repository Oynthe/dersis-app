"""Probe 1 + 2: Excel-import AttributeError and Ctrl+C-on-Dashboard IndexError.

1. Statically checks which handler methods SchedulerApp is missing, then drives
   _import_from_excel() with QFileDialog/QMessageBox monkeypatched so a real
   template file is "chosen" and dialogs auto-answer, capturing the crash.
2. Places classes, switches to the Dashboard tab, calls _copy_to_clipboard()
   and captures the traceback.

Run: .venv-audit/Scripts/python.exe stress-test/tests/probe_excel_import_and_clipboard_crash.py
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ui_boot import boot, load_state, greedy_place, repo_root  # noqa: E402

sys.path.insert(0, os.path.join(repo_root(), "stress-test", "tests"))
from _fixtures.dataset_gen import make_preset  # noqa: E402


def main():
    app, window, sandbox = boot("excel_clipboard")
    out = {}

    # ---- Static: which expected handlers are missing on the class? ----
    candidates = ["_on_state_changed", "refresh", "_on_state_dirty",
                  "_state_changed", "mark_dirty", "refresh_all"]
    missing = [m for m in candidates if not hasattr(type(window), m)
               and not hasattr(window, m)]
    out["static_missing_methods"] = missing
    out["has_refresh_grid"] = hasattr(window, "refresh_grid")

    # ---- Probe 1: generate a real template, then drive _import_from_excel ----
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    tmpl = os.path.join(sandbox, "tmpl.xlsx")
    import_result = {}
    try:
        from scheduler_app.data_io.template import generate_excel_template
        generate_excel_template(tmpl)
        import_result["template_generated"] = os.path.exists(tmpl)
    except Exception as e:
        import_result["template_error"] = f"{type(e).__name__}: {e}"

    # Monkeypatch dialogs
    orig_open = QFileDialog.getOpenFileName
    orig_info = QMessageBox.information
    orig_warn = QMessageBox.warning
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (tmpl, ""))
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    try:
        window._import_from_excel()
        import_result["outcome"] = "NO CRASH (import completed)"
    except Exception as e:
        import_result["outcome"] = "CRASH"
        import_result["exc_type"] = type(e).__name__
        import_result["exc_msg"] = str(e)
        tb = traceback.format_exc()
        # last app.py frame
        applines = [ln.strip() for ln in tb.splitlines() if "app.py" in ln]
        import_result["last_app_frames"] = applines[-3:]
    finally:
        QFileDialog.getOpenFileName = orig_open
        QMessageBox.information = orig_info
        QMessageBox.warning = orig_warn
    out["probe1_excel_import"] = import_result

    # ---- Probe 2: Ctrl+C on Dashboard tab ----
    state = make_preset("small", seed=7)
    load_state(app, window, state)
    n_placed = greedy_place(state, fraction=1.0)
    load_state(app, window, state)

    tab_count = window.notebook.count()
    # find dashboard index (last tab per addTab order)
    dash_idx = tab_count - 1
    clip = {}
    clip["tab_count"] = tab_count
    clip["dashboard_index"] = dash_idx
    clip["n_placed"] = n_placed

    window.notebook.setCurrentIndex(dash_idx)
    for _ in range(2):
        app.processEvents()
    clip["current_index"] = window.notebook.currentIndex()
    try:
        window._copy_to_clipboard()
        clip["outcome"] = "NO CRASH"
    except Exception as e:
        clip["outcome"] = "CRASH"
        clip["exc_type"] = type(e).__name__
        clip["exc_msg"] = str(e)
        tb = traceback.format_exc()
        applines = [ln.strip() for ln in tb.splitlines() if "app.py" in ln]
        clip["last_app_frames"] = applines[-3:]

    # Also test each other tab index for _copy_to_clipboard robustness
    per_tab = {}
    for idx in range(tab_count):
        window.notebook.setCurrentIndex(idx)
        for _ in range(1):
            app.processEvents()
        try:
            window._copy_to_clipboard()
            per_tab[idx] = "ok"
        except Exception as e:
            per_tab[idx] = f"{type(e).__name__}: {e}"
    clip["per_tab_outcome"] = per_tab
    out["probe2_clipboard"] = clip

    import json
    print(json.dumps(out, indent=2, ensure_ascii=False))
    window.close()


if __name__ == "__main__":
    main()

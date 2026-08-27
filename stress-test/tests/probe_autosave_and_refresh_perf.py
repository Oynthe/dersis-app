"""Probe 4 + 5 + 6: auto-save-on-every-refresh, warning-log unbounded growth,
open-slots widget churn, rapid-refresh degradation, and the error swallow.

Run: .venv-audit/Scripts/python.exe stress-test/tests/probe_autosave_and_refresh_perf.py
"""
import os
import sys
import time
import ctypes
import statistics
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ui_boot import boot, load_state, greedy_place, repo_root  # noqa: E402

sys.path.insert(0, os.path.join(repo_root(), "stress-test", "tests"))
from _fixtures.dataset_gen import make_preset  # noqa: E402

_k = ctypes.windll.kernel32
_k.GetCurrentProcess.restype = wintypes.HANDLE


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


_psapi = ctypes.WinDLL("psapi")
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]


def rss_mb():
    c = _PMC()
    c.cb = ctypes.sizeof(c)
    _psapi.GetProcessMemoryInfo(_k.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return round(c.WorkingSetSize / 1048576, 1)


def p(m):
    print(m, flush=True)


def main():
    app, window, sandbox = boot("autosave_perf")
    import scheduler_app.storage as storage
    sp = storage.settings_path()
    out = {}
    out["settings_path"] = sp

    # ---------- normal-state single-refresh cost + components ----------
    stn = make_preset("normal", seed=3)
    load_state(app, window, stn)
    greedy_place(stn, fraction=0.6)
    load_state(app, window, stn)
    window.notebook.setCurrentIndex(0)
    for _ in range(3):
        app.processEvents()

    def med(fn, n):
        xs = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            xs.append((time.perf_counter() - t0) * 1000)
        return round(statistics.median(xs), 1)

    out["normal"] = {
        "n_classes": len(stn["classes"]),
        "grid": f"{len(stn['days'])}d x {len(stn['slots'])}s x {len(stn['classrooms'])}r",
        "refresh_grid_ms": med(window.refresh_grid, 5),
        "auto_save_ms": med(window._auto_save, 5),
        "render_tab_ms": med(window._render_current_tab, 5),
        "open_slots_ms": med(window._refresh_open_slots, 5),
        "warnings_ms": med(window._refresh_warnings, 3),
        "egu_bytes": os.path.getsize(sp) if os.path.exists(sp) else None,
    }
    p(f"normal done: {out['normal']}")

    # ---------- large-state single refresh_grid + open-slots widget count ----------
    stl = make_preset("large", seed=3)
    load_state(app, window, stl)
    npl = greedy_place(stl, fraction=0.6)
    load_state(app, window, stl)
    window.notebook.setCurrentIndex(0)
    for _ in range(3):
        app.processEvents()
    t0 = time.perf_counter()
    window.refresh_grid()
    single = (time.perf_counter() - t0) * 1000
    # open slots widget count in default mode
    window._selected_class = None
    window._refresh_open_slots()
    os_count = window._open_slots_layout.count() if hasattr(window, "_open_slots_layout") else None
    out["large"] = {
        "n_classes": len(stl["classes"]),
        "n_placed": npl,
        "n_unplaced": len(stl["classes"]) - npl,
        "grid": f"{len(stl['days'])}d x {len(stl['slots'])}s x {len(stl['classrooms'])}r",
        "single_refresh_grid_ms": round(single, 1),
        "auto_save_ms": med(window._auto_save, 3),
        "warnings_ms": med(window._refresh_warnings, 2),
        "open_slots_layout_widgets": os_count,
        "egu_bytes": os.path.getsize(sp) if os.path.exists(sp) else None,
    }
    p(f"large done: single_refresh={single:.0f}ms open_slots_widgets={os_count}")

    # ---------- Probe 5+6: refresh loop degradation & warning-log growth ----------
    # Use the large state (100 unplaced classes → heavy auto-negotiation).
    wl = getattr(window, "warning_log", None)
    window.warning_log.clear() if wl else None
    per_iter = []
    N = 12
    rss0 = rss_mb()
    egu0 = os.path.getsize(sp) if os.path.exists(sp) else 0
    for i in range(N):
        t0 = time.perf_counter()
        window.refresh_grid()
        dt = (time.perf_counter() - t0) * 1000
        msgcount = len(window.warning_log._messages) if wl else None
        per_iter.append({
            "iter": i + 1,
            "refresh_ms": round(dt, 0),
            "warning_log_messages": msgcount,
            "rss_mb": rss_mb(),
            "egu_bytes": os.path.getsize(sp) if os.path.exists(sp) else 0,
        })
    out["loop_degradation"] = {
        "iterations": N,
        "first_refresh_ms": per_iter[0]["refresh_ms"],
        "last_refresh_ms": per_iter[-1]["refresh_ms"],
        "slowdown_x": round(per_iter[-1]["refresh_ms"] / max(1, per_iter[0]["refresh_ms"]), 2),
        "warning_log_first": per_iter[0]["warning_log_messages"],
        "warning_log_last": per_iter[-1]["warning_log_messages"],
        "rss_start_mb": rss0,
        "rss_end_mb": per_iter[-1]["rss_mb"],
        "rss_growth_mb": round(per_iter[-1]["rss_mb"] - rss0, 1),
        "egu_start_bytes": egu0,
        "egu_end_bytes": per_iter[-1]["egu_bytes"],
        "egu_grew": per_iter[-1]["egu_bytes"] > egu0,
        "per_iter": per_iter,
    }
    p(f"loop done: first={per_iter[0]['refresh_ms']}ms last={per_iter[-1]['refresh_ms']}ms "
      f"msgs {per_iter[0]['warning_log_messages']}->{per_iter[-1]['warning_log_messages']} "
      f"rss {rss0}->{per_iter[-1]['rss_mb']}MB")

    # ---------- Probe 4b: read-only settings path → swallowed error ----------
    import stat
    swallow = {}
    window._auto_save()  # ensure file exists
    pre_mtime = os.path.getmtime(sp)
    os.chmod(sp, stat.S_IREAD)
    stl["classes"][0]["name"] = "MUTATED_SENTINEL_XYZ"
    time.sleep(0.05)
    raised = False
    try:
        window._auto_save()
    except Exception as e:
        raised = True
        swallow["raise"] = f"{type(e).__name__}: {e}"
    post_mtime = os.path.getmtime(sp)
    swallow["auto_save_raised"] = raised
    swallow["file_mtime_unchanged"] = (post_mtime == pre_mtime)
    os.chmod(sp, stat.S_IWRITE | stat.S_IREAD)
    try:
        reloaded = storage.load_encrypted(sp)
        names = [c.get("name") for c in reloaded.get("state", {}).get("classes", [])]
        swallow["sentinel_persisted"] = "MUTATED_SENTINEL_XYZ" in names
    except Exception as e:
        swallow["reload_error"] = f"{type(e).__name__}: {e}"
    swallow["conclusion"] = (
        "SWALLOWED: auto_save returned normally, write blocked, edit lost silently"
        if not raised else "raised")
    out["probe4b_readonly_swallow"] = swallow
    p(f"swallow done: {swallow}")

    import json
    print("=== JSON ===")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    window.close()


if __name__ == "__main__":
    main()

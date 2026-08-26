"""Minimal timing diagnostic: isolate the cost of each refresh component on a
large state, with flushed progress so we can see where time goes."""
import os
import sys
import time
import ctypes
import ctypes.wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ui_boot import boot, load_state, greedy_place, repo_root

sys.path.insert(0, os.path.join(repo_root(), "stress-test", "tests"))
from _fixtures.dataset_gen import make_preset


class PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def rss_mb():
    c = PMC()
    c.cb = ctypes.sizeof(c)
    ctypes.windll.psapi.GetProcessMemoryInfo(
        ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize / (1024 * 1024)


def p(msg):
    print(msg, flush=True)


def main():
    app, window, sandbox = boot("perf_diag")
    import scheduler_app.storage as storage
    sp = storage.settings_path()

    st = make_preset("large", seed=3)
    load_state(app, window, st)
    n = greedy_place(st, fraction=0.6)
    load_state(app, window, st)
    window.notebook.setCurrentIndex(0)
    for _ in range(3):
        app.processEvents()
    p(f"large: classes={len(st['classes'])} placed={n} rss={rss_mb():.1f}MB "
      f"egu={os.path.getsize(sp) if os.path.exists(sp) else 0}B")

    def timeit(fn, label):
        t0 = time.perf_counter()
        fn()
        dt = (time.perf_counter() - t0) * 1000
        p(f"  {label}: {dt:.1f} ms")
        return dt

    p("component timings (single call each):")
    timeit(window._render_current_tab, "_render_current_tab")
    timeit(window._refresh_open_slots, "_refresh_open_slots")
    timeit(window._refresh_warnings, "_refresh_warnings")
    timeit(window._auto_save, "_auto_save")
    timeit(window.refresh_grid, "refresh_grid (full)")

    # rapid loop 20x with rss tracking
    p("rapid loop 20x refresh_grid:")
    rss0 = rss_mb()
    t0 = time.perf_counter()
    for i in range(20):
        window.refresh_grid()
        if i % 5 == 4:
            p(f"    iter {i+1}: rss={rss_mb():.1f}MB "
              f"egu={os.path.getsize(sp)}B")
    dt = time.perf_counter() - t0
    rss1 = rss_mb()
    p(f"  loop_total={dt:.2f}s per_refresh={dt/20*1000:.1f}ms "
      f"rss_delta={rss1-rss0:.1f}MB")

    window.close()


if __name__ == "__main__":
    main()

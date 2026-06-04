# DERSIS — Runtime Issues Log

Issues encountered while setting up and running the application.

---

## Issue 1: PyQt6-QSvg Was Listed as a Dependency

**Severity**: Medium (blocks CI builds)

**Error**:
```
ERROR: Could not find a version that satisfies the requirement PyQt6-QSvg>=6.5 (from versions: none)
ERROR: No matching distribution found for PyQt6-QSvg>=6.5
```

**Cause**: `PyQt6-QSvg` does not exist as a PyPI package. It was mistakenly listed as a dependency. Qt SVG support is bundled inside the `PyQt6-Qt6` package (a transitive dep of `PyQt6`), not distributed separately.

**Impact**: pip install fails on all platforms when this package is listed.

**Fix Applied**: Removed `PyQt6-QSvg` from all dependency files. The application does not import `PyQt6.QtSvg` anywhere — all icons use PNG files or are programmatically painted.

---

## Issue 2: Missing libEGL.so.1 on Linux

**Severity**: Medium (blocks startup)

**Error**:
```
ImportError: libEGL.so.1: cannot open shared object file: No such file or directory
```

**Cause**: PyQt6 requires EGL libraries for rendering, even in offscreen mode. Not installed by default on minimal Linux environments.

**Impact**: Application cannot start without this library.

**Fix Applied**: `apt-get install libegl1`

**Recommendation**: Document Linux system dependencies in README or BUILD.md. This is only relevant for development/CI — the production target is Windows where these libraries are bundled.

---

## Issue 3: Auth Gate Blocks Headless/Offline Launch

**Severity**: Expected behavior (not a bug)

**Details**: The `main()` function in `scheduler_gui.py` requires:
1. `run_language_gate()` — needs a display for language selection dialog
2. `_run_auth_gate()` — needs network access to the remote licensing backend

In a headless or offline environment, the app cannot proceed past the auth gate.

**Impact**: Cannot fully test interactive features without credentials and display server.

**Workaround**: Bypass auth by directly instantiating `SchedulerApp` with a mock session dict and using `QT_QPA_PLATFORM=offscreen`.

**Recommendation**: Consider adding a `--dev` or `--offline` flag for development/testing that skips the auth gate. This would simplify CI testing.

---

## Summary

| # | Issue | Severity | Status |
|---|---|---|---|
| 1 | PyQt6-QSvg not a real package | Medium | Fixed (removed from all dependency files) |
| 2 | Missing libEGL.so.1 | Medium | Fixed (`apt install libegl1`) |
| 3 | Auth gate blocks headless use | Expected | Bypassed for testing |

No code changes were required. All issues were environment-related, not application bugs.

# File: `scheduler_app/_version.py`

## 1. File Role
Reads the canonical `VERSION` file from the repo/dist root and exposes `__version__`. Falls back to `"0.0.0"` if the file can't be located.

## 2. Why this file matters
**Critical.** Imported by `ui/app.py` (the About dialog) and `ui/bug_report.py` — the places that display the app version.

## 3. Imports and Dependencies
- stdlib: `os` (aliased to `_os` to keep the module namespace clean).

## 4. Main Symbols
| Symbol | Lines | Purpose |
|--------|-------|---------|
| `_FALLBACK` | 18 | `"0.0.0"`. |
| `_read_version()` | 21–48 | Search `..VERSION`, `./VERSION`, `cwd/VERSION` in order; returns first found stripped value; else fallback. |
| `__version__` | 51 | Module-level export populated at import time by `_read_version()`. |

## 5. Block-by-block code map
| Lines | Block | What | Why |
|-------|-------|------|-----|
| 1–14 | docstring | Explains the search order and build-time generation strategy. | Onboarding aid. |
| 16 | import | `import os as _os`. | Avoids accidental name leakage. |
| 18 | `_FALLBACK` | Default value. | Stable fallback. |
| 21–48 | `_read_version` | Multi-location lookup. | Works in dev, in embeddable-Python builds, and in Nuitka builds. |
| 51 | `__version__` | Eager evaluation. | One read at import; downstream just uses the string. |

## 6. Runtime Behavior
Runs once at import. Looks in: (a) sibling-of-package `../VERSION` (repo root or dist root), (b) `./VERSION` inside the package, (c) `os.getcwd()/VERSION`.

## 7. Data Flow
- **In**: `VERSION` file contents.
- **Out**: `__version__` string.

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
- Returns `"0.0.0"` if every candidate path fails (silent OSError per candidate).
- Build scripts may overwrite this file with a static `__version__ = "X.Y.Z"` for Nuitka builds where the VERSION file isn't on disk.

## 10. Integration Points
Consumed by the places that display the version (the `ui/app.py` About dialog and `ui/bug_report.py`). The `VERSION` file is written/read by `build_embed.bat` and `build_nuitka.bat`.

## 11. Risks and Maintenance Notes
Do not duplicate version strings in code; always import `__version__` from here.

## 12. Mini Summary
Single source of truth for `__version__`. Reads `VERSION` from up to three candidate locations.

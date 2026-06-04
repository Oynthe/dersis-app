# File: `verify_deps.py`

## 1. File Role
Pre-build dependency import check. Verifies every direct and transitive dep is importable so Nuitka doesn't fail mid-compile. Exit 0 if all OK, 1 otherwise.

## 2. Why this file matters
Supporting (build pipeline gate).

## 3. Imports and Dependencies
- stdlib: `sys`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `REQUIRED` | List of `(import_name, pip_label)` tuples — direct + transitive deps. |
| `main()` | Loops `REQUIRED`; prints `[OK]` / `[MISSING]`; returns 1 if any missing. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–9 | docstring | |
| 10 | import sys | |
| 12–39 | `REQUIRED` | Direct + transitive. |
| 42–59 | `main` | Loop + reporting. |
| 62–63 | `if __name__ == "__main__": sys.exit(main())` | Standard guard. |

## 6. Runtime Behavior
Synchronous; prints to stdout; returns exit code.

## 7. Data Flow
None besides stdout.

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
- `ImportError` per dep is caught and recorded as missing.

## 10. Integration Points
Invoked by `build_nuitka.bat` and CI (`ci.yml`).

## 11. Risks and Maintenance Notes
- Adding a new transitive dep that Nuitka would miss → add it here AND to `requirements-lock.txt`.

## 12. Mini Summary
Pre-build sanity check. Fails fast if a dependency is missing.

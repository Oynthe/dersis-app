# File: `scheduler_app/assets/__init__.py`

## 1. File Role
Asset path helper. Exposes `ASSETS_DIR` constant and `asset_path(filename)`.

## 2. Why this file matters
Supporting. Provides a stable way to resolve bundled icons regardless of cwd or frozen build layout.

## 3. Imports and Dependencies
- stdlib: `os`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `ASSETS_DIR` | `os.path.dirname(os.path.abspath(__file__))`. |
| `asset_path(filename)` | `os.path.join(ASSETS_DIR, filename)`. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1 | docstring | |
| 2 | import os | |
| 4 | `ASSETS_DIR` | |
| 7–9 | `asset_path` | |

## 6. Runtime Behavior
Stateless.

## 7. Data Flow
None.

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
None.

## 10. Integration Points
Used by `ui/icons.py` and `installer.iss` indirectly.

## 11. Risks and Maintenance Notes
- Path must remain stable after Nuitka compilation (Nuitka copies the assets directory; `os.path.abspath(__file__)` resolves correctly in standalone mode).

## 12. Mini Summary
Tiny helper for bundled asset paths.

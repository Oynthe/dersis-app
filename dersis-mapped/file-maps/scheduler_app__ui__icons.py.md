# File: `scheduler_app/ui/icons.py`

## 1. File Role
Icon helper: loads PNG flag icons from the `flags/` directory, draws programmatic icons (toolbar, menus, arrows) with `QPainter`, and writes temporary arrow PNGs used by Qt stylesheets that require `image: url(...)`.

## 2. Why this file matters
Supporting. Without it, the language gate has no flag icons and the menus look bare.

## 3. Imports and Dependencies
- stdlib: `os`, `tempfile`.
- Third-party: `PyQt6.QtGui.{QPixmap, QPainter, QColor, QFont, QPen, QBrush, QIcon, QPolygonF, QPainterPath}`, `PyQt6.QtCore.{Qt, QRect, QRectF, QPointF}`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `_FLAGS_DIR` | Path to `flags/` (computed from `__file__`). |
| `_png_flag_icon(png_filename, size=22)` → QIcon | Loads + scales a flag PNG. |
| Flag helpers: `flag_gb`, `flag_tr`, `flag_de`, `flag_fr`, `flag_es`, `flag_cn`, `flag_ru`, `flag_br`, `flag_se`, `flag_dk`, `flag_it`, `flag_nl`, `flag_pl`, `flag_in`, `flag_id`, `flag_az`, `flag_za`, `flag_sa`, `flag_ir`, `flag_jp`, `flag_kr`, `flag_pt` | Each returns a QIcon. |
| `_arrow_dir`, `_ensure_arrow_dir()` | Writes arrow PNGs to a process-local temp dir for stylesheet `image: url(...)` references. |
| Painted icons | Toolbar icons drawn at runtime (e.g. plus, settings, refresh, calendar, etc.). |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–6 | docstring | |
| 8–14 | imports | |
| 17–22 | flag PNG loader | path is 3 dirs up from `ui/` (repo root) + `flags/`. |
| 25–32 | `_png_flag_icon` | scaled with KeepAspectRatio + Smooth. |
| ~35–~80 | `_ensure_arrow_dir` | Writes down/up arrow PNGs to temp. |
| ~80–330 | flag and icon helpers | Each calls `_png_flag_icon` with the matching filename. |

## 6. Runtime Behavior
Each helper is lazy — icons are created when called. Some painted icons cache pixmaps in module globals.

## 7. Data Flow
- In: filenames.
- Out: QIcon instances.

## 8. UI Flow
Used by the language selector, the toolbar, menus, dialogs.

## 9. Error Handling and Edge Cases
- Missing PNG → `QPixmap` is null; `QIcon` will be empty. No exception.
- Temp dir creation failure → painted icons that require stylesheet arrows may not render correctly, but no crash.

## 10. Integration Points
- `ui/first_run.py` for the language dialog.
- `ui/app.py` for toolbar/menus.
- `ui/dialogs.py` for ad-hoc icons.

## 11. Risks and Maintenance Notes
- `_FLAGS_DIR` path computation assumes a specific source layout. After Nuitka build, the relative path still resolves because Nuitka copies `flags/` into the dist root.
- Adding a new language flag: drop the PNG into `flags/`, add a helper, wire it in `first_run.py`.

## 12. Mini Summary
Loads flag icons from `flags/` and paints programmatic icons. Keep helpers stable; UI consumers expect specific names.

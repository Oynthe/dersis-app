# File: `scheduler_app/ui/renderer.py`

## 1. File Role
QGraphicsView-based timetable rendering. Replaces the old QGridLayout/QFrame approach. Handles painting, interaction (click, drag, context menu), and drop validation callbacks. Scheduling engine remains untouched.

## 2. Why this file matters
**Critical.** The most-visible part of the UI.

## 3. Imports and Dependencies
- Third-party: `PyQt6.QtWidgets.{QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsItem, QMenu, QApplication, QLabel}`, `PyQt6.QtCore.{Qt, QRectF, QPointF, QMimeData, QPoint}`, `PyQt6.QtGui.{QColor, QPen, QBrush, QFont, QPainter, QDrag, QPixmap, QTransform}`.
- Internal: `constants.*` (colours, cell sizes), `translations.tr`, `logic.{get_placed_classes, total_duration, classroom_of, get_year_color, lighten_color, build_virtual_classroom_day_layout}`, `models.{get_protection_label, effective_day, effective_time, is_sequential_class, slot_offset_for_target}`, `ui.badge_formatter.{get_badge, badge_text}`, `ui.cell_formatter.tooltip_text`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| Layout constants: `COL_TIME_W=85`, `COL_DAY_W=MIN_CELL_W=150`, `ROW_HEADER_H=38`, `ROW_SLOT_H=MIN_CELL_H=70`. | |
| `FILTER_MODE_DEFAULT`, `FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP` | View-filter enums. |
| `RendererAdapter(state, view_mode, filter_mode)` | Produces normalised layout blocks (lane assignment, day groups). |
| `HeaderItem(QGraphicsRectItem)` | Day/time/corner header cell. |
| `EmptySlotItem(QGraphicsRectItem)` | Drop-target for empty (day, slot, room). |
| `LessonItem(QGraphicsRectItem)` | Interactive lesson cell. Supports mouse press / drag start (`QDrag`) / right-click context menu / hover tooltip. |
| `MatrixLessonItem(QGraphicsRectItem)` | Read-only variant for Show Everything. |
| `TimetableScene(QGraphicsScene)` | Assembles items. Emits `lessonDropped(cls, day, slot, room)`. |
| `TimetableView(QGraphicsView)` | View with scroll + drop + zoom (Ctrl+wheel). |

## 5. Block-by-block code map (logical sections)
| Lines (approx.) | Block | What |
|-----------------|-------|------|
| 1–15 | docstring | Class overview. |
| 17–34 | imports | |
| 36–50 | layout constants | |
| ~50–~200 | `RendererAdapter` | Builds row/column metadata, lane assignment for virtual-classroom view, year-colour resolution. |
| ~200–~350 | `HeaderItem` + `EmptySlotItem` | basic painted cells. |
| ~350–~700 | `LessonItem` | most complex item: paint, badges, mouse handlers, drag MIME data, context menu. |
| ~700–~850 | `MatrixLessonItem` | simpler painted cell for matrix view. |
| ~850–~1200 | `TimetableScene` | grid assembly, drop event handling, emits `lessonDropped`. |
| ~1200–1414 | `TimetableView` | scroll + zoom + drop-acceptance. |

## 6. Runtime Behavior
- Built once per main window (multiple instances for different tabs/view modes).
- Each schedule change triggers a `refresh()` → adapter rebuild → scene re-paint.
- Drag-drop runs synchronously: drop → `TimetableScene.lessonDropped(...)` → handler in `ui/app.py` → `workflow.validate_drop` → accept/reject + repaint.

## 7. Data Flow
- In: state (read-only).
- Out: visual paint + drop event signals.

## 8. UI Flow
- Hover → tooltip from `cell_formatter.tooltip_text`.
- Left-click → select lesson.
- Drag → `QDrag` with `application/x-dersis-lesson-uid` MIME data.
- Right-click → context menu (auto-place, edit, pin/unpin, set protection, delete, …).
- Drop on `EmptySlotItem` → emits `lessonDropped`.

## 9. Error Handling and Edge Cases
- Drag-drop rejected (`workflow.validate_drop` invalid) → toast + warning log entry.
- Sequential classes paint as multiple connected blocks (one per target) using `slot_offset_for_target`.
- Pinned/locked lessons reject drag attempts.

## 10. Integration Points
Consumed by `ui/app.py` (one instance per tab). Emits signals to the main window.

## 11. Risks and Maintenance Notes
- Adding a new view mode → extend the adapter + scene assembly.
- Cell painting performance: avoid expensive operations in `paint()`; cache where possible.
- Drop event handling is hot-path; never block.

## 12. Mini Summary
The QGraphicsView timetable. RendererAdapter computes layout, items paint themselves, scene wires drop events. Big file but cleanly organised around the four item types.

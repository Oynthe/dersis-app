# File: `scheduler_app/ui/tutorial.py`

## 1. File Role
Full-window interactive tutorial overlay with spotlight cutout and progress bar. Section-organised steps; each can carry an optional `action` callable that fires when activated (switch tab, scroll to widget, etc.).

## 2. Why this file matters
Supporting (onboarding). Sets first impression.

## 3. Imports and Dependencies
- Third-party: PyQt6 widgets/core/gui.
- Internal: `translations.tr`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `TutorialOverlay(QWidget)` | Full-window widget. Paints four dim rectangles around the spotlight. |
| Signal `finished` | Emitted on skip or last step. |
| Constants `_DIM`, `_MARGIN`, `_CARD_MAX_W`, `_CARD_MIN_W`, `_CORNER_R` | Visual constants. |
| `SECTION_NAME_KEYS` | List of translation keys for sections (welcome, interface, setup, classes, placement, views, panels, optimization, dashboard, …). |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–9 | docstring | |
| 11–17 | imports | |
| 22–~80 | `TutorialOverlay.__init__` | builds card widget + buttons + progress bar; covers main window. |
| ~80–~200 | paint event | dim rectangles + rounded spotlight cutout. |
| ~200–~280 | step navigation | next/prev/skip; `action` callback firing. |
| ~280–342 | utilities | localisation, resize handling, key events (Arrow / Escape). |

## 6. Runtime Behavior
Constructed by `FirstRunController.start_tutorial(window)`. The overlay is reparented to the main window and always on top.

## 7. Data Flow
- In: list of step dicts.
- Out: `finished` signal.

## 8. UI Flow
- Arrow keys + buttons for navigation.
- Escape or "Skip" → finish.
- Action callbacks may switch tabs, scroll, etc.

## 9. Error Handling and Edge Cases
- Target widget missing or off-screen → falls back to centred card with no spotlight.
- Widget moved during the step → paint event uses live `mapToGlobal` so it updates.

## 10. Integration Points
Used by `ui/first_run.FirstRunController` (and the Help menu trigger).

## 11. Risks and Maintenance Notes
- Adding a step: extend the steps list with translation keys; ensure the target widget is reachable in the main window.
- The progress bar shows sections, not steps — make sure each step's `section` field is correct.

## 12. Mini Summary
Spotlight tutorial overlay. Section-aware progress bar. Step actions can drive the rest of the UI.

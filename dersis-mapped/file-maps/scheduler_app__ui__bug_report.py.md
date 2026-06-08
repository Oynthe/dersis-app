# File: `scheduler_app/ui/bug_report.py`

> **Captured 2026-06-04.** _Updated 2026-06-08: this file is unchanged, but its caller `scheduler_gui._global_exception_handler` was restructured — `CrashReportDialog` is now shown **only** when a `QApplication` already exists; a pre-Qt bootstrap/import failure instead surfaces via a native Windows `MessageBox` (`scheduler_gui._report_startup_failure`), not this dialog._

## 1. File Role
Bug + crash report dialogs and a small status-bar bug icon button. Reports are composed **locally** and handed to the user's default email client via a `mailto:` link — nothing is transmitted by the app.

## 2. Why this file matters
Critical for support. Crash/bug reports are the main feedback channel, now routed entirely through the user's mail client (offline).

## 3. Imports and Dependencies
- stdlib: `platform`.
- Third-party: `PyQt6.QtWidgets`, `PyQt6.QtCore.{Qt, QUrl, QUrlQuery}`, `PyQt6.QtGui.{QColor, QPainter, QPen, QCursor, QDesktopServices}`.
- Internal: `translations.tr`, `_version.__version__` (as `APP_VERSION`).
- **No** `AuthClient`, no `threading`, no `requests`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `BUG_REPORT_EMAIL` | `"dersis.app@gmail.com"` — the destination address. |
| `BUG_REPORT_SUBJECT` | `"DERSİS Bug Report"` — the mail subject. |
| `_BUG_DIALOG_STYLE` (long QSS string) | Dark theme shared by both dialogs. |
| `_open_mailto(subject, body, parent=None)` | Builds a `mailto:` `QUrl` (with `subject`/`body` query items) and opens it via `QDesktopServices.openUrl`. If no mail client is available, copies the body to the clipboard and shows an informational dialog with the address. Returns whether a client launched. |
| `BugReportDialog` | Manual report form: title, severity, description/expected/steps fields, optional traceback, auto-filled version + OS. On submit, composes a plain-text body and calls `_open_mailto`. |
| `CrashReportDialog` | Auto-shown by `_global_exception_handler` **only on the Qt-running path** (a `QApplication` must already exist). Pre-fills exception name + message + traceback + log path; collapsible traceback + optional user note; "Report This Crash" composes the body and calls `_open_mailto`. |
| `BugReportButton` | Status-bar bug icon widget (painted). On click the host (`ui/app.py`) opens `BugReportDialog`. **Unchanged** by the offline conversion. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–25 | docstring + imports + `BUG_REPORT_EMAIL`/`BUG_REPORT_SUBJECT` | |
| 28–144 | `_BUG_DIALOG_STYLE` | central QSS. |
| 147–189 | helpers (`_make_heading`, `_make_subheading`, `_open_mailto`) | mailto compose + clipboard fallback. |
| 192–389 | `BugReportDialog` | layout, validation, `_on_submit` → builds body → `_open_mailto`. |
| 391–526 | `CrashReportDialog` | minimal layout; `_send_crash_report` → builds body (trace trimmed to ~4000 chars) → `_open_mailto`. |
| 529–587 | `BugReportButton` | painted bug icon + hover behaviour. |

## 6. Runtime Behavior
Dialogs are modal. On submit/report, the app builds a plain-text message and opens the OS mail client via `QDesktopServices.openUrl`. Nothing runs in a background thread and no HTTP request is made.

## 7. Data Flow
- In: form fields + (for crashes) exception type/message/traceback/log path.
- Out: a `mailto:` URL passed to the OS; on failure, the body is placed on the clipboard. No network egress from the app.

## 8. UI Flow
- Status bar `BugReportButton` → `BugReportDialog` (opened by `ui/app.py::_open_bug_report`).
- Uncaught exception **with Qt running** → `scheduler_gui._global_exception_handler` → `CrashReportDialog`. (Uncaught exception **before Qt is up** → native `MessageBox` via `_report_startup_failure`, not this dialog.)

## 9. Error Handling and Edge Cases
- No mail client configured → `_open_mailto` copies the report to the clipboard and shows a dialog with the address; returns `False`.
- Clipboard access failure is swallowed (best-effort).
- `BugReportDialog` requires a non-empty title (shows an inline error otherwise).
- Crash dialog must work in a partially-initialised state → imports kept minimal; the caller falls back to `QMessageBox.critical` if the crash dialog itself fails.

## 10. Integration Points
- `scheduler_gui._global_exception_handler` instantiates `CrashReportDialog` (only when a `QApplication` exists).
- `ui/app.py` adds `BugReportButton` to the status bar and connects it to `_open_bug_report`.

## 11. Risks and Maintenance Notes
- The dark stylesheet is long; consolidate later if a theme module appears.
- `mailto:` body length is OS/client-limited; `CrashReportDialog` already trims the traceback to ~4000 chars (the full trace remains in `crash_log.txt`).
- The destination address is hardcoded in `BUG_REPORT_EMAIL`; update it there if support routing changes.

## 12. Mini Summary
Manual bug report + automatic crash report dialogs that compose a `mailto:dersis.app@gmail.com` message and hand it to the user's email client (clipboard fallback). No backend, no threads, no network — fully offline.

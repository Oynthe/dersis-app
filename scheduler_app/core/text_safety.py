"""Neutralising user text for renderers that interpret markup.

ST-UI-007 / ST-UI-008. A class name, a room code, a branch letter and a time-slot
label are all **free text the user typed**. Three of the surfaces DERSİS hands
them to parse markup, and each parses a *different* dialect, so there is no
single "escape" that serves all three — a helper per context, named for the
context, so every call site says which renderer it is protecting.

The three contexts, and why they cannot share one function
----------------------------------------------------------
**Qt rich text** decides *per string* whether to parse markup at all:
``Qt.mightBeRichText`` returns True only for ``<`` followed by a tag name Qt
knows. So ``html.escape`` on the way into a ``QLabel`` is wrong — for the common
case, where Qt would have rendered the string as plain text, it produces visible
``&amp;`` garbage. The right move for a plain label is to remove Qt's choice
with ``setTextFormat(PlainText)``; escaping is only for strings interpolated
into a template that really is rich text.

**reportlab** parses every ``Paragraph`` as markup, with no auto-detection, and
fails in two different ways. A tag it knows raises ``ValueError`` out of the
whole export — measured, **10 of 24** (field × mode) combinations produced no
file at all. A tag it does not know is dropped silently, and a bare ``&``
corrupts: ``"R&D Lab"`` renders as ``"R&D; Lab"``. It does decode HTML entities
correctly, so ``html.escape`` is the right tool here even though the dialect is
not HTML — verified to round-trip every payload through ``getPlainText()``.

**A spreadsheet** treats a leading ``=`` as a formula. Entities would be visible
garbage, so neither of the above applies. CSV is export-only in this app, so a
quote prefix is safe there; XLSX is **not** — DERSİS re-imports its own
workbooks, and a prefix written into the string renames the user's data on the
way back in. That case needs openpyxl's cell-level ``quotePrefix`` flag instead
and therefore does not live here; see ``data_io/spreadsheet_safety.py``.

**A crash report** is the fourth context, and the only one where the thing being
neutralised is not the user's typing but the user's *identity*: a traceback on
its way into a ``mailto:`` body carries filesystem paths, and on Windows a
filesystem path carries the account name. See :func:`redact_user_paths`.

Layering: ``core`` imports nothing from ``ui`` or ``data_io``, so every consumer
of this module imports downward and no new ST-ARCH-009 violation is added.
"""
import html
import os
import re
from typing import Optional


def escape_qt_rich(value):
    """Escape *value* for interpolation into a Qt **rich-text** template.

    Use this only where the surrounding string genuinely is rich text — a
    ``<span style=…>`` or a ``<b>…</b>`` the code itself wrote. For a label that
    merely displays a message, call ``setTextFormat(Qt.TextFormat.PlainText)``
    instead: escaping a string Qt was going to render literally is how ``&amp;``
    reaches the screen.
    """
    return html.escape(str(value))


def qt_tooltip(text):
    """Wrap *text* as an explicit Qt rich-text tooltip, newlines preserved.

    Tooltips carry class and room names, so whether Qt parses one as markup
    currently depends on whether the *user's own text* happens to start with a
    tag Qt recognises — the same string renders differently on two different
    lessons. Forcing the format makes it deterministic.
    """
    return "<qt>" + html.escape(str(text)).replace("\n", "<br>") + "</qt>"


def escape_pdf_markup(value):
    """Escape *value* for a reportlab ``Paragraph``.

    ``quote=False`` because ``'`` and ``"`` are only special inside a reportlab
    tag *attribute*, and no user text reaches one — every ``<font color=…>`` in
    the exporter interpolates a literal constant from
    ``scheduler_app.core.constants``. Escaping them anyway would put ``&#x27;``
    in front of every apostrophe in a printed lecturer name.

    Kept separate from :func:`escape_qt_rich` despite the near-identical body:
    the two have different failure modes and different owners, and a future
    reportlab or Qt change should have exactly one place to move.
    """
    return html.escape(str(value), quote=False)


def csv_safe(value):
    """Neutralise a spreadsheet formula trigger for an **export-only** format.

    Excel and LibreOffice evaluate a cell whose text begins with ``=``; the
    leading ``+``, ``-``, ``@``, tab and carriage-return forms are the rest of
    the conventional list. A leading apostrophe is the standard neutralisation.

    **Only for the .csv file.** The safety of a literal prefix depends entirely
    on nothing reading it back, and that is true of the exported CSV — the only
    import filters in the app are ``*.xlsx`` and ``*.xlsx *.xls``.

    It is **not** true of the clipboard, and it is not true of XLSX:

    * ``EditClassesDialog._copy_rows`` writes the class list as TSV and
      ``_paste_rows`` parses that same text back, so a prefix applied on copy is
      read as part of the name on paste.
    * DERSİS re-imports its own workbooks through six symmetric export/import
      pairs. Measured on that round trip, a literal prefix renamed **7 of 8**
      values, including a perfectly innocent ``-9A Matematik``.

    For XLSX use openpyxl's cell-level ``quotePrefix`` flag instead, which
    neutralises the formula without touching the stored string.
    """
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


# ── Crash / bug report redaction (ST-SEC-008) ──────────────────────────────

# Anchored on the literal `X:\Users\` prefix, and matching exactly one path
# segment after it. Three separator forms, because a path reaches a report by
# three routes: a frame path (single backslash), an OSError message (`repr`
# doubles the separators, and that is the one form the obvious fix misses), and
# the occasional forward-slash spelling. Case-insensitive because a launcher
# handed an uppercased path bakes an uppercased `co_filename` into the frame.
#
# The excluded characters are the ones that cannot appear *inside* a single
# path segment, so a match stops at the next separator, quote or angle bracket
# instead of eating the rest of the line.
_USER_PROFILE_SEGMENT = re.compile(
    r"""(?i)([A-Za-z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/))([^\\/'"<>|:\r\n]+)"""
)


def _short_path(path: str) -> Optional[str]:
    r"""Return the Windows 8.3 short form of *path*, or ``None``.

    ``C:\Users\ZZ Long User Name ZZ`` becomes ``C:\Users\ZZLONG~1``, and Python
    *keeps* that form in ``co_filename`` when the process was launched through
    it — so a redactor that knows only the long name leaves the first six
    characters of the account name on screen.

    Everything is inside ``try``: ``ctypes.windll`` does not exist off Windows
    and this module has to keep importing for ``Dersis-mac.spec`` and the Linux
    CI job. Returns ``None`` when the API is absent or the path has no distinct
    short form — which includes every path that does not exist on disk, and
    every volume with 8.3 name creation switched off.
    """
    try:
        import ctypes
        from ctypes import wintypes

        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(512)
        used = get_short(path, buf, 512)
        if used and used < 512 and buf.value and buf.value != path:
            return str(buf.value)
    except Exception:
        pass
    return None


def _home_needles() -> list:
    """Every literal spelling of this user's home directory, longest first.

    ``os.path.expanduser("~")`` gives one of them. The others are the
    doubled-backslash form (what ``repr`` inside an ``OSError`` message
    produces), the forward-slash form, and the 8.3 short name with those same
    two separator variants.

    Longest first matters: the doubled form and the single form are different
    needles, and a shorter one must never get first refusal on text a longer
    one would have matched.
    """
    home = os.path.expanduser("~")
    if not home or home == "~":
        return []
    forms = {home}
    short = _short_path(home)
    if short:
        forms.add(short)
    needles = set()
    for form in forms:
        form = form.rstrip("\\/")
        if not form:
            continue
        needles.add(form)
        needles.add(form.replace("\\", "\\\\"))
        needles.add(form.replace("\\", "/"))
    return sorted(needles, key=len, reverse=True)


def redact_user_paths(text: str) -> str:
    r"""Remove the Windows account name from *text* before it leaves the machine.

    Called from exactly one place — the first line of
    ``ui.bug_report._open_mailto`` — which is enough to cover the crash report,
    the manual bug report, the ``mailto:`` URL, and the clipboard fallback that
    runs when no mail client is configured.

    Deliberately **not** applied to ``logs/crash_log.txt``, to
    ``startup_error.log``, or to the log path the crash dialog puts on screen.
    None of those leaves the machine, they are the only unredacted copy a local
    maintainer has, and putting new code inside the crash writer adds a way to
    lose the report entirely on the app's worst day.

    **Order matters: longest, most specific needle first.** The home-prefix pass
    runs before the ``X:\Users\<segment>`` pass, because the regex would
    otherwise rewrite the text the literal pass was about to match and turn that
    pass into dead code that still looks correct.

    Stated precisely, since the measurement behind this fix reported a swapped
    order as a *leak*: with these two passes it is not one on Windows — both
    spell out the doubled-backslash and 8.3 forms, so a swapped version still
    removes the account name. What a swap costs is the reading
    (``C:\Users\<user>\...`` instead of ``~\...``) and, for a home that is not
    under ``X:\Users\`` at all — a redirected profile, or any POSIX box — the
    literal pass is the only one that can fire and must not be pre-empted.
    ``tests/test_report_redaction.py`` pins the order through the output.

    What this deliberately does **not** do is replace the bare account name.
    Windows permits one-character accounts, and on a real captured traceback a
    global replace by account name scored 34 hits for ``"a"`` of which 31 were
    collateral, 24/21 for ``"in"``, and 6/3 for ``"os"`` — where
    ``File "<frozen os>"`` becomes ``File "<frozen <user>>"``. A traceback
    corrupted into unreadability is not a privacy win.

    Residual, by design: the *basename* survives, so an OSError over
    ``...\saves\9-A Sinifi Ders Programi.egu`` still names a class. Removing it
    would gut the diagnostic; saying what the report contains is the other half
    of that answer.
    """
    out = "" if text is None else str(text)
    if not out:
        return out
    for needle in _home_needles():
        out = re.sub(re.escape(needle), "~", out, flags=re.IGNORECASE)
    return _USER_PROFILE_SEGMENT.sub(r"\1<user>", out)

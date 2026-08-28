r"""The account name does not leave the machine in a bug report.

ST-SEC-008 (Low) · `core/text_safety.py`, `ui/bug_report.py`
    A crash traceback goes into a ``mailto:`` body, and on Windows a filesystem
    path in that traceback carries the Windows account name.

The register recorded this INFERRED. It has since been observed, and the
observation changed the fix in three ways that this module pins:

**1. The leak is in the exception message, not the frame paths.**
``build_embed.bat:183`` runs ``compileall -b`` on a *relative* path, so every
frozen ``.pyc`` carries ``co_filename = 'build\Dersis.dist\scheduler_app\...'``
— the developer's build directory. A live frozen slot crash produced a
traceback with **zero** account-name occurrences in frame paths and **one** in
an ``OSError`` message naming ``Documents\Dersis``.

**2. That message spells the path with doubled backslashes**, because ``repr``
doubles the separators — and that is precisely the form the register's own
recommendation, ``str.replace(os.path.expanduser("~"), "~")``, does not match.
On the real captured traceback that recommendation went 3 occurrences to 1; in
the common (slot-crash) case it removed **0 of 1**. ``test_the_shapes_a_real_*``
below is parametrised over the measured shapes so that fix cannot come back.

**3. A global replace of the bare account name is worse than the leak.**
Measured on a real traceback: ``"a"`` matched 34 times of which 31 were
collateral, ``"in"`` 24/21, ``"os"`` 6/3 — where ``File "<frozen os>"`` becomes
``File "<frozen <user>>"``. Windows permits one-character accounts.
``test_an_unlucky_account_name_*`` is parametrised over the five measured names.

What is deliberately *not* redacted
-----------------------------------
``logs/crash_log.txt``, ``startup_error.log``, and the log path the crash dialog
puts on screen. None of those leaves the machine, they are the only unredacted
copy a local support person has, and the dialog's path label exists precisely so
the user can find that file. Redaction is applied at the single point of egress
— ``bug_report._open_mailto`` — which is why one line covers the crash dialog,
the manual bug dialog, the URL, and the clipboard fallback.

Residual, by design: the *basename* survives, so an ``OSError`` over
``...\saves\9-A Sinifi Ders Programi.egu`` still names a class. Removing it
would gut the diagnostic.
"""
import os
import sys

import pytest

SENTINEL = "ZZUSERZZ"
FAKE_HOME = r"C:\Users" + "\\" + SENTINEL


@pytest.fixture
def sentinel_home(monkeypatch):
    r"""Make ``os.path.expanduser("~")`` return ``C:\Users\ZZUSERZZ``.

    A sentinel, not the real account: every count in this module is a count of
    ``ZZUSERZZ``, so the test says the same thing on every machine and the
    auditor's own name never enters the repository.

    All four variables, because ``ntpath.expanduser`` prefers ``USERPROFILE``
    and falls back to ``HOMEDRIVE`` + ``HOMEPATH``, while ``posixpath`` reads
    ``HOME``.
    """
    drive, tail = os.path.splitdrive(FAKE_HOME)
    monkeypatch.setenv("USERPROFILE", FAKE_HOME)
    monkeypatch.setenv("HOME", FAKE_HOME)
    monkeypatch.setenv("HOMEDRIVE", drive)
    monkeypatch.setenv("HOMEPATH", tail)
    assert os.path.expanduser("~") == FAKE_HOME
    return FAKE_HOME


# ── 1. The shapes an account name actually arrives in ───────────────────────

# Every entry was observed in a real traceback or a real OSError message, or
# measured directly (the 8.3 name via GetShortPathNameW). The doubled-backslash
# row is the one that matters most: it is the only form present in the common
# slot-crash case, and the only one the naive fix misses.
_LEAKY_SHAPES = [
    ("frame path",
     r"  File \"C:\Users\ZZUSERZZ\AppData\Local\Programs\Dersis\scheduler_gui.py\", line 207"),
    ("OSError message, doubled separators",
     "FileNotFoundError: [Errno 2] No such file or directory: "
     r"'C:\\Users\\ZZUSERZZ\\Documents\\Dersis\\saves\\9-A.egu'"),
    ("forward slashes",
     "opening C:/Users/ZZUSERZZ/Documents/Dersis/logs/crash_log.txt"),
    ("uppercased drive and Users",
     r"C:\USERS\ZZUSERZZ\Documents\Dersis"),
    ("mixed case account",
     r"C:\Users\zzuserzz\Documents\Dersis"),
    ("8.3 short name",
     r"C:\Users\ZZUSER~1\Documents\Dersis"),
    ("a second profile that is not this user",
     r"D:\Users\SomeoneElse\Desktop\notes.txt"),
]


@pytest.mark.parametrize("label,text",
                         _LEAKY_SHAPES,
                         ids=[s[0].replace(" ", "-") for s in _LEAKY_SHAPES])
def test_the_shapes_a_real_traceback_carries_are_all_redacted(
        sentinel_home, label, text):
    from scheduler_app.core.text_safety import redact_user_paths

    cleaned = redact_user_paths(text)

    assert SENTINEL.lower() not in cleaned.lower(), (
        "%s still carries the account name: %r" % (label, cleaned))
    assert "ZZUSER~1" not in cleaned, (
        "the 8.3 short name exposes the first six characters of the account")
    assert "SomeoneElse" not in cleaned, (
        "a profile path that is not this user's home is still a profile path")


def test_this_user_s_own_home_collapses_to_a_tilde(sentinel_home):
    r"""Pins the pass ordering, as output rather than as folklore.

    The home-prefix pass runs before the ``X:\Users\<segment>`` pass — longest,
    most specific needle first. Swap them and the regex rewrites the text the
    literal pass was about to match, so the literal pass becomes dead code that
    still looks correct.

    Stated precisely, because the measurement that produced this fix reported
    the swap as a *leak* and in this implementation it is not: both passes cover
    the Windows spellings, so a swapped version still removes the account name.
    What it loses is the reading — ``C:\Users\<user>\Documents`` instead of
    ``~\Documents`` — and, for a home that is not under ``X:\Users\`` at all
    (a redirected profile, or any POSIX box), the literal pass is the only one
    that fires and it must not have been pre-empted.
    """
    from scheduler_app.core.text_safety import redact_user_paths

    assert (redact_user_paths(FAKE_HOME + r"\Documents\Dersis")
            == r"~\Documents\Dersis")
    assert (redact_user_paths(r"C:\\Users\\ZZUSERZZ\\Documents")
            == r"~\\Documents")


@pytest.mark.skipif(sys.platform != "win32",
                    reason="8.3 short names are a Windows filesystem feature")
def test_the_real_8_3_form_of_this_home_is_redacted(tmp_path, monkeypatch):
    r"""Pins ``_short_path``, which nothing else in the suite reaches.

    The synthetic ``C:\Users\ZZUSER~1`` row above proves nothing about
    ``_short_path``: any 8.3 name *under* ``X:\Users\`` is caught by the
    anchored pass whether or not the short name is known. So this one makes a
    real long-named directory, asks Windows for its real short form, points the
    home at the long form, and feeds the redactor the short one. Only the
    ``_short_path`` needle can bridge those two spellings.
    """
    from scheduler_app.core.text_safety import redact_user_paths, _short_path

    home = tmp_path / "ZZ Long User Name ZZ"
    home.mkdir()
    short = _short_path(str(home))
    if not short:
        pytest.skip("8.3 name creation is disabled on this volume")
    assert short != str(home)

    drive, tail = os.path.splitdrive(str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HOMEDRIVE", drive)
    monkeypatch.setenv("HOMEPATH", tail)

    assert (redact_user_paths(short + r"\Documents\Dersis\a.egu")
            == r"~\Documents\Dersis\a.egu")


def test_the_naive_home_replacement_is_not_enough(sentinel_home):
    """Fixes the register's recommendation in place, so it cannot be re-adopted.

    ``stress-test/11-security-resilience-notes.md:69`` says "replace with ``~``".
    This is that recommendation, run on the shape that dominates real crashes.
    """
    doubled = ("FileNotFoundError: [Errno 2] No such file or directory: "
               r"'C:\\Users\\ZZUSERZZ\\Documents\\Dersis\\saves\\9-A.egu'")
    naive = doubled.replace(os.path.expanduser("~"), "~")
    assert SENTINEL in naive, (
        "the naive fix suddenly works, so this comparison is meaningless — "
        "re-measure before trusting the rest of this module")

    from scheduler_app.core.text_safety import redact_user_paths
    assert SENTINEL not in redact_user_paths(doubled)


# ── 2. The anti-cases: redaction must not corrupt the traceback ─────────────

# Real account names that are also common substrings. The middle three are the
# reason this module never replaces a bare account name.
_UNLUCKY_NAMES = ["a", "in", "is", "os", "Dersis"]

# Two lines from a real frozen traceback. Neither contains a profile path, so
# both must come back byte-identical whatever the account is called.
_MUST_SURVIVE = [
    r"  File \"build\Dersis.dist\scheduler_app\single_instance.py\", line 88",
    r'  File "<frozen os>", line 1004, in _find_and_load',
]


@pytest.mark.parametrize("name", _UNLUCKY_NAMES)
def test_an_unlucky_account_name_does_not_corrupt_the_traceback(
        monkeypatch, name):
    from scheduler_app.core.text_safety import redact_user_paths

    home = r"C:\Users" + "\\" + name
    drive, tail = os.path.splitdrive(home)
    monkeypatch.setenv("USERPROFILE", home)
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("HOMEDRIVE", drive)
    monkeypatch.setenv("HOMEPATH", tail)

    for line in _MUST_SURVIVE:
        assert redact_user_paths(line) == line, (
            "account name %r mangled a line that contains no profile path — "
            "this is the bare-substring replace, and it makes the report "
            "unreadable" % (name,))


def test_redaction_survives_odd_input(sentinel_home):
    from scheduler_app.core.text_safety import redact_user_paths

    assert redact_user_paths("") == ""
    assert redact_user_paths(None) == ""
    turkish = "Halen var olan bir dosya oluşturulamaz: 'ÇŞİĞÜÖ.egu'"
    assert redact_user_paths(turkish) == turkish
    big = (FAKE_HOME + r"\Documents\Dersis" + "\n") * 2000
    cleaned = redact_user_paths(big)
    assert isinstance(cleaned, str)
    assert SENTINEL not in cleaned


# ── 3. End to end: what actually goes into the mail ────────────────────────

class _FakeClipboard:
    def __init__(self):
        self.value = None

    def setText(self, text):
        self.value = text


@pytest.mark.ui
def test_the_crash_mail_carries_no_account_name(qapp, sentinel_home, monkeypatch):
    """Drive the real dialog; inspect the real URL and the real clipboard text.

    ``openUrl`` is made to return False so that both egress paths — the
    ``mailto:`` URL and the clipboard fallback — run in one pass and are both
    asserted on.
    """
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from scheduler_app.ui import bug_report

    opened = []
    monkeypatch.setattr(
        bug_report.QDesktopServices, "openUrl",
        staticmethod(lambda url: (opened.append(url.toString()), False)[1]))
    clip = _FakeClipboard()
    monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: clip))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    traceback_text = "\n".join(t for _, t in _LEAKY_SHAPES)
    dialog = bug_report.CrashReportDialog(
        "FileNotFoundError",
        r"[Errno 2] No such file: 'C:\\Users\\ZZUSERZZ\\Documents\\Dersis\\a.egu'",
        traceback_text,
        log_path=FAKE_HOME + r"\Documents\Dersis\logs\crash_log.txt",
    )
    try:
        dialog._send_crash_report()
    finally:
        dialog.deleteLater()

    assert opened, "no mailto: URL was produced"
    assert SENTINEL.lower() not in opened[0].lower(), (
        "the mailto: URL carries the account name")
    assert clip.value is not None, "the clipboard fallback wrote nothing"
    assert SENTINEL.lower() not in clip.value.lower(), (
        "the clipboard fallback carries the account name — one redaction has "
        "to cover both exits, and it does not")


@pytest.mark.ui
def test_the_manual_bug_report_is_redacted_too(qapp, sentinel_home, monkeypatch):
    """The other dialog funnels through the same single call site."""
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from scheduler_app.ui import bug_report

    opened = []
    monkeypatch.setattr(
        bug_report.QDesktopServices, "openUrl",
        staticmethod(lambda url: (opened.append(url.toString()), True)[1]))
    monkeypatch.setattr(QApplication, "clipboard",
                        staticmethod(_FakeClipboard))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    bug_report._open_mailto(
        "DERSİS Bug Report",
        "Steps to reproduce:\nopened " + FAKE_HOME + r"\Documents\Dersis\a.egu")

    assert opened
    assert SENTINEL.lower() not in opened[0].lower()


@pytest.mark.ui
def test_the_crash_log_on_disk_keeps_the_full_path(
        qapp, dersis_home, monkeypatch):
    """The deliberate split, pinned so a later tidy-up cannot quietly delete it.

    Drives the real ``scheduler_gui._global_exception_handler`` — 70 lines on
    the app's worst-day path that no test imported before this one. If someone
    later "finishes the job" by redacting the crash writer too, this goes red,
    and the module docstring says why that is a loss rather than a win.
    """
    import scheduler_gui
    from scheduler_app.storage import storage
    from scheduler_app.ui import bug_report

    shown = []
    monkeypatch.setattr(bug_report.CrashReportDialog, "exec",
                        lambda self: shown.append(self) or 0)

    leaky = FAKE_HOME + r"\Documents\Dersis\saves\9-A.egu"
    try:
        raise FileNotFoundError(2, "No such file or directory", leaky)
    except FileNotFoundError:
        import sys
        scheduler_gui._global_exception_handler(*sys.exc_info())

    assert shown, "the crash dialog never opened; the handler took another path"
    written = open(storage.crash_log_path(), encoding="utf-8").read()
    assert SENTINEL in written, (
        "redaction reached the on-disk crash log; that copy never leaves the "
        "machine and it is the only unredacted one a local maintainer has")


# ══ 4. The redaction must not damage what it is protecting ═════════════════


def _home_env(monkeypatch, home):
    drive, tail = os.path.splitdrive(home)
    monkeypatch.setenv("USERPROFILE", home)
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("HOMEDRIVE", drive)
    monkeypatch.setenv("HOMEPATH", tail)
    assert os.path.expanduser("~") == home
    return home


# Every row is a home directory whose account name is a strict *prefix* of some
# other profile's account name, and the text that other profile appears in.
# Measured before the boundary guard existed: the home needle matched inside
# the longer name, ate the ``X:\Users\`` anchor the second pass needs, and left
# the remainder of the neighbour's account name in the report —
# ``C:\Users\Ahmet\...`` came out as ``~hmet\...``.
_PREFIX_SHAPES = [
    (r"C:\Users\a",
     r"OSError: cannot open C:\Users\Ahmet\Documents\Dersis\9A.egu",
     r"OSError: cannot open C:\Users\<user>\Documents\Dersis\9A.egu"),
    (r"C:\Users\Test",
     r"C:\Users\TestOgretmen\Documents\Dersis",
     r"C:\Users\<user>\Documents\Dersis"),
    (r"C:\Users\os",
     r"C:\Users\osman\Documents\Dersis",
     r"C:\Users\<user>\Documents\Dersis"),
    (r"C:\Users\emre",
     r"C:\Users\emreu\Documents\Dersis",
     r"C:\Users\<user>\Documents\Dersis"),
    (r"C:\Users\a",
     r"C:\Users\All Users\Dersis",
     r"C:\Users\<user>\Dersis"),
    # The one that matters most: the neighbour's *surname* is what survives.
    (r"C:\Users\Ayse",
     r"C:\Users\Ayse Yilmaz\Documents\Dersis",
     r"C:\Users\<user>\Documents\Dersis"),
]


@pytest.mark.parametrize("home,text,expected", _PREFIX_SHAPES,
                         ids=[s[0].rsplit("\\", 1)[-1].replace(" ", "-")
                              + "-vs-" + s[1].split("\\")[2].replace(" ", "-")
                              for s in _PREFIX_SHAPES])
def test_a_home_that_prefixes_another_account_does_not_leak_that_account(
        monkeypatch, home, text, expected):
    r"""The home needle may only match at a path-segment boundary.

    ``re.sub(re.escape(home), "~", …)`` with no boundary is a *substring*
    replace, not the prefix replace the code claims to be. For a reporter
    called ``a``, ``os`` or ``Ayse`` it fires inside a colleague's account name
    and destroys the ``X:\Users\`` anchor that the second pass would have used
    to redact it properly.
    """
    from scheduler_app.core.text_safety import redact_user_paths

    _home_env(monkeypatch, home)
    cleaned = redact_user_paths(text)

    assert cleaned == expected, (
        "home %r left part of another profile's account name in the report"
        % (home,))


def test_the_two_passes_agree_on_one_traceback(monkeypatch):
    r"""The interaction, not each pass alone.

    One report carrying both this user's own home and a neighbour's profile
    whose name starts with it. The home pass must collapse the first to ``~``
    and must *not* touch the second, which belongs to the anchored pass.
    """
    from scheduler_app.core.text_safety import redact_user_paths

    _home_env(monkeypatch, r"C:\Users\Test")
    report = "\n".join([
        r'  File "C:\Users\Test\AppData\Local\Programs\Dersis\gui.py", line 12',
        r"PermissionError: 'C:\Users\TestOgretmen\Documents\Dersis\9A.egu'",
        r"also tried C:/Users/Test/Documents and C:/Users/TestOgretmen/Documents",
    ])

    assert redact_user_paths(report) == "\n".join([
        r'  File "~\AppData\Local\Programs\Dersis\gui.py", line 12',
        r"PermissionError: 'C:\Users\<user>\Documents\Dersis\9A.egu'",
        r"also tried ~/Documents and C:/Users/<user>/Documents",
    ])


def test_a_space_is_not_a_path_segment_boundary(sentinel_home):
    r"""Pins the recommendation this module deliberately did **not** adopt.

    Excluding the space from ``_USER_PROFILE_SEGMENT`` would stop the anchored
    pass eating the rest of a hand-typed line — and would publish the second
    half of every account name that contains a space. Windows hands those out
    freely (``All Users``, ``Ayse Yilmaz``, and the suite's own
    ``ZZ Long User Name ZZ``).

    The cost of keeping the space is over-redaction, which is the fail-safe
    direction and leaves a visible ``<user>`` marker; it cannot cross a
    newline, so it costs one line and never the report.
    """
    from scheduler_app.core.text_safety import redact_user_paths

    assert (redact_user_paths(r"C:\Users\Ayse Yilmaz\Documents\Dersis")
            == r"C:\Users\<user>\Documents\Dersis")
    assert (redact_user_paths(r"C:\Users\ZZ Long User Name ZZ\Documents")
            == r"C:\Users\<user>\Documents")
    assert (redact_user_paths("Dosyayi C:\\Users\\ayse konumuna kaydedemiyorum")
            == r"Dosyayi C:\Users\<user>")

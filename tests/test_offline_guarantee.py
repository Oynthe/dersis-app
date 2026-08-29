"""DERSİS reaches the network nowhere, and now that is checkable.

ST-SEC-005 (High) · `ui/app.py`, `ui/dialogs.py`
    Three code paths — PDF export and two copies of the Excel dependency check
    — asked "Install now?" and, on Yes, ran
    ``[sys.executable, "-m", "pip", "install", ...]`` with a 120 s timeout,
    blocking the Qt event loop. ``README-en.md:151`` says "Fully offline — no
    network calls of any kind"; those three sites were the app's only network
    reach, so the sentence was false.

What the register got wrong, and why it matters here
----------------------------------------------------
The register argues the branch on "it cannot work in the frozen build". That
premise is **false** for the build DERSİS actually ships. ``build_embed.bat``
installs pip via ``get-pip.py``, uncomments ``import site`` in ``python*._pth``,
and then *gates the build* on ``python.exe -m pip install -r
requirements-lock.txt`` — a shipping installer is proof ``-m pip`` works in that
prefix. ``sys.executable`` at runtime is ``{app}\\python\\pythonw.exe`` (the
``Dersis.exe`` stub spawns it), the install target ``%LOCALAPPDATA%\\Programs\\
Dersis`` is user-writable (``installer.iss`` sets ``PrivilegesRequired=lowest``),
and ``check_call([exe, "-m", "pip", "install", "--no-index", "reportlab"])`` from
a windowless process with NULL std handles was measured to return **exit 0**.

So this module does not assert "the install could not have worked". It asserts
the two things that are true and that a user can feel:

* a missing optional dependency produces a **message**, not a Yes/No offer and
  not a background process (``test_a_missing_*`` below), and
* no module in ``scheduler_app/`` can reach the network or shell out at all
  (``test_no_module_under_scheduler_app_*``).

Why not just grep for ``pip install``
-------------------------------------
Because 154 translation strings legitimately contain it — the seven
``errors.*_required`` keys across 22 locales end with "Install with: pip install
…", and they stay correct: a developer with a thin venv is the only audience
that can reach these branches, and a developer *can* run pip. The only string
that became a lie is ``dialogs.install.prompt`` ("Install now?"), which is no
longer concatenated onto anything. Zero locale edits were needed; measured, not
assumed.

A text grep would also stay green against ``ensurepip``, against
``urllib.request.urlopen``, and against a Yes/No dialog whose Yes does nothing.
The import census below is the same idea at the altitude that matters, and the
argv guard is a cheap second mechanism aimed at the exact regression.
"""
import ast
import contextlib
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_ROOT = os.path.join(REPO_ROOT, "scheduler_app")

# Importing any of these anywhere under scheduler_app/ would give the app a way
# off the machine. `multiprocessing` is deliberately absent: the CP-SAT worker
# is local compute, not egress. `os.startfile` is absent too — it opens a local
# file with the shell and is a plausible future "open the exports folder", so
# pinning it here would spend the test's credibility on the wrong thing.
_EGRESS_MODULES = frozenset({
    "subprocess", "urllib", "socket", "ftplib", "smtplib", "telnetlib",
    "requests", "httplib", "http", "xmlrpc", "poplib", "imaplib",
})
_EGRESS_OS_CALLS = frozenset({
    "system", "popen", "execv", "execve", "execvp", "spawnv", "spawnl",
    "spawnve",
})


def _package_sources():
    for dirpath, dirnames, filenames in os.walk(PKG_ROOT):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    yield os.path.relpath(path, REPO_ROOT), fh.read()


class _ImportBlocker:
    """A ``sys.meta_path`` finder that makes named top-level packages absent."""

    def __init__(self, roots):
        self._roots = frozenset(roots)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self._roots:
            raise ImportError(
                "blocked by test_offline_guarantee: %s" % fullname)
        return None


@contextlib.contextmanager
def unimportable(*roots):
    """Make *roots* raise ImportError, then put ``sys.modules`` back.

    Evicting the already-imported modules is the load-bearing half: without it
    ``import pandas`` finds the cached object and never consults ``meta_path``.
    """
    saved = {k: v for k, v in sys.modules.items()
             if k.split(".")[0] in roots}
    for key in saved:
        del sys.modules[key]
    blocker = _ImportBlocker(roots)
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(saved)


class _Recorder:
    """Stand-in for a modal ``QMessageBox`` static; records instead of blocking."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        return self.result

    @property
    def texts(self):
        return [a[2] for a in self.calls if len(a) > 2]


@pytest.fixture
def message_boxes(monkeypatch):
    """Record every modal message box the code under test raises."""
    from PyQt6.QtWidgets import QMessageBox

    recorders = {
        "information": _Recorder(QMessageBox.StandardButton.Ok),
        "warning": _Recorder(QMessageBox.StandardButton.Ok),
        "critical": _Recorder(QMessageBox.StandardButton.Ok),
        # Yes, deliberately: if anything still *asks*, the caller proceeds down
        # the old install path and the spawn guard below fires.
        "question": _Recorder(QMessageBox.StandardButton.Yes),
    }
    for name, rec in recorders.items():
        monkeypatch.setattr(QMessageBox, name, staticmethod(rec))
    return recorders


class _NoProcess:
    """A stand-in for ``subprocess.Popen`` that is callable **and subclassable**.

    A bare function will not do. ``asyncio.windows_utils`` runs
    ``class Popen(subprocess.Popen)`` at import time, and the first import of
    ``scheduler_app.ui.app`` inside a guarded test pulls asyncio in through
    deepdiff -> cachebox. With a function bound to ``subprocess.Popen`` that
    subclassing raises ``TypeError``, leaving a half-initialised ``asyncio`` in
    ``sys.modules`` and failing three unrelated tests with
    ``NameError: name 'base_events' is not defined``. Observed, then fixed here.
    """

    def __init__(self, *args, **kwargs):
        raise AssertionError(
            "the app tried to start a process: args=%r kwargs=%r"
            % (args, kwargs))


@pytest.fixture
def no_spawn(monkeypatch):
    """Turn any attempt to start a process into a loud failure."""
    import subprocess

    def _boom(*args, **kwargs):
        raise AssertionError(
            "the app tried to start a process: args=%r kwargs=%r"
            % (args, kwargs))

    for name in ("check_call", "check_output", "call", "run"):
        monkeypatch.setattr(subprocess, name, _boom)
    monkeypatch.setattr(subprocess, "Popen", _NoProcess)
    for name in ("system", "popen"):
        monkeypatch.setattr(os, name, _boom)
    return _boom


def _install_prompts():
    """Every locale's rendering of ``dialogs.install.prompt``."""
    from scheduler_app.i18n.translations import TRANSLATIONS

    return {loc: cat["dialogs.install.prompt"]
            for loc, cat in TRANSLATIONS.items()
            if cat.get("dialogs.install.prompt")}


def _assert_offers_nothing(texts):
    prompts = _install_prompts()
    assert prompts, "dialogs.install.prompt vanished — update this assertion"
    joined = "\n".join(texts)
    offered = sorted(loc for loc, value in prompts.items() if value in joined)
    assert not offered, (
        "the dialog still offers to install something (locales %r matched in "
        "%r)" % (offered, joined))


# ── 1. What the user sees when a dependency is missing ──────────────────────

@pytest.mark.ui
def test_a_missing_excel_dependency_is_reported_not_installed(
        qapp, message_boxes, no_spawn):
    """The 13-caller copy in dialogs.py states the problem and stops."""
    from scheduler_app.ui import dialogs
    from scheduler_app.translations import tr

    with unimportable("pandas", "openpyxl"):
        result = dialogs._ensure_excel_deps(None)

    assert result is False
    assert not message_boxes["question"].calls, (
        "a Yes/No question survived; the only honest answer to 'Install now?' "
        "is that DERSİS does not install anything")
    assert message_boxes["warning"].calls, "the user was told nothing at all"
    texts = message_boxes["warning"].texts
    assert any(tr("errors.pandas_openpyxl_required") in t for t in texts), texts
    _assert_offers_nothing(texts)


@pytest.mark.ui
def test_the_excel_check_the_main_window_runs_is_the_same_one(
        make_app, message_boxes, no_spawn, monkeypatch):
    """Rule 4: the copy the user actually runs.

    ``SchedulerApp._ensure_excel_deps`` was a byte-identical second
    implementation with 2 callers to the dialogs copy's 13. Driven through the
    real window, not through the module function.

    The last assertion is a *routing* one, not an identity one, and that
    distinction was measured. ``assert app._ensure_excel_deps is
    dialogs._ensure_excel_deps`` looks like it pins the collapse and does not:
    it stays green while the method carries a full second body, because the
    module-level import is still the same object. Substituting the shared
    function and demanding the method return *its* answer is the assertion a
    re-pasted copy fails.
    """
    from scheduler_app.ui import app as app_module
    from scheduler_app.ui import dialogs

    win = make_app()
    with unimportable("pandas", "openpyxl"):
        result = win._ensure_excel_deps()

    assert result is False
    assert not message_boxes["question"].calls
    _assert_offers_nothing(message_boxes["warning"].texts)

    assert app_module._ensure_excel_deps is dialogs._ensure_excel_deps, (
        "app.py imports something other than the dialogs implementation")
    sentinel = object()
    monkeypatch.setattr(app_module, "_ensure_excel_deps",
                        lambda parent: sentinel)
    assert win._ensure_excel_deps() is sentinel, (
        "SchedulerApp._ensure_excel_deps grew its own body again — that is the "
        "data_io/exporter.py shape Phase 6 was burned by: two copies, one "
        "well-exercised, the next fix landing on the wrong one")


@pytest.mark.ui
def test_a_missing_reportlab_is_reported_not_installed(
        make_app, message_boxes, no_spawn, monkeypatch):
    """PDF export explains itself instead of installing reportlab."""
    from scheduler_app.ui.tier_enforcement import TierEnforcement
    from scheduler_app.translations import tr

    monkeypatch.setattr(
        TierEnforcement, "can_use_feature", lambda self, feature: True)

    opened = []
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (opened.append(a), ("", ""))[1]))

    win = make_app()
    with unimportable("reportlab"):
        win._export_to_pdf()

    assert not message_boxes["question"].calls
    texts = message_boxes["warning"].texts
    assert any(tr("errors.reportlab_required") in t for t in texts), texts
    _assert_offers_nothing(texts)
    assert not opened, (
        "export continued past the missing dependency and asked for a filename")


# ── 2. The property the README claims ───────────────────────────────────────

def test_no_module_under_scheduler_app_can_reach_the_network():
    """`README-en.md:151`: "Fully offline — no network calls of any kind"."""
    offenders = []
    for relpath, source in _package_sources():
        tree = ast.parse(source, filename=relpath)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in _EGRESS_MODULES:
                        offenders.append(
                            "%s:%d import %s" % (relpath, node.lineno,
                                                 alias.name))
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if node.level == 0 and root in _EGRESS_MODULES:
                    offenders.append(
                        "%s:%d from %s import ..."
                        % (relpath, node.lineno, node.module))
            elif isinstance(node, ast.Call):
                fn = node.func
                if (isinstance(fn, ast.Attribute)
                        and isinstance(fn.value, ast.Name)
                        and fn.value.id == "os"
                        and fn.attr in _EGRESS_OS_CALLS):
                    offenders.append(
                        "%s:%d os.%s(...)" % (relpath, node.lineno, fn.attr))
    assert offenders == [], (
        "scheduler_app can reach off the machine again:\n  "
        + "\n  ".join(offenders))


def test_no_argv_under_scheduler_app_spells_pip_install():
    """A second, blunter mechanism aimed at the exact regression.

    Deliberately structural rather than textual: it looks at *adjacent elements
    of a list or tuple literal*, so the 154 translation strings that legitimately
    read "Install with: pip install …" cannot trip it and no exclusion list is
    needed to keep it quiet.
    """
    offenders = []
    for relpath, source in _package_sources():
        tree = ast.parse(source, filename=relpath)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)):
                continue
            words = [e.value for e in node.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            for first, second in zip(words, words[1:]):
                if (first, second) in (("-m", "pip"), ("pip", "install")):
                    offenders.append("%s:%d %r" % (relpath, node.lineno, words))
    assert offenders == [], (
        "a pip invocation is being built again:\n  " + "\n  ".join(offenders))


def test_the_only_url_the_app_can_open_is_behind_an_empty_constant():
    """The two ``webbrowser.open`` calls are dead, and stay dead by construction."""
    from scheduler_app.ui.tier_enforcement import PRICING_PAGE_URL

    assert PRICING_PAGE_URL == "", (
        "PRICING_PAGE_URL is no longer empty, so DERSİS can now open a remote "
        "page and README-en.md:151 needs rewriting rather than this test")

    unguarded = []
    for relpath, source in _package_sources():
        tree = ast.parse(source, filename=relpath)
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            guarded = any(isinstance(n, ast.Name) and n.id == "PRICING_PAGE_URL"
                          for n in ast.walk(node.test))
            if not guarded:
                continue
            for inner in ast.walk(node):
                if _is_webbrowser_open(inner):
                    inner._dersis_guarded = True
        for node in ast.walk(tree):
            if _is_webbrowser_open(node) and not getattr(
                    node, "_dersis_guarded", False):
                unguarded.append("%s:%d" % (relpath, node.lineno))
    assert unguarded == [], (
        "webbrowser.open() outside an `if PRICING_PAGE_URL:` guard:\n  "
        + "\n  ".join(unguarded))


def _is_webbrowser_open(node):
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "open"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "webbrowser")

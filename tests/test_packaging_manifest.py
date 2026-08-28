"""Four build lanes describe the same app four times — ST-ARCH-009, ST-SEC-003/007.

DERSİS is packaged by ``build_embed.bat`` (the shipped Windows lane),
``build_nuitka.bat``, an inline copy of the Nuitka command inside
``.github/workflows/build-installer.yml``, and ``Dersis-mac.spec`` (PyInstaller,
macOS). Each is a hand-maintained list of what goes in the box. Nothing has ever
compared them, and they had already drifted in two ways that no build failure
would report.

**The macOS bundle could not open its main window.** ``scheduler_gui.py`` imports
``scheduler_app.app``, ``scheduler_app.first_run`` and ``scheduler_app.translations``
— three of the 32 flat aliases that ``scheduler_app/__init__.py`` resolves through
a ``sys.meta_path`` shim at import time. **No file of those names exists.**
PyInstaller's static analysis records ``from pkg import name`` on a non-existent
submodule as an *attribute*, not a missing module, so it emits no warning and
collects nothing. Measured with PyInstaller's own modulegraph 6.22.2 against this
spec: **13 of 58 modules collected, 45 dropped, 0 warnings** — the 45 including
``ui/app.py`` (the main window) and ``i18n/translations.py`` (the entire
translation table). A real launch imports 37 modules, 24 of them absent from the
bundle. ``build-macos.yml`` only checked that the ``.dmg`` file exists, and the
README advertises that ``.dmg`` to every Mac user.

**The Nuitka lane produced `0.0.0` twice.** ``build_nuitka.bat`` never shipped
``VERSION`` and never wrote ``build\\version.iss``, so ``_version.py`` fell back
to ``_FALLBACK = "0.0.0"`` in the bug-report dialog, and its own closing advice
("Next step: iscc installer.iss") produced ``Dersis_Setup_v0.0.0.exe``. CI never
saw it because the workflow re-implements the whole Nuitka command inline and
generates ``version.iss`` itself.

The installer script is the last manifest in the chain, and this file pins the
two things about it that a well-meaning edit gets wrong in opposite directions:
the ACL nobody needed, and the AppId nobody may change.

Pure parsing — ``ast`` for the spec, text for the batch files and the ``.iss``.
No build tools, no PyInstaller, no Inno Setup, no ``scheduler_app`` import.
"""
import ast
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(REPO, "scheduler_app")

MAC_SPEC = "Dersis-mac.spec"
INSTALLER = "installer.iss"

# ── the AppId, frozen ───────────────────────────────────────────────────────
# This is a don't-change ratchet, the inverse of the usual test. See
# test_the_installer_appid_is_frozen for why the obvious "fix" is the bug.
FROZEN_APP_ID = "{{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"


def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


# ── scheduler_app's own shape ───────────────────────────────────────────────

def _shim_map():
    """`_SHIM_MAP` read out of the package source, never imported.

    Same technique as tests/test_import_layering.py: the flat aliases are the
    whole reason static analysis fails here, so the test has to know the real
    map rather than a copy of it that can drift.
    """
    for node in ast.walk(ast.parse(_read("scheduler_app", "__init__.py"))):
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "_SHIM_MAP":
                return ast.literal_eval(node.value)
    raise AssertionError(
        "scheduler_app/__init__.py no longer defines _SHIM_MAP as a literal; "
        "this module's shim awareness depends on being able to read it")


def _subpackages():
    """Every importable subpackage directory under `scheduler_app/`."""
    found = set()
    for dirpath, dirnames, filenames in os.walk(PKG):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        if "__init__.py" in filenames and os.path.abspath(dirpath) != os.path.abspath(PKG):
            rel = os.path.relpath(dirpath, os.path.dirname(PKG)).replace(os.sep, ".")
            found.add(rel)
    return found


# ── the PyInstaller spec, parsed ────────────────────────────────────────────

def _spec_value(name):
    """The right-hand side of the spec's top-level `name = ...` assignment."""
    for node in ast.walk(ast.parse(_read(MAC_SPEC))):
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
    raise AssertionError("%s does not assign %r" % (MAC_SPEC, name))


def _collect_calls(node, func_name):
    """Literal string arguments of every `func_name(...)` call inside `node`."""
    args = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if called != func_name:
            continue
        for arg in sub.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                args.append(arg.value)
    return args


def _spec_literals(name):
    """Every string constant appearing in the spec's `name = ...` expression."""
    return [
        node.value
        for node in ast.walk(_spec_value(name))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


# ── ST-ARCH-009: the macOS bundle ───────────────────────────────────────────

def test_the_mac_spec_collects_the_shimmed_scheduler_app_modules():
    """PyInstaller cannot find what only a meta-path finder knows about.

    ``scheduler_gui.py``'s three top-level imports name modules that do not exist
    as files. Static analysis follows them to nothing, warns about nothing, and
    the bundle ships without the main window. The fix is one line — declaring the
    package so PyInstaller walks it on disk instead of through the import
    statements.
    """
    hidden = _spec_value("hiddenimports")
    collected = _collect_calls(hidden, "collect_submodules")
    literal = set(_spec_literals("hiddenimports"))

    if "scheduler_app" in collected:
        return

    missing = sorted(set(_shim_map().values()) | _subpackages() | {"scheduler_app"} - literal)
    raise AssertionError(
        "%s does not call collect_submodules(\"scheduler_app\"), and does not "
        "name these modules explicitly either:\n  %s\n\n"
        "scheduler_app/__init__.py resolves %d flat aliases through a "
        "sys.meta_path shim, and scheduler_gui.py imports three of them "
        "(scheduler_app.app, .first_run, .translations) — none of which exist as "
        "files. PyInstaller's modulegraph records those as attributes, collects "
        "13 of 58 modules and emits zero warnings, so the .dmg builds green and "
        "dies on import."
        % (MAC_SPEC, "\n  ".join(missing), len(_shim_map()))
    )


def test_the_mac_spec_ships_the_data_files_reportlab_needs():
    """A PDF font is a data file, and data files are not found by import analysis.

    ``reportlab`` is listed in ``hiddenimports``, which collects Python modules
    and nothing else. Its bundled ``.ttf``/``.pfb`` faces live beside the code as
    package data; without them the mac build imports reportlab cleanly and then
    fails when the timetable export asks for a font.
    """
    datas = _spec_value("datas")
    assert "reportlab" in _collect_calls(datas, "collect_data_files"), (
        "%s does not collect reportlab's data files. Every other packaging "
        "manifest ships them (--include-package-data=reportlab in both Nuitka "
        "lanes; the embed lane installs the wheel whole), so the mac spec is the "
        "one place a font-dependent PDF export would break." % MAC_SPEC
    )


# ── the four manifests, compared ────────────────────────────────────────────

# Each entry: (file, [(what it must contain, why)]). Substring checks, because
# these are shell scripts and a shell script has no other structure to parse.
VERSION_SHIPPING = {
    "build_embed.bat": (
        'copy /y VERSION "%DIST_DIR%\\"',
        "the embed lane copies the file into the dist root, where "
        "_version.py's first candidate finds it",
    ),
    "build_nuitka.bat": (
        "--include-data-files=VERSION=VERSION",
        "Nuitka bundles no data file it is not told about, and _version.py's "
        "three candidate paths all miss, so __version__ silently becomes "
        '_FALLBACK = "0.0.0" in the bug report dialog and the About box',
    ),
    ".github/workflows/build-installer.yml": (
        'Copy-Item "VERSION"',
        "CI re-implements the Nuitka lane inline and has to repeat the copy",
    ),
    MAC_SPEC: (
        None,  # checked structurally below
        "the spec lists it in datas",
    ),
}

# The needle has to be the statement that *writes* the file, not a mention of
# its name: both batch lanes also test for its existence afterwards, and a
# needle of "build\version.iss" alone would stay green with the generation
# deleted. (Measured — it did, under mutation M17.)
VERSION_ISS_WRITING = {
    "build_embed.bat": '#define AppVersion "%APP_VERSION%"> "build\\version.iss"',
    "build_nuitka.bat": '#define AppVersion "%APP_VERSION%"> "build\\version.iss"',
    ".github/workflows/build-installer.yml": '| Out-File -FilePath "build\\version.iss"',
}


def test_every_manifest_ships_the_VERSION_file():
    """`0.0.0` in the About box is a packaging bug, not a version bug.

    ``scheduler_app/_version.py`` reads ``VERSION`` from the dist root, from
    inside the package, or from the working directory, and returns ``"0.0.0"``
    when all three miss. Every lane that produces a dist therefore has to put the
    file somewhere on that list.
    """
    for manifest, (needle, why) in sorted(VERSION_SHIPPING.items()):
        if manifest == MAC_SPEC:
            assert "VERSION" in _spec_literals("datas"), (
                "%s does not list VERSION in datas — %s" % (manifest, why))
            continue
        assert needle in _read(manifest), (
            "%s never ships the VERSION file (looked for %r): %s"
            % (manifest, needle, why)
        )


def test_every_windows_lane_writes_the_installer_version_include():
    """`installer.iss` silently falls back to 0.0.0 when the include is absent.

    ``installer.iss`` does ``#ifexist "build\\version.iss"`` / ``#else #define
    AppVersion "0.0.0"``, and ``OutputBaseFilename`` is
    ``Dersis_Setup_v{#AppVersion}``. A lane that tells the user "Next step: iscc
    installer.iss" without writing that include produces
    ``Dersis_Setup_v0.0.0.exe`` and an Add/Remove Programs entry to match.
    """
    for manifest, needle in sorted(VERSION_ISS_WRITING.items()):
        assert needle in _read(manifest), (
            "%s never generates build\\version.iss (looked for %r), so iscc "
            "falls back to AppVersion 0.0.0" % (manifest, needle)
        )


def test_every_nuitka_lane_declares_the_scheduler_app_package():
    """One `--include-package` covers the tree; the sub-package lines do not.

    Read from Nuitka's own source: ``--include-package=scheduler_app`` reaches
    ``Recursion._addIncludedModule``, which walks the package directory and
    recurses into every sub-package. The per-subpackage lines below it are
    decorative — which is exactly why their drift (the CI copy omits
    ``scheduler_app.i18n``) was harmless and invisible. This pins the line that
    is *not* decorative.
    """
    for manifest in ("build_nuitka.bat", ".github/workflows/build-installer.yml"):
        assert "--include-package=scheduler_app " in _read(manifest).replace("^", " ") \
               or "--include-package=scheduler_app\n" in _read(manifest), (
            "%s does not pass --include-package=scheduler_app; a Nuitka build "
            "would ship whatever --follow-imports happened to reach" % manifest
        )


def test_every_manifest_ships_the_runtime_resources():
    """Flags, the branding PNG and the icon set are looked up by relative path."""
    resources = {
        "build_nuitka.bat": (
            "--include-data-dir=flags=flags",
            "--include-data-files=docs/dersis.png=docs/dersis.png",
            "--include-data-dir=scheduler_app/assets=scheduler_app/assets",
        ),
        ".github/workflows/build-installer.yml": (
            "--include-data-dir=flags=flags",
            "--include-data-files=docs/dersis.png=docs/dersis.png",
            "--include-data-dir=scheduler_app/assets=scheduler_app/assets",
        ),
    }
    for manifest, needles in sorted(resources.items()):
        text = _read(manifest)
        for needle in needles:
            assert needle in text, "%s is missing %r" % (manifest, needle)

    spec_datas = _spec_literals("datas")
    for needed in ("flags", "docs/dersis.png", "scheduler_app/assets"):
        assert needed in spec_datas, "%s is missing %r from datas" % (MAC_SPEC, needed)


# ── ST-SEC-003: the ACL on {app} ────────────────────────────────────────────

def _iss_sections():
    """`{section_lower: [line, ...]}` for `installer.iss`, comments dropped."""
    sections = {}
    current = None
    for raw in _read(INSTALLER).splitlines():
        line = raw.strip()
        if line.startswith(";") or not line:
            continue
        match = re.match(r"^\[([A-Za-z]+)\]$", line)
        if match:
            current = match.group(1).lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return sections


def _iss_setup():
    """`[Setup]` parsed as `{key_lower: value}`."""
    out = {}
    for line in _iss_sections().get("setup", []):
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip().lower()] = value.strip()
    return out


def test_the_installer_does_not_make_the_program_directory_world_writable():
    """`Permissions: users-modify` on `{app}` was granted for a write that never happens.

    All three candidate justifications were measured false. ``build_embed.bat``
    compiles ``scheduler_app`` with ``compileall -b`` and then deletes every
    ``.py``, so no ``__pycache__`` is ever written there. A write-denied directory
    imports sourceless ``.pyc`` fine — probed with ``icacls /deny (WD,AD)``:
    ``rc=0``, zero files created, because CPython swallows the bytecode
    ``OSError``. And ``scheduler_gui.py`` tries ``~/Documents`` first and the temp
    directory third, so its log path never depends on ``{app}``.

    What the grant does do is measured too. ``PrivilegesRequired=lowest`` means
    ``{autopf}`` always resolves to ``%LOCALAPPDATA%\\Programs``, whose fresh
    directories inherit exactly SYSTEM, Administrators and the installing user —
    no ``BUILTIN\\Users``. Adding that ACE turns a single-user directory into one
    every local account on a shared staffroom or lab machine can modify, and
    ``{app}\\python\\Lib\\site-packages`` is a very good place to plant code.
    """
    for line in _iss_sections().get("dirs", []):
        assert "{app}" not in line or "permissions" not in line.lower(), (
            "installer.iss [Dirs] grants %r. Delete it: nothing needs it, and it "
            "makes the program directory writable by every local account. If a "
            "writable location is ever genuinely required it must be a "
            "subdirectory, never {app} itself." % line
        )


def test_the_installer_appid_is_frozen():
    """The obvious fix — "replace the placeholder GUID" — is itself the bug.

    ``{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}`` is the canonical tutorial GUID and
    looks exactly like something a helpful reviewer should replace. Inno Setup
    keys **upgrade and uninstall detection** on AppId. ``Dersis_Setup_v1.0.0.exe``
    at ``/releases/latest`` has 105+ downloads. A new AppId makes every one of
    those installs invisible to the next setup: it installs over the same files,
    leaves two Add/Remove Programs entries, and uninstalling either deletes the
    other's files.

    Colliding with another product that pasted the same tutorial GUID is strictly
    less bad than orphaning the installed base. If the collision risk is ever
    judged unacceptable, the only correct migration is an ``InitializeSetup``
    that runs the old ``UninstallString`` first — a real feature, not a
    cosmetic edit.
    """
    assert _iss_setup().get("appid") == FROZEN_APP_ID, (
        "installer.iss AppId is %r, expected %r. This value must never change. "
        "See this test's docstring before touching it."
        % (_iss_setup().get("appid"), FROZEN_APP_ID)
    )


def test_the_installer_never_asks_for_administrator():
    """`lowest` is what keeps `{autopf}` out of Program Files.

    Inno's own words: with ``PrivilegesRequired=lowest`` Setup "will not request
    to be run with administrative privileges even if it was started by a member
    of the Administrators group". That is what makes the default install
    ``%LOCALAPPDATA%\\Programs\\Dersis`` on every path. Adding
    ``PrivilegesRequiredOverridesAllowed`` re-opens the Program Files
    destination, and with it the privilege-escalation variant of ST-SEC-003.
    """
    setup = _iss_setup()
    assert setup.get("privilegesrequired") == "lowest", (
        "installer.iss PrivilegesRequired is %r; it must stay 'lowest'"
        % setup.get("privilegesrequired")
    )
    assert "privilegesrequiredoverridesallowed" not in setup, (
        "installer.iss allows the privileges requirement to be overridden, which "
        "puts an elevated install back into Program Files"
    )


def test_the_uninstaller_removes_the_bundled_python_tree():
    """The uninstaller cleaned seven directories that cannot exist and missed the one that does.

    ``build_embed.bat`` deletes every ``.py`` under ``scheduler_app``, so the
    ``scheduler_app\\...\\__pycache__`` entries were unreachable. Meanwhile the
    ~2,639 ``.py`` files in the bundled ``site-packages`` do get ``__pycache__``
    written beside them on first run, inside ``{app}``, and none of those files
    are in Inno's install log — so without an explicit entry the uninstaller
    leaves the whole tree behind and the parent directories fail to delete as
    non-empty.
    """
    entries = " ".join(_iss_sections().get("uninstalldelete", []))
    assert "{app}\\python" in entries, (
        "installer.iss [UninstallDelete] does not cover {app}\\python, the only "
        "place first-run bytecode is actually written"
    )
    assert "scheduler_app\\core\\__pycache__" not in entries, (
        "installer.iss [UninstallDelete] still lists scheduler_app __pycache__ "
        "paths. build_embed.bat deletes every .py under scheduler_app, so those "
        "directories cannot exist; keeping them reads as coverage and is not."
    )


def test_every_installer_source_exists_or_is_explicitly_optional():
    """A `[Files] Source:` that silently is not there ships a smaller installer.

    ``vc_redist.x64.exe`` is deliberately absent from the repository, and carries
    ``skipifsourcedoesntexist`` to say so. Anything else missing is a mistake
    that Inno would report only as a compile error on a machine that happens to
    have run a build first.
    """
    optional = {"installer\\vc_redist.x64.exe"}
    seen = set()
    for line in _iss_sections().get("files", []):
        match = re.search(r'Source:\s*"([^"]+)"', line)
        if not match:
            continue
        source = match.group(1)
        if source.lower().startswith("build\\"):
            continue  # produced by the build, absent from a clean checkout
        seen.add(source)
        if "skipifsourcedoesntexist" in line.lower():
            assert source in optional, (
                "installer.iss marks %r as skippable but this test does not know "
                "it is optional. Add it to `optional` with a reason, or drop the "
                "flag so a missing file is loud." % source
            )
            continue
        assert os.path.isfile(os.path.join(REPO, source.replace("\\", os.sep))), (
            "installer.iss [Files] references %r, which does not exist and is "
            "not marked skipifsourcedoesntexist" % source
        )
    assert optional <= seen, (
        "the optional-source allow-list names %s, which installer.iss no longer "
        "references" % sorted(optional - seen)
    )

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


# ── ST-SEC-007: the launcher every shortcut points at ───────────────────────
#
# Phase 7 replaced build-release.yml with release.yml and, in the rewrite,
# dropped `"$dist\Dersis.exe"` from the Windows "Verify build output" list that
# the deleted workflow had carried. Nothing else in the chain would have
# noticed: build_embed.bat compiles that file with `Add-Type ... 2>$null` and
# used to print "[WARN] exe failed, using .vbs" and carry on; its end-of-build
# gate did not count it; and its `if %ERRORS% GTR 0` branch had no `exit /b 1`,
# so even a counted missing file left the script exiting 0. The installer's own
# size gate is `-lt 1MB` against a ~113 MiB artefact, so it passes on a bundle
# that is complete apart from the one file every shortcut names.
#
# The tests below pin the three links of that chain independently — and pin
# them against *disabling*, not merely against deletion. The first draft of this
# section did neither: it asked `"$dist\Dersis.exe" in run_body` and
# `"exit /b 1" in build_embed_text`, so a PowerShell `#` in front of the array
# member kept it green (measured: mutation R1, exit 0) and the two gates
# build_embed.bat grew for this chain were pinned by nothing at all (measured:
# mutations M3 and M4, both exit 0, both surviving all 105 tests in the
# packaging and docs lane). The helpers in `tests/_support/pwsh_parse.py` exist
# so that the array is read as an array.

WORKFLOW_DIR = os.path.join(REPO, ".github", "workflows")


def _verify_build_output_run():
    """The `run:` body of release.yml's Windows `Verify build output` step."""
    import yaml

    with open(os.path.join(WORKFLOW_DIR, "release.yml"), encoding="utf-8") as fh:
        workflow = yaml.safe_load(fh)
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step, dict) and step.get("name") == "Verify build output":
                return str(step.get("run") or "")
    raise AssertionError(
        "release.yml has no step named 'Verify build output'. If it was renamed, "
        "rename it here too — this test is the only thing standing between a "
        "failed launcher build and a published installer."
    )


def _verified_dist_relative_paths():
    """What the verify step's `foreach` *actually* iterates, `$dist`-relative.

    Substring matching over the step body cannot tell a live array member from
    a commented-out one, and cannot tell either from the path appearing inside
    a ``Write-Host``. All three read as "checked". This returns the live
    members only, with the body's own ``$dist`` assignment expanded and
    stripped, so the caller can assert exact membership.
    """
    from _support.pwsh_parse import (
        expand, powershell_scalar_assignments, powershell_string_array)

    body = _verify_build_output_run()
    members = powershell_string_array(body, "checks")
    assert members is not None, (
        "release.yml's 'Verify build output' step no longer contains a "
        "`$checks = @( ... )` array. If the step was rewritten in another "
        "shape, this parser has to be rewritten with it — a check nobody can "
        "read is a check nobody is pinning."
    )
    assignments = powershell_scalar_assignments(body)
    dist = assignments.get("dist")
    assert dist, (
        "the 'Verify build output' step no longer assigns `$dist = \"...\"`; "
        "the paths below cannot be resolved against the build directory."
    )
    prefix = dist + "\\"
    return {expand(m, assignments)[len(prefix):]
            for m in members if expand(m, assignments).startswith(prefix)}


def _app_rooted_targets():
    """Every `{app}\\…` file the [Icons] and [Run] sections *launch*.

    The lookbehind matters: ``IconFilename:`` is a different key with a
    different failure mode. A missing icon is cosmetic — Windows draws a
    default one — while a missing ``Filename:`` target is a shortcut that does
    nothing when clicked. Only the second is worth failing a release over.
    """
    sections = _iss_sections()
    targets = set()
    for name in ("icons", "run"):
        for line in sections.get(name, []):
            for match in re.finditer(
                    r'(?<![A-Za-z])Filename:\s*"\{app\}\\([^"]+)"', line):
                targets.add(match.group(1))
    return targets


def test_every_installer_shortcut_target_is_a_verified_build_output():
    """ST-SEC-007 — a shortcut that points at a file the build never made.

    A user installs DERSİS, the installer reports success, and the Start Menu
    entry, the Desktop icon and the "Launch DERSİS" checkbox all do nothing,
    because the file all three name was never produced and no step checked.

    ``test_every_installer_source_exists_or_is_explicitly_optional`` cannot see
    this: it reads ``[Files] Source:`` and ``continue``s on anything under
    ``build\\``, and ``[Icons]``/``[Run]`` have no ``Source:`` at all.
    """
    checked = _verified_dist_relative_paths()
    targets = _app_rooted_targets()
    assert targets, (
        "installer.iss's [Icons]/[Run] name no {app}-rooted file at all. Either "
        "the shortcut section was gutted or the parser above stopped matching."
    )
    assert checked, (
        "release.yml's 'Verify build output' checks nothing under $dist at all."
    )
    for target in sorted(targets):
        assert target in checked, (
            "installer.iss points a user-facing entry at {app}\\%s, but "
            "release.yml's 'Verify build output' step never checks that the "
            "build produced it. It checks %s. That is exactly how Dersis.exe "
            "went unverified: the build can exit 0 without it, the installer's "
            "`-lt 1MB` size gate cannot see it, and the failure surfaces as a "
            "shortcut that does nothing on the user's machine. Note that this "
            "reads the live members of the `$checks` array, so commenting the "
            "line out counts as removing it." % (target, sorted(checked))
        )


def test_every_build_script_exits_nonzero_on_a_missing_critical_file():
    """ST-SEC-007 — a build that counts its own errors and then returns success.

    Both Windows lanes tally missing critical files into ``ERRORS``. If the
    final branch does not ``exit /b 1``, the CI step that called the script sees
    success and the release continues on an incomplete bundle.

    ``build_nuitka.bat`` has always exited 1 here and its comment claimed both
    lanes did; ``build_embed.bat`` printed a warning and exited 0. A comment is
    not a test, which is why this one exists.
    """
    for script in ("build_embed.bat", "build_nuitka.bat"):
        text = _read(script)
        match = re.search(r"if %ERRORS% GTR 0 \((.*?)^\)", text, re.S | re.M)
        assert match, (
            "%s no longer has an `if %%ERRORS%% GTR 0 (` block. The counter is "
            "only worth keeping if something branches on it." % script
        )
        assert "exit /b 1" in match.group(1), (
            "%s counts missing critical files into %%ERRORS%% and then exits 0. "
            "A CI step calling it reads that as a successful build, so the "
            "release proceeds to compile an installer out of a bundle the "
            "script itself just reported as incomplete." % script
        )


def _add_type_lines():
    """The non-comment ``build_embed.bat`` lines that invoke ``Add-Type``.

    ``::`` comment lines are excluded deliberately: the comment above the call
    quotes the redirection it is explaining, and a scanner that cannot tell
    code from the prose describing it fails on its own documentation.
    """
    text = _read("build_embed.bat")
    lines = [ln for ln in text.splitlines()
             if "Add-Type" in ln and not ln.lstrip().startswith("::")]
    assert lines, "build_embed.bat no longer compiles a Dersis.exe launcher"
    return lines


# Matched as patterns rather than as one literal string. The first draft
# asserted `"2>$null" not in line`, which is a blacklist of exactly one
# spelling: `2> $null` with a space and `-ErrorAction SilentlyContinue` both
# passed it (measured, mutations M5 and M6, exit 0) and both delete the same
# thing — a probe against deliberately broken C# showed each one removing the
# per-line compiler diagnostic and the `>>>` source echo, leaving only the
# generic "Cannot add type. Compilation errors occurred."
#
# `| Out-Null` is deliberately NOT listed. It was proposed, and measurement
# says it belongs to a different stream: piping Add-Type's output leaves the
# diagnostics byte-for-byte intact, because they travel the error stream, not
# the pipeline. Banning it would be a rule this test cannot justify.
_SUPPRESSORS = (
    (re.compile(r"2\s*>\s*\$null"), "a `2>$null` stderr redirection"),
    (re.compile(r"-ErrorAction\s+(SilentlyContinue|Ignore)"),
     "an `-ErrorAction SilentlyContinue`/`Ignore` preference"),
)


def test_the_launcher_compile_step_does_not_discard_its_error():
    """ST-SEC-007 — the one build failure whose cause was thrown away.

    ``Dersis.exe`` is compiled at build time rather than copied, so it is the
    single critical file that can go missing without anything upstream having
    failed to *find* something. With the compiler's stderr redirected to
    ``$null`` there was no way to learn why from the build log — the only
    evidence was a ``[WARN]`` line in an otherwise green run.
    """
    for pattern, spelling in _SUPPRESSORS:
        for line in _add_type_lines():
            assert not pattern.search(line), (
                "build_embed.bat discards the C# compiler's diagnostics on the "
                "Add-Type that produces Dersis.exe, via %s. Every installer "
                "shortcut points at that file; when the compile fails, the "
                "reason is the only thing that makes it fixable, and this is "
                "what throws it away." % spelling
            )


def test_the_launcher_compile_failure_stops_the_build_immediately():
    """ST-SEC-007 — the gate that turns a failed compile into a failed build.

    ``build_embed.bat`` grew an ``if errorlevel 1 (... exit /b 1)`` block
    directly after the ``Add-Type`` call. Nothing read it: deleting the whole
    block left the packaging and release suites green (measured, mutation M3,
    exit 0 across 105 tests), and a whole-file ``"exit /b 1" in text`` check
    cannot help — the script contains ten of them.

    Scoped by position rather than by line number: the gate must be the first
    line of actual code after the compile, so that inserting a blank line or a
    ``::`` comment between the two stays green while removing the gate does not.
    """
    lines = _read("build_embed.bat").splitlines()
    compiles = [i for i, ln in enumerate(lines)
                if "Add-Type" in ln and not ln.lstrip().startswith("::")]
    assert len(compiles) == 1, (
        "expected exactly one Add-Type invocation in build_embed.bat, found %d "
        "on lines %s — the positional gate below has no single call to attach "
        "to." % (len(compiles), [i + 1 for i in compiles])
    )

    index = compiles[0] + 1
    while index < len(lines) and (not lines[index].strip()
                                 or lines[index].lstrip().startswith("::")):
        index += 1
    assert index < len(lines) and lines[index].strip() == "if errorlevel 1 (", (
        "the first statement after build_embed.bat's Add-Type call is %r, not "
        "`if errorlevel 1 (`. cmd does not stop on a failed command, so "
        "without that gate a failed C# compile continues into `iscc "
        "installer.iss` and produces an installer whose Start Menu entry, "
        "Desktop icon and post-install \"Launch program\" all point at a file "
        "that was never built."
        % (lines[index].strip() if index < len(lines) else "<end of file>")
    )
    body = []
    for line in lines[index + 1:]:
        if line.rstrip() == ")":
            break
        body.append(line)
    assert any(ln.strip() == "exit /b 1" for ln in body), (
        "build_embed.bat checks `errorlevel` after compiling Dersis.exe and "
        "then does not `exit /b 1`. The CI step that calls the script reads "
        "the script's own exit code, so a gate that only prints is a gate that "
        "does nothing. Block body was:\n  %s" % "\n  ".join(body)
    )


def test_the_launcher_is_one_of_the_files_the_build_counts_as_critical():
    """ST-SEC-007 — the entry in build_embed.bat's own verify list.

    ``test_every_build_script_exits_nonzero_on_a_missing_critical_file`` pins
    that the ``if %ERRORS% GTR 0`` branch exits non-zero. It does not pin that
    anything ever counts a missing ``Dersis.exe`` into ``ERRORS`` — so deleting
    that entry both removed the check and neutered the branch for this file,
    with the suite green (measured, mutation M4, exit 0).

    Scoped to the region between ``set ERRORS=0`` and ``if %ERRORS% GTR 0``, so
    a mention of the launcher in prose or in the success banner cannot satisfy
    it.
    """
    text = _read("build_embed.bat")
    start = text.index("set ERRORS=0")
    end = text.index("if %ERRORS% GTR 0 (")
    assert start < end, "build_embed.bat counts ERRORS after branching on them"
    region = text[start:end]

    subjects = set(re.findall(r'if exist "%DIST_DIR%\\([^"]+)"', region))
    assert "Dersis.exe" in subjects, (
        "build_embed.bat's verify list no longer checks for Dersis.exe. It "
        "checks %s. Dersis.exe is the one item in that list compiled at build "
        "time rather than copied, so it is the only one that can go missing "
        "without anything upstream having failed to *find* a file."
        % sorted(subjects)
    )
    match = re.search(
        r'if exist "%DIST_DIR%\\Dersis\.exe"\s*\((.*?)^\)$',
        region, re.S | re.M)
    assert match and "set /a ERRORS+=1" in match.group(1), (
        "build_embed.bat tests for Dersis.exe but its else-branch never does "
        "`set /a ERRORS+=1`, so the end-of-build gate cannot see it missing."
    )


# ── ST-SEC-004: the lock file against the floors it claims to satisfy ───────

def _normalise(name):
    return name.strip().lower().replace("_", "-")


def _parse_pins(name, sep):
    """`{package_lower: version_spec}` for a requirements file.

    Only ever called with ``sep="=="`` on the lock file, where every line is an
    exact pin by construction. requirements.txt is read by ``_requirements``
    instead — see the anti-vacuity note in the test below for why a separator
    split is the wrong tool for a file that may use any operator.
    """
    out = {}
    for raw in _read(name).splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or sep not in line:
            continue
        pkg, _, spec = line.partition(sep)
        out[_normalise(pkg)] = spec.strip()
    return out


def _requirements():
    """Every requirement in requirements.txt, parsed, whatever the operator."""
    from packaging.requirements import Requirement

    out = []
    for raw in _read("requirements.txt").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            out.append(Requirement(line))
        except Exception:
            continue
    return out


def test_every_locked_pin_satisfies_the_requirements_floor():
    """ST-SEC-004 — the shipped installer built against a version we disallow.

    ``requirements-lock.txt`` is what the three BUILD lanes install into the
    bundle users run (build-installer.yml:111, build_nuitka.bat:46,
    build_embed.bat:133). The test lanes install the unpinned
    ``requirements.txt``, so a lock pin below its own declared floor would ship
    a version the suite never exercises and never turn anything red.

    This deliberately does NOT assert ``lock == pip freeze``: the audit venv is
    a development environment carrying 16 packages the lock does not describe,
    so that test would be red by construction and deleted within a week.
    """
    from packaging.version import Version

    required = _requirements()
    locked = _parse_pins("requirements-lock.txt", "==")

    # Anti-vacuity, and the reason this test is written with a real parser.
    # The first draft split each line on `>=` and `continue`d when it was not
    # there, so a requirement written `ortools==9.7` — or `>`, or `~=` — left
    # the floors dict entirely and stopped being checked, silently and one
    # package at a time. The old `assert floors` guard fired only if ALL of
    # them vanished. Measured: `ortools==9.7` in requirements.txt against
    # `ortools==1.0.0` in the lock was green.
    declared = [ln for ln in (raw.split("#", 1)[0].strip()
                              for raw in _read("requirements.txt").splitlines())
                if ln]
    assert len(required) == len(declared), (
        "requirements.txt has %d requirement lines but only %d of them parsed: "
        "%s. A line this test cannot read is a dependency it stops checking, "
        "which is exactly the failure this assertion exists to make loud."
        % (len(declared), len(required),
           sorted(set(declared) - {str(r) for r in required}))
    )

    names = {_normalise(req.name): req for req in required}
    missing = sorted(set(names) - set(locked))
    assert not missing, (
        "requirements.txt requires %s, which requirements-lock.txt does not pin. "
        "The build lanes install ONLY the lock file, so an unpinned direct "
        "dependency is simply absent from the shipped app." % missing
    )

    for pkg, req in sorted(names.items()):
        assert Version(locked[pkg]) in req.specifier, (
            "requirements-lock.txt pins %s==%s, which does not satisfy "
            "requirements.txt's `%s`. The build lanes read the lock and the "
            "test lanes read requirements.txt, so this ships a version nothing "
            "in CI has ever imported." % (pkg, locked[pkg], req)
        )

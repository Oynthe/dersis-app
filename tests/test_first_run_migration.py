"""The upgrade path must not eat the schedule — ST-ARCH-001 item 6.

Item 6 of the audit's "top 10 highest-risk untested behaviors" is *first-run /
legacy migration ordering*. The register states it as "language gate creates
dirs before ``ensure_dirs``, defeating migration". **That half is fixed**, as a
side effect of ST-DATA-012: ``scheduler_gui.main`` claims the single-instance
lock before the gate, and ``single_instance.default_lock_path`` calls
``storage.ensure_dirs()``. So the *folder* is created in the right order.

The **file** migration was left behind, and it is a live data loss.

``storage.migrate_legacy_files`` was reachable from exactly one place,
``ui/app.py``'s ``SchedulerApp.__init__`` — which runs *after*
``run_language_gate()``. The gate writes ``language_chosen`` through
``ui/first_run._write_flag``, and that **creates** ``settings/app_settings.egu``.
``_migrate_json_file`` then refuses, because its second guard is::

    if os.path.exists(dest_sav):
        _backup_original(src)      # move the user's file out of the way
        return False               # ...and do not migrate it

So a user upgrading from the pre-Dersis build picks a language and lands on an
empty timetable. Their whole schedule survives only as an unreferenced
``backups/scheduler_config.json``, with nothing in the UI saying so.

**The ordering was only half of it.** ``storage._old_app_config_path()``
resolved its "app directory" from ``storage.py``'s own ``__file__``, so it
looked in ``{app}/scheduler_app/storage/`` — two directories below the place
its docstring named, and a place the legacy file can never be. Measured end to
end with the payload written beside ``scheduler_gui.py`` and **nothing**
stubbed::

    before: notes []   settings keys []             legacy file still in place
    after:  notes ['Migrated scheduler_config.json → settings/app_settings.egu']
            settings keys ['language', 'last_file', 'state']

``sys.frozen`` never rescued it. ``build_embed.bat`` ships ``Dersis.exe`` as a
C# wrapper that runs ``{app}\\python\\pythonw.exe {app}\\scheduler_gui.py``, so
the frozen branch is dead on the build the installer produces, and
``dirname(sys.executable)`` would be ``{app}\\python`` even if it were not.
``_app_dir()`` now climbs the package root instead, and step 1 also checks
``~/.class_scheduler/scheduler_config.json`` — the location
``dersis-mapped/09_SETTINGS_LOCALIZATION_AND_PERSISTENCE_MAP.md`` documents.

What the migration does **not** carry over is the saved language. The gate
writes ``language`` from the dialog *after* the migration, so the value that
survives is always the one the user just picked. Measured against the real
probe: legacy ``tr`` + dialog ``en`` → ``en``; legacy ``tr`` + dialog ``tr`` →
``tr``; legacy ``de`` + dialog ``en`` → ``en``. An earlier revision of this
module asserted ``language == 'tr'`` while stubbing the dialog to ``tr`` as
well — it was reading the stub, and stayed green with the fix removed. Its
replacement is ``last_file``: a legacy key nothing downstream rewrites, which
does redden when the migration is taken out.

The guard is deliberately in two halves, because neither alone is enough:

* a **behavioural** test that runs the real ``scheduler_gui.main()`` in a
  subprocess (0.9 s), because the defect is an *ordering* between two calls that
  each work perfectly in isolation. The first version of this test called
  ``migrate_legacy_files()`` itself and then the gate — it passed **before** the
  fix and pinned nothing, which is the ``tests/README.md`` trap verbatim;
* a **structural** ``ast`` test over ``main()``, which states the ordering rule
  directly and costs nothing.

``scheduler_gui.py`` is imported by **zero** other tests in this suite, which is
why nothing saw this for seven phases. That also makes the behavioural test the
first thing in the repository to execute the real startup path end to end — the
"offscreen smoke launch" the roadmap asks for, arriving as a side effect.
"""
import json
import os
import sys

import pytest


LEGACY_LAST_FILE = r"D:\Okul\2019_2020_guz.uva"


def _write_legacy_config(app_dir, *, language="tr", class_name="LEGACY-LESSON"):
    """Write the pre-Dersis ``scheduler_config.json`` beside the "executable"."""
    cfg = {
        "language": language,
        "last_file": LEGACY_LAST_FILE,
        "state": {
            "classes": [{"name": class_name, "code": "LGC101"}],
            "days": ["monday"],
            "slots": ["1. Ders"],
        },
    }
    path = os.path.join(app_dir, "scheduler_config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return path


@pytest.fixture
def frozen_install(tmp_path, monkeypatch):
    """Make ``storage._old_app_config_path()`` resolve to a fake install dir.

    A real user's legacy config sits in the app directory, which for the
    checkout is the repository root — no test may write there. ``_app_dir()``
    honours ``sys.frozen``/``sys.executable``, so moving those two moves the
    anchor while the join, the basename and the candidate list stay real.
    """
    import sys

    app_dir = tmp_path / "install"
    app_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app_dir / "Dersis.exe"))
    return app_dir


def test_the_legacy_config_is_looked_for_beside_the_entry_script():
    """The path half of item 6, asserted against the **unstubbed** resolver.

    Both guards below stub ``_old_app_config_path`` (they have to: no test may
    write into the install directory it is running from), so without this one
    nothing in the suite ever executes the function that decides whether the
    file is found. It was resolving to
    ``{app}/scheduler_app/storage/scheduler_config.json`` while its own
    docstring said "the app directory", and every guard stayed green.

    A failure means the lookup has drifted away from the directory
    ``scheduler_gui.py`` is started from — on the shipped build, the directory
    the C# ``Dersis.exe`` wrapper passes to ``pythonw.exe``.
    """
    from scheduler_app.storage import storage

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.isfile(os.path.join(repo, "scheduler_gui.py")), (
        "this test's anchor moved: the entry script is no longer at %s" % repo)

    resolved = storage._old_app_config_path()  # real, unstubbed
    assert os.path.basename(resolved) == "scheduler_config.json"
    assert os.path.normcase(os.path.dirname(resolved)) == os.path.normcase(repo), (
        "the legacy config is looked for in %r, but a pre-Dersis install put it "
        "beside the entry script in %r. Nothing will ever be migrated from a "
        "directory inside the installed package tree."
        % (os.path.dirname(resolved), repo))

    candidates = [os.path.normcase(p) for p in storage._old_app_config_candidates()]
    documented = os.path.normcase(
        os.path.join(storage._OLD_DATA_DIR, "scheduler_config.json"))
    assert documented in candidates, (
        "09_SETTINGS_LOCALIZATION_AND_PERSISTENCE_MAP.md names %r as the legacy "
        "source; step 1 no longer looks there: %r" % (documented, candidates))


def test_a_legacy_config_in_the_app_directory_is_migrated(dersis_home, frozen_install):
    """The app-directory candidate, end to end, in process.

    ``frozen_install`` moves the *whole* resolver — ``sys.frozen`` plus
    ``sys.executable`` — rather than replacing ``_old_app_config_path``, so the
    join and the basename are the real ones; only the anchor directory is a
    tmp_path. The unstubbed anchor is pinned by the test above.
    """
    from scheduler_app.storage import storage

    _write_legacy_config(str(frozen_install))

    notes = storage.migrate_legacy_files()

    assert any("scheduler_config.json" in n for n in notes), (
        "a legacy config sitting in the app directory was not migrated: %r" % notes)
    settings = storage.load_encrypted(storage.settings_path())
    assert [c["name"] for c in settings.get("state", {}).get("classes", [])] \
        == ["LEGACY-LESSON"]
    assert not os.path.exists(frozen_install / "scheduler_config.json"), (
        "the migrated original was left in place; it will be re-migrated or "
        "shadow the encrypted copy on the next launch")


def test_an_immovable_legacy_file_still_lets_the_app_start(
        dersis_home, tmp_path, monkeypatch):
    """``migrate_legacy_files()`` runs before any window exists, so it must return.

    Both callers — ``scheduler_gui.main()`` and
    ``SchedulerApp.__init__`` — call it unguarded, so anything it raises becomes
    a startup crash box with no window behind it. Every failure path was already
    contained except one: the ``_backup_original`` in the
    destination-already-exists branch sat *outside* the migrator's ``try``.
    Measured with an ``msvcrt.locking`` byte-lock held on a legacy
    ``learned_weights.json``: ``PermissionError [WinError 33]`` straight out of
    the function. ``shutil.move`` is patched here instead of taking a real lock,
    because the point is the containment, not the OS's error code.

    Swallowing is only correct because nothing is discarded: the failed move
    leaves the legacy file exactly where it was (ST-DATA-001), and the next
    launch retries. Both halves are asserted.
    """
    from scheduler_app.storage import storage

    storage.save_encrypted({"weights": {}}, storage.learned_weights_path())
    # ``dersis_home`` only rebinds the Dersis root; ``_OLD_DATA_DIR`` is a
    # module global under the sandboxed HOME and is therefore shared by every
    # test in the session. Anything left in it leaks into the next test's
    # migration run, so give this one its own.
    old_data = tmp_path / "class_scheduler"
    old_data.mkdir()
    monkeypatch.setattr(storage, "_OLD_DATA_DIR", str(old_data))
    src = os.path.join(storage._OLD_DATA_DIR, "learned_weights.json")
    with open(src, "w", encoding="utf-8") as fh:
        json.dump({"weights": {"gap": 3}}, fh)

    # The JSONL migrator carries the identical branch; cover both in one run.
    storage.save_encrypted([], storage.feedback_log_path())
    src_log = os.path.join(storage._OLD_DATA_DIR, "feedback_log.jsonl")
    with open(src_log, "w", encoding="utf-8") as fh:
        fh.write('{"accepted": true}\n')

    def _immovable(*_a, **_kw):
        raise PermissionError(
            33, "The process cannot access the file because another process "
                "has locked a portion of the file")

    monkeypatch.setattr(storage.shutil, "move", _immovable)

    notes = storage.migrate_legacy_files()

    assert notes == [], "nothing could be migrated, so nothing may be claimed"
    assert os.path.exists(src), (
        "the legacy file is gone after a failed backup — it must be left exactly "
        "where it was so the next launch can retry (ST-DATA-001)")
    with open(src, encoding="utf-8") as fh:
        assert json.load(fh) == {"weights": {"gap": 3}}
    assert os.path.exists(src_log), (
        "the JSONL migrator's destination-exists branch discarded the legacy "
        "feedback log after a failed backup")


@pytest.mark.ui
@pytest.mark.parametrize("legacy_location", ["app_dir", "old_data_dir"])
def test_a_legacy_config_survives_a_real_first_run(tmp_path, legacy_location):
    """ST-ARCH-001 item 6 — picking a language must not cost the user their schedule.

    A failure here means an upgrading user opens DERSİS, chooses a language, and
    finds an empty timetable, with their real schedule moved into ``backups/``
    where nothing will ever read it again.

    Run once per documented legacy location. The ``old_data_dir`` case stubs
    **nothing** about path resolution — ``~/.class_scheduler`` is inside the
    sandboxed HOME, so the real ``_old_app_config_candidates()`` finds the file
    on its own.

    This drives the **real** ``scheduler_gui.main()``, because the defect is an
    *ordering* between two calls that each work fine in isolation. A version of
    this test that called ``migrate_legacy_files()`` itself and then the gate was
    written first and measured **green before the fix** — it pinned nothing.

    It runs in a subprocess for a reason that is not stylistic: ``main()``
    constructs its own ``QApplication``, Qt permits exactly one per process, and
    the session ``qapp`` fixture already holds it. Driving ``main()`` in-process
    hangs rather than raising. See ``tests/_support/first_run_probe.py`` for what
    is stubbed and why.
    """
    import subprocess

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    home = tmp_path / "home"
    app_dir = tmp_path / "install"
    (home / "Documents").mkdir(parents=True)
    app_dir.mkdir()

    env = dict(os.environ)
    env.pop("DERSIS_PROBE_FROZEN_DIR", None)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        QT_QPA_PLATFORM="offscreen",
        PYTHONIOENCODING="utf-8",
        PYTHONPATH=repo,
        # Deliberately NOT the legacy config's language: with both set to "tr"
        # an assertion on the surviving language reads the stub, not the code.
        DERSIS_PROBE_LANG="en",
    )
    if legacy_location == "app_dir":
        _write_legacy_config(str(app_dir))
        # The real user's file sits beside the entry script. A test cannot write
        # there, so the anchor is moved; test_the_legacy_config_is_looked_for_
        # beside_the_entry_script pins the unstubbed anchor separately.
        env["DERSIS_PROBE_FROZEN_DIR"] = str(app_dir)
    else:
        old_data = home / ".class_scheduler"
        old_data.mkdir()
        _write_legacy_config(str(old_data))

    proc = subprocess.run(
        [sys.executable, os.path.join(repo, "tests", "_support", "first_run_probe.py")],
        capture_output=True, text=True, timeout=180, env=env, cwd=repo,
    )
    assert proc.returncode == 0, (
        "the first-run probe did not complete: rc=%s\nstderr:\n%s"
        % (proc.returncode, proc.stderr[-4000:])
    )

    settings = json.loads(proc.stdout)
    recovered = [c["name"] for c in settings.get("state", {}).get("classes", [])]

    assert recovered == ["LEGACY-LESSON"], (
        "a real first run lost the legacy schedule; settings keys were %r. "
        "migrate_legacy_files must run BEFORE the language gate writes "
        "app_settings.egu, because _migrate_json_file refuses when the "
        "destination already exists — and side-lines the source anyway."
        % sorted(settings)
    )
    assert settings.get("last_file") == LEGACY_LAST_FILE, (
        "the language gate replaced the migrated settings container instead of "
        "extending it: last_file is %r. Every legacy key other than the ones "
        "the gate writes must still be there after the first run."
        % settings.get("last_file")
    )
    # Not a pin on the migration, and deliberately so: the gate writes
    # ``language`` from the dialog after migrate_legacy_files() has run, so the
    # migrated ``language`` ("tr" above) is always overwritten by the user's
    # pick. This assertion pins that behaviour — it reddens if someone makes the
    # gate honour a migrated language and skip the dialog, which would deny the
    # user the choice on the one launch where the app changed identity.
    assert settings.get("language") == "en", (
        "the language gate did not write the language the user picked; the "
        "migrated value must not win over the dialog (%r)" % settings.get("language")
    )


def test_startup_migrates_before_it_writes_any_settings():
    """The ordering invariant, asserted structurally so it cannot silently drift.

    The behavioural test above needs Qt. This one is pure ``ast`` over
    ``scheduler_gui.py`` (the precedent is ``test_import_layering.py``) and
    states the rule directly: in ``main()``, the legacy migration must be called
    before the language gate, because the gate creates the very file the
    migration refuses to overwrite.
    """
    import ast

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(repo, "scheduler_gui.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())

    main_fn = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "main")

    order = []
    for node in ast.walk(main_fn):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in ("migrate_legacy_files", "run_language_gate"):
                order.append((node.lineno, name))
    order.sort()
    names = [n for _, n in order]

    assert "migrate_legacy_files" in names, (
        "scheduler_gui.main() does not migrate legacy files at all. It was "
        "reachable only from SchedulerApp.__init__, which runs after the "
        "language gate — see this module's docstring for what that costs."
    )
    assert names.index("migrate_legacy_files") < names.index("run_language_gate"), (
        "main() calls the language gate before the legacy migration: %r" % names
    )


def test_the_gate_writing_first_is_what_destroys_it(dersis_home, frozen_install):
    """The counterfactual, so the test above cannot pass for the wrong reason.

    Ordering is the whole mechanism. If this ever stops reproducing the loss,
    the guard above has become vacuous and the reason must be re-measured.
    """
    from scheduler_app.storage import storage
    from scheduler_app.ui import first_run
    from scheduler_app.single_instance import default_lock_path

    _write_legacy_config(str(frozen_install))

    default_lock_path()
    first_run._write_flag(storage.settings_path(), "language_chosen", True)  # gate first
    notes = storage.migrate_legacy_files()

    settings = storage.load_encrypted(storage.settings_path())
    assert settings.get("state", {}).get("classes", []) == [], (
        "the pre-fix ordering no longer loses the schedule — good news, but this "
        "test is now vacuous and the fix's rationale needs re-measuring"
    )
    assert notes == [], "a migration that cannot run must not claim it migrated"


def test_the_legacy_folder_is_carried_over_by_the_lock(tmp_path, monkeypatch):
    """ST-DATA-012's side effect, pinned so nobody undoes it by moving the lock.

    ``default_lock_path`` calls ``ensure_dirs``, which is what copies a
    ``~/Documents/ClassScheduler`` tree to ``~/Documents/Dersis``. Moving the
    lock acquisition back after the language gate would silently re-break the
    half of item 6 that ST-DATA-012 fixed.

    This test deliberately does **not** use ``dersis_home``: that fixture
    *creates* the root, and ``ensure_dirs`` only carries the legacy tree over
    when the root does **not** yet exist. Written against ``dersis_home`` the
    assertion can never see the copy happen.
    """
    from scheduler_app.storage import storage

    home = os.path.expanduser("~")
    legacy_saves = os.path.join(home, "Documents", "ClassScheduler", "saves")
    os.makedirs(legacy_saves, exist_ok=True)
    with open(os.path.join(legacy_saves, "timetable.uva"), "wb") as f:
        f.write(b"UVA1-legacy-payload")

    fresh_root = str(tmp_path / "Documents" / "Dersis")  # deliberately absent
    monkeypatch.setattr(storage, "_ROOT_DIR", fresh_root)
    storage._cached_key = None
    try:
        from scheduler_app.single_instance import default_lock_path

        default_lock_path()

        carried = os.path.join(fresh_root, storage.SAVES_DIR, "timetable.uva")
        assert os.path.exists(carried), (
            "the legacy ~/Documents/ClassScheduler tree was not carried over by "
            "the lock's ensure_dirs() call; an upgrading user's saves are stranded"
        )
        with open(carried, "rb") as f:
            assert f.read() == b"UVA1-legacy-payload"
    finally:
        storage._cached_key = None

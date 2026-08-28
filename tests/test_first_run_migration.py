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
``backups/scheduler_config.json``, with nothing in the UI saying so, and their
saved language is lost with it.

Measured on a simulated frozen install before the fix::

    classes_recovered  []            language 'en'   notes []
    after the fix:     ['LEGACY-LESSON']  language 'tr'

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


def _write_legacy_config(app_dir, *, language="tr", class_name="LEGACY-LESSON"):
    """Write the pre-Dersis ``scheduler_config.json`` beside the "executable"."""
    cfg = {
        "language": language,
        "last_file": "",
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

    A real user's legacy config sits next to ``Dersis.exe``. ``_old_app_config_path``
    branches on ``sys.frozen``, so without this the test would look for the file
    beside ``storage.py`` in the checkout and silently measure nothing.
    """
    import sys

    app_dir = tmp_path / "install"
    app_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app_dir / "Dersis.exe"))
    return app_dir


@pytest.mark.ui
def test_a_legacy_config_survives_a_real_first_run(tmp_path):
    """ST-ARCH-001 item 6 — picking a language must not cost the user their schedule.

    A failure here means an upgrading user opens DERSİS, chooses a language, and
    finds an empty timetable, with their real schedule moved into ``backups/``
    where nothing will ever read it again.

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
    _write_legacy_config(str(app_dir))

    env = dict(os.environ)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        QT_QPA_PLATFORM="offscreen",
        PYTHONIOENCODING="utf-8",
        PYTHONPATH=repo,
        DERSIS_PROBE_LANG="tr",
        # _old_app_config_path() branches on sys.frozen and reads the directory
        # of sys.executable, which is where a real user's legacy config lives.
        DERSIS_PROBE_FROZEN_DIR=str(app_dir),
    )

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
    assert settings.get("language") == "tr", (
        "the user's saved language was lost in the migration"
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

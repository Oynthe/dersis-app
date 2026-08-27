"""Sanity checks for the test harness itself.

If these fail, nothing else in the suite can be trusted: they prove that HOME
is sandboxed (so no test can write to the developer's real ~/Documents/Dersis)
and that the core package imports.
"""
import os

import pytest


def test_home_is_sandboxed():
    home = os.path.expanduser("~")
    assert "dersis_pytest_" in home, (
        f"HOME is {home!r} — the conftest sandbox did not take effect; "
        "tests would write to the real user profile")


def test_storage_root_is_inside_sandbox():
    from scheduler_app.storage import storage
    assert "dersis_pytest_" in storage.root_dir()


def test_dersis_home_fixture_rebinds_storage(dersis_home):
    from scheduler_app.storage import storage
    assert storage.root_dir() == str(dersis_home)
    assert os.path.isdir(os.path.join(storage.root_dir(), "saves"))


@pytest.mark.parametrize("module", [
    "scheduler_app.core.models",
    "scheduler_app.core.workflow",
    "scheduler_app.core.schedule_optimizer",
    "scheduler_app.storage",
    "scheduler_app.data_io.importer",
    "scheduler_app.data_io.exporter",
    "scheduler_app.data_io.template",
    "scheduler_app.plans",
])
def test_core_module_imports(module):
    __import__(module)


def test_dataset_generator_is_deterministic(make_preset):
    a = make_preset("tiny", seed=7)
    b = make_preset("tiny", seed=7)
    assert [c["name"] for c in a["classes"]] == [c["name"] for c in b["classes"]]
    assert len(a["classes"]) == 5

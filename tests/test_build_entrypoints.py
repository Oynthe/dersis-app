"""Regression contracts for the Windows source and production entrypoints.

These tests deliberately inspect the batch files instead of executing a full
Windows build.  The real builds are exercised manually; this suite protects
the interpreter and staging decisions that made those builds reliable.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8").lower()


def test_windows_entrypoints_anchor_their_working_directory() -> None:
    for name in ("run.bat", "setup.bat", "build_embed.bat", "build_nuitka.bat"):
        assert 'cd /d "%~dp0"' in _read(name), (
            f"{name} must resolve repository-relative paths from its own directory"
        )


def test_nuitka_uses_an_isolated_standard_venv() -> None:
    text = _read("build_nuitka.bat")

    assert "set \"nuitka_env=.tools\\nuitka-venv\"" in text
    assert '"!hostpy!" -m venv "%nuitka_env%"' in text
    assert 'set "buildpy=%nuitka_env%\\scripts\\python.exe"' in text
    assert '"%buildpy%" -m pip install -r requirements-lock.txt' in text

    # The audit/development environments may seed the dedicated venv, but the
    # pinned shipping dependencies must never be installed into either one.
    assert 'set "buildpy=.venv-audit\\scripts\\python.exe"' not in text
    assert 'set "buildpy=.venv\\scripts\\python.exe"' not in text


def test_nuitka_promotes_only_a_successful_staged_build() -> None:
    text = _read("build_nuitka.bat")

    build = text.index('"%buildpy%" -m nuitka')
    failure_gate = text.index("if errorlevel 1 (", build)
    source = text.index("set src_dist=build\\nuitka\\scheduler_gui.dist", failure_gate)
    remove_old = text.index('if exist "%dist_dir%" rmdir /s /q "%dist_dir%"', source)
    promote = text.index('move /y "%src_dist%" "%dist_dir%"', remove_old)

    assert "set \"nuitka_cache_dir=%cd%\\.tools\\nuitka-cache\"" in text
    assert "--output-dir=build\\nuitka" in text
    assert build < failure_gate < source < remove_old < promote

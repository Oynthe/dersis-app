"""Single source of truth for the DERSİS application version.

In development (running from source), this reads the VERSION file at the
repository root.  In packaged builds (build_embed.bat / Nuitka), the
VERSION file is copied into the dist directory by the build script.

Build-time generation:
    The build script may overwrite this file with a static version string
    for fully compiled (Nuitka) builds where the VERSION file is absent:

        echo __version__ = "1.2.0" > scheduler_app/_version.py

    For embeddable-Python builds the VERSION file approach works as-is.
"""

import os as _os

_FALLBACK = "0.0.0"


def _read_version() -> str:
    """Read version from the VERSION file with multi-location fallback."""
    # Candidate directories where VERSION might live:
    #   1. Two levels up from this file (repo root / dist root)
    #   2. One level up (scheduler_app/ parent)
    _this_dir = _os.path.dirname(_os.path.abspath(__file__))
    _candidates = [
        _os.path.join(_this_dir, "..", "VERSION"),       # repo root or dist root
        _os.path.join(_this_dir, "VERSION"),             # inside package (fallback)
    ]

    # Also check the working directory (handles edge cases in frozen builds)
    _cwd_version = _os.path.join(_os.getcwd(), "VERSION")
    if _cwd_version not in [_os.path.abspath(c) for c in _candidates]:
        _candidates.append(_cwd_version)

    for candidate in _candidates:
        try:
            path = _os.path.abspath(candidate)
            if _os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    v = f.read().strip()
                    if v:
                        return v
        except OSError:
            continue

    return _FALLBACK


__version__ = _read_version()

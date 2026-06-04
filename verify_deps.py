#!/usr/bin/env python3
"""Verify all runtime dependencies are importable before Nuitka build.

Exit code 0 = all OK, exit code 1 = something missing.

This script checks both direct and transitive dependencies because Nuitka
needs every package discoverable at compile time, even packages that pip
installs automatically as transitive dependencies.
"""
import sys

REQUIRED = [
    # ── Direct deps (core — always imported) ────────────────────────────
    ("PyQt6", "PyQt6"),
    ("cryptography", "cryptography"),

    # ── Direct deps (conditional imports but should be present) ─────────
    ("packaging", "packaging"),
    ("openpyxl", "openpyxl"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("reportlab", "reportlab"),
    ("deepdiff", "deepdiff"),
    ("ortools", "ortools"),

    # ── Transitive deps (auto-installed by pip, but Nuitka must find) ───
    ("et_xmlfile", "et_xmlfile"),        # openpyxl
    ("dateutil", "python-dateutil"),     # pandas
    ("six", "six"),                      # python-dateutil
    ("orderly_set", "orderly-set"),      # deepdiff
    ("google.protobuf", "protobuf"),     # ortools
    ("absl", "absl-py"),                 # ortools
    ("immutabledict", "immutabledict"),  # ortools
]


def main():
    fails = []
    for import_name, label in REQUIRED:
        try:
            __import__(import_name)
            print(f"  [OK]      {label}")
        except ImportError:
            print(f"  [MISSING] {label}")
            fails.append(label)

    print()
    if fails:
        print(f"ERROR: {len(fails)} package(s) missing: {', '.join(fails)}")
        print("Run:  pip install -r requirements.txt")
        return 1
    else:
        print(f"All {len(REQUIRED)} packages verified OK.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

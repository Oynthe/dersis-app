# 00 — READ ME FIRST

## What this folder is

`dersis-mapped/` is a stand-alone documentation map of the **DERSİS** repository at the state captured on **2026-06-04**. It was produced by inspecting every source, configuration, and asset file in the repo so that a future Claude instance (or human developer) can understand the project quickly and accurately, without prior context.

The original application code has **not** been modified by this folder. Every artefact in this folder is documentation.

> **Offline conversion note:** DERSİS has been converted into a **fully offline desktop application**. There is no login, license check, account page, version/update check, or any network call. The app launches directly into the main window, every feature is unlocked locally, and bug/crash reports are composed locally and handed to the user's email client via a `mailto:` link (nothing is transmitted by the app).

## What DERSİS is (one-paragraph summary)

DERSİS (Turkish: *Ders Programı Hazırlama Sistemi* — "Class Schedule Preparation System") is a **PyQt6 desktop application** for **automated weekly class timetabling** at universities and schools. It combines a heuristic placement engine, Large Neighborhood Search (LNS) optimization, and Google OR-Tools CP-SAT constraint solving to build conflict-free schedules, with an interactive drag-and-drop UI, explainable AI, multi-language UI (22 languages), and encrypted local persistence (`.egu` files, AES-256-GCM). It runs **fully offline** — no login, license server, or network connection is required.

## How a new Claude instance should use this folder

1. **Start here** (`00_READ_ME_FIRST.md`) for orientation.
2. Read **`02_PROJECT_OVERVIEW.md`** for what the app actually does.
3. Read **`03_ARCHITECTURE_MAP.md`** for the layer cake (UI / core / data_io / storage / learning).
4. Then dip into whichever topic-specific map matches the task:
   - UI work → `06_UI_MAP.md`
   - Scheduling algorithm work → `07_SCHEDULING_AND_OPTIMIZATION_MAP.md`
   - Import/export work → `08_IMPORT_EXPORT_AND_REPORTING_MAP.md`
   - Persistence/encryption → `09_SETTINGS_LOCALIZATION_AND_PERSISTENCE_MAP.md`
   - Build/release → `10_BUILD_PACKAGING_RELEASE_MAP.md`
5. Use **`14_SYMBOL_INDEX.md`** as a jump table when you know the symbol name you are looking for.
6. Use **`15_COVERAGE_MATRIX.md`** to verify whether a specific file already has a dedicated map.
7. Use **`file-maps/`** for the per-file deep dive once you know which file to read.

## Recommended reading order (top 3 to start)

| # | File | Why |
|---|------|-----|
| 1 | `02_PROJECT_OVERVIEW.md` | Domain, purpose, technology stack |
| 2 | `03_ARCHITECTURE_MAP.md` | How the layers fit together |
| 3 | `13_NEXT_INSTANCE_ONBOARDING.md` | What to read next depending on the task |

## Important ground rules (also see `12_RISKS_TECH_DEBT_AND_UNKNOWN.md`)

- The application is large and tightly integrated; **do not refactor casually**.
- `scheduler_app/ui/translations.py` is 21,790 lines of multilingual UI strings — touch with extreme care.
- The `.egu` file format and the key file under `~/Documents/Dersis/keys/` are critical for data recoverability — never delete or break compatibility.
- The `master` branch is production; this map lives on `claude/fervent-cannon-YCFOj`.

## Date of analysis

**2026-06-04** — based exclusively on the repository state at that time. Anything documented here may have drifted since.

## Source of truth statement

This map is the result of reading every file under `/home/user/dersis/` (excluding `.git/`, `__pycache__/`, virtual envs, build outputs, and the `archive_repo_cleanup/` legacy folder). The mapping cross-references the existing in-repo docs under `docs/` (notably `STRUCTURE.md`, `FEATURES.md`, `APP_OVERVIEW.md`, `CONTEXT.md`) but does not blindly trust them — each claim was verified against the actual source code where feasible.

If you find this map disagrees with the code, **the code wins**. Please update the map.

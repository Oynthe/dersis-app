# 04 — Functional Stress Test

Part of the [DERSİS stress-test audit](00-README.md). Import/export/UI-workflow
functions, attacked with normal, boundary, invalid, duplicate, and adversarial
inputs. Findings are `ST-FUNC-*` and some `ST-UI-*`
([register](12-findings-register.md)). The scheduling engine has its own document
([05](05-scheduling-engine-stress-test.md)); state integrity is
[07](07-data-state-reliability.md); malformed/edge inputs are
[08](08-error-edge-case-audit.md).

Probe scripts: [`tests/dataio/`](tests/) (import/export/storage), plus the UI
handlers driven in [`tests/`](tests/). Evidence files listed inline.

---

## 1. Excel import — the flagship workflow is broken end-to-end

**ST-FUNC-001 (Critical) — every successful import crashes.**
`SchedulerApp._import_from_excel` merges the imported classes into `state_data`
(`app.py:4512-4523`) and then calls `self._on_state_changed()` and
`self.refresh()` (`app.py:4525-4526`) — **neither method exists** anywhere in the
class's 8-class MRO (the real methods are `refresh_grid()` / `_update_status()`).

```
Test ID       ST-04-IMPORT-01
Steps         Import any valid .xlsx (incl. the app's own template)
Expected      Classes appear, grid refreshes, "import successful"
Actual        AttributeError at app.py:4525, EVERY time — after state is mutated
Evidence      tests/dataio/probe_01_import_ui_flow_static.py (hasattr → False×2)
Severity      Critical      Verdict  FAIL
```

Because the merge happens *before* the crash and the global excepthook keeps the
app alive, the user is left with half-imported data, an unrefreshed screen, and a
crash dialog — an inconsistent state that the next action will autosave.

**ST-FUNC-002 (Critical) — blank joint-group cells silently merge/delete classes.**
`importer.py:297` does `jcg = str(row.get('joint_class_group','')).strip()`;
pandas reads blank cells as `NaN` and `str(NaN) == 'nan'` (truthy), so every
class with a blank joint group shares the key `'nan'` and is merged into one
joint session, the duplicates deleted.

```
Test ID       ST-04-IMPORT-02
Data          The app's OWN generated template (5 example classes)
Expected      Re-import → 4 classes (C001-3 independent, C004-5 grouped as J1)
Actual        2 classes: C001/C002/C003 merged via 'nan'; is_valid=True, no warning
Evidence      tests/dataio/probe_06_template_roundtrip.py  (5 in → 2 out)
Severity      Critical      Verdict  FAIL
```

**ST-FUNC-003 (High) — malformed numeric cell aborts the whole import.**
Blank/text `duration` or `student_count` → uncaught `ValueError` escaping
`load_scheduler_data_from_excel`; the caller has no `try/except`.

```
Cases (probe_02): blank duration → "cannot convert float NaN to integer";
                  duration='two' / student_count='many' → "invalid literal for int()"
Result: 3/3 crash the import instead of a per-row validation error.
```

**Other import defects (probe_02, probe_06):**
- `required_room_type` is advertised in the template and import schema but **never
  read** by the importer — silently ignored ([ST-FUNC-009](12-findings-register.md)).
- A row whose class ID contains a **space** is silently dropped (1 expected → 0
  imported) ([ST-FUNC-010](12-findings-register.md)).
- A workbook with **zero recognized sheets** reports `is_valid=True` with an empty
  result — "success" on garbage ([ST-FUNC-011](12-findings-register.md)).

Positive: the schema layer correctly maps translated sheet/column names across
languages, and Turkish characters in *cell values* import fine — the failures are
in the numeric/joint-group/whitespace handling, not encoding.

## 2. Export — three formats, three different problems

**ST-FUNC-004 (High) — PDF cannot render Turkish letters.** The reportlab export
registers no Unicode font; the emitted PDF embeds only `Helvetica`,
`Helvetica-Bold`, `ZapfDingbats` (0 `/FontFile`). Helvetica `stringWidth` for
ş/ğ/İ/ı are all identical (7.61, the substitute-glyph width) vs 5.56 for `a`.

```
Test ID   ST-04-PDF-01     Evidence  tests/dataio/probe_05_export_pdf_xlsx_csv.py
Result    Every Turkish-specific letter renders as a box in the printed timetable
          — for a Turkish-first product, in the exact artifact teachers print.
Verdict   FAIL (Severity High)
```

**ST-FUNC-005 (High) — xlsx export crashes on legal names.** A lecturer/room/
branch name containing any of `/ \ : ? * [ ]` (all legal in those fields) crashes
the export with openpyxl's sheet-title `ValueError` (2/2 such names crashed;
`probe_05_export_crashes.py`).

**ST-FUNC-006 / ST-UI-008 (Medium) — CSV export.** Writes with the OS-locale
codepage (no `encoding=` on the `open()`), so on a non-Turkish Windows locale
(cp1252) Turkish text raises `UnicodeEncodeError`; the day column leaks the raw
internal key `monday` instead of `Pazartesi`; and cells beginning with `=` are
written verbatim (spreadsheet formula/DDE injection) — evidence
[`evidence/adv.csv`](evidence/adv.csv).

**Export vs UI consistency.** There are **two export engines**
([ST-ARCH-003](12-findings-register.md)): the UI uses `app.py._write_excel`
(Excel/CSV) and `exporter.export_schedule` only for PDF; `data_io/exporter.py`'s
Excel/CSV paths are dead code that has drifted (no `mode` parameter, `T_`/`R_`
unlocalized sheet names). So "what you see" and the library's own exporter can
differ.

## 3. Duplicate & repeated actions

- **Duplicate entities** ([ST-FUNC-012](12-findings-register.md)): duplicate
  `class_code`, classroom names, and lecturer names are all accepted with **no
  dedup and no warning** (3/3 accepted). Two rooms named "R1" become
  indistinguishable in occupancy.
- **Repeated export**: exporting the same schedule repeatedly is stable (no
  accumulation), but overwrites the same default filename silently.
- **Repeated import**: each import *appends* (via the broken merge path), so
  re-importing the same file doubles the classes — no idempotency or "replace vs
  merge" choice.

## 4. Small interactions

- **Ctrl+C on the Dashboard tab → `IndexError`** ([ST-FUNC-008](12-findings-register.md)):
  the clipboard handler maps tabs 0–3 only; tab 4 crashes (`app.py:3801`).
- **Open-Slots rows** show a pointing-hand cursor and hover highlight but have no
  click handler — a false affordance ([ST-UI-017](12-findings-register.md)).
- **Language switch** works and flips RTL, but `tr()` silently swallows missing
  keys/format errors, so gaps surface as raw keys in the UI
  (`labels.targets` shipped visible in all 22 languages —
  [ST-UI-011](12-findings-register.md)).

## 5. What works

Not everything is broken — several functions passed their stress:

- **Storage roundtrip** of large/complex states is semantically lossless
  (250-class state: DeepDiff = 0 differences) — see
  [07](07-data-state-reliability.md).
- **Template generation** produces a valid, correctly-localized workbook (the
  round-trip loss is on the *import* side, not generation).
- **Empty-state** operations (reschedule, all three exports, analytics on a fresh
  `new_state()`) do **not** divide-by-zero — guards hold
  ([08](08-error-edge-case-audit.md)).
- **Save/Open** of `.egu` files works; nonces/salts are unique per save.

---

Cross-references: the import/export architecture duplication is
[ST-ARCH-003](12-findings-register.md) in [10](10-code-architecture-audit.md);
the render-side of import damage (invisible conflicts) is
[ST-UI-001](12-findings-register.md#st-ui-001) in [09](09-ui-ux-audit.md);
storage/key failure modes are in [07](07-data-state-reliability.md).

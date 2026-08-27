# Evidence artifacts

Raw outputs produced by the [audit probes](../tests/README.md). Referenced
throughout the [audit documents](../00-README.md). Regenerate any of these by
re-running the corresponding probe with the audit venv (see
[methodology](../03-test-methodology.md)).

## Measurements

- `scheduler_benchmark.csv` — full scaling/density sweep of the production solver
  (wall time, placed/unplaced, greedy iters, memory) → [05](../05-scheduling-engine-stress-test.md), [06](../06-performance-audit.md).
- `oracle_tiny_small_normal.json`, `oracle_large.json` — independent hard-constraint
  oracle results over the committed schedules → [05](../05-scheduling-engine-stress-test.md).
- `arch_complexity_top50.csv` — radon cyclomatic complexity, worst 50 functions →
  [10](../10-code-architecture-audit.md).
- `diagnose_large.txt` — traced committed violations on the large preset.

## UI screenshots (headless `widget.grab()`)

- `screen-0..4-*.png`, `screen-main-loaded.png` — the 5 main tabs with a loaded schedule.
- `smoke-main-window*.png` — empty-state launch (offscreen vs native font rendering).
- `ux-empty-state.png`, `ux-tab*-*.png`, `ux-large-*.png`, `ux-narrow-1000x700*.png`
  — information-architecture and responsiveness captures.
- `ux-dlg-*.png` — the 14 dialogs (setup, add/bulk/edit class, place, results,
  reschedule/goals, crash/bug, upgrade, lecturer constraints).
- `ux-stress-*.png` — adversarial visuals: `conflict-r001`/`conflict-group`
  (silently-hidden conflicts, [ST-UI-001](../12-findings-register.md#st-ui-001)),
  `longname-200char` (grid deformation), `unplaced-25`, `warninglog-expanded`.
- `dashboard-room-switching-zero.png` — the zeroed room metric ([ST-UI-003](../12-findings-register.md#st-ui-003)).

## Export & injection samples

- `export_turkish.csv`, `benign_*.pdf`, `malformed_*.pdf`, `orphan_*.{csv,xlsx,pdf}`,
  `empty_*.{csv,xlsx,pdf}`, `dup_*.xlsx` — export behavior on edge/adversarial data.
- `adv.csv`, `adv.xlsx` — CSV formula-injection sample ([ST-UI-008](../12-findings-register.md)).
- `warning_injection_doc.html` — captured `QTextEdit` HTML showing markup injection
  ([ST-UI-007](../12-findings-register.md)).

## Persistence

- `roundtrip_big.egu`, `roundtrip_tricky.egu` — encrypted-container roundtrip fixtures.

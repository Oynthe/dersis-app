# Stress-test probe index

Reproducible probe scripts for the [DERSİS audit](../00-README.md). Every script
is standalone and sandboxes its own `HOME`/`USERPROFILE` before importing
`scheduler_app`, so **none touches real user data**. Run from the repo root with
the audit venv:

```bash
.venv-audit/Scripts/python.exe stress-test/tests/<script>.py
```

Shared helpers: [`_fixtures/dataset_gen.py`](_fixtures/dataset_gen.py) (deterministic
datasets — `make_state`, `make_preset`), `_fixtures/sandbox.py` / `_sandbox.py` /
`_eh_sandbox.py` / `_ui_boot.py` (sandbox + Qt bootstrap). Files prefixed `_` are
helpers/calibration, not standalone findings.

## Key deliverables

| Script | Produces | Findings |
|---|---|---|
| [`schedule_oracle.py`](schedule_oracle.py) | Independent hard-constraint oracle over the production path → `evidence/oracle_*.json` | [ST-SCHED-001](../12-findings-register.md#st-sched-001), [ST-SCHED-002](../12-findings-register.md#st-sched-002) |
| [`scheduler_benchmark.py`](scheduler_benchmark.py) | Scaling/density sweep → `evidence/scheduler_benchmark.csv` | [ST-PERF-001](../12-findings-register.md#st-perf-001), [ST-SCHED-012/013](../12-findings-register.md) |
| [`capture_screens.py`](capture_screens.py) | Loaded-schedule screenshots of all 5 tabs | [09](../09-ui-ux-audit.md) |
| [`smoke_offscreen_launch.py`](smoke_offscreen_launch.py) / [`smoke_native_screenshot.py`](smoke_native_screenshot.py) | Headless launch timing + render technique | methodology |

## By subsystem

**Scheduler correctness** — `schedule_oracle.py`, `verify_optimizer_conflicts.py`,
`pinned_infeasible_probe.py`, `diagnose_applied_violations.py`,
`ghost_day_and_stale_time_probe.py`, `legacy_solver_probe.py`,
`validator_integrity_probe.py`, `probe_malformed_classes.py`.
→ [05](../05-scheduling-engine-stress-test.md).

**Optimizer quality** — `probe_optimizer_determinism.py`,
`probe_cpsat_protection_semantics.py`, `probe_cpsat_midblock_availability.py`,
`probe_goals_weights_clobber.py`, `probe_move_conflicts_dead.py`,
`probe_greedy_recursion_depth.py`, `probe_neighbor_impact_dead.py`,
`probe_parallel_objective_divergence.py`, `probe_optimizer_scaling.py`.
→ [05](../05-scheduling-engine-stress-test.md), [06](../06-performance-audit.md).

**Data / storage / import-export** — `dataio/probe_01_import_ui_flow_static.py`,
`dataio/probe_02_import_edge_cases.py`, `dataio/probe_03_storage_key_and_formats.py`,
`dataio/probe_04_feedback_log_perf_and_corruption.py`,
`dataio/probe_05_export_pdf_xlsx_csv.py`, `dataio/probe_06_template_roundtrip.py`.
→ [04](../04-functional-stress-test.md), [07](../07-data-state-reliability.md).

**UI behavior / state** — `probe_excel_import_and_clipboard_crash.py`,
`probe_undo_and_drag_integrity.py`, `probe_autosave_and_refresh_perf.py`,
`probe_setup_dialog_reconcile.py`, `probe_screenshots_evidence.py`,
`probe_perf_diag.py`. → [06](../06-performance-audit.md), [09](../09-ui-ux-audit.md).

**Error / edge / adversarial** — `probe_empty_and_boundary.py`,
`probe_deleted_resources.py`, `probe_malformed_classes.py`,
`probe_recovery_rollback.py`, `probe_persistence_dupes_unicode.py`,
`probe_warning_html_injection.py`, `probe_pdf_benign_chars.py`.
→ [08](../08-error-edge-case-audit.md).

**Analytics / explanation** (in [`../scenarios/`](../scenarios/)) —
`probe_dashboard_room_key_bug.py`, `probe_dashboard_widget_refresh.py`,
`probe_analytics_degenerate.py`, `probe_impact_analyzer.py`,
`probe_impact_fallback_path.py`, `probe_scorer_serialization_consistency.py`,
`probe_parallel_worker_vs_sequential.py`, `probe_explanation_and_unreachable.py`.
→ [08](../08-error-edge-case-audit.md).

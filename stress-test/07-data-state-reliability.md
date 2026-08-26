# 07 — Data Integrity & State Reliability

Part of the [DERSİS stress-test audit](00-README.md). Can the application enter
contradictory states, and does it prevent / detect / repair / silently accept
them? Findings are `ST-DATA-*` (with related `ST-SEC-*`)
([register](12-findings-register.md)). Probes:
[`probe_deleted_resources.py`](tests/probe_deleted_resources.py),
[`probe_recovery_rollback.py`](tests/probe_recovery_rollback.py),
[`dataio/probe_03_storage_key_and_formats.py`](tests/dataio/probe_03_storage_key_and_formats.py),
[`probe_autosave_and_refresh_perf.py`](tests/probe_autosave_and_refresh_perf.py),
[`probe_undo_and_drag_integrity.py`](tests/probe_undo_and_drag_integrity.py)
(full list in the [test index](tests/README.md)).

**The taxonomy.** For each way of producing a bad state, the key question is what
DERSİS does about it. The summary is stark: it mostly **silently accepts** or
**crashes**, rarely **detects**, and never **repairs**.

| Bad state | DERSİS response |
|---|---|
| Corrupt settings container | **Silently accepts** → discards saved schedule ([ST-DATA-014](12-findings-register.md)) |
| Short/corrupt `key.bin` | **Silently "repairs"** by regenerating → orphans all saves ([ST-DATA-001](12-findings-register.md#st-data-001)) |
| Corrupt feedback log | **Silently accepts** → returns `[]`, next write overwrites ([ST-DATA-002](12-findings-register.md)) |
| Removed time slot w/ placements | **Crashes** 8/9 downstream ops ([ST-DATA-003](12-findings-register.md#st-data-003)) |
| Removed day/room/lecturer/year | **Silently accepts** orphan (tolerated by lookups) |
| Colliding pins | **Silently accepts** → commits double-booking ([ST-SCHED-002](12-findings-register.md#st-sched-002)) |
| Duplicate entities | **Silently accepts** ([ST-FUNC-012](12-findings-register.md)) |
| Failed mid-batch schedule | **Silently accepts** leaked half-class ([ST-DATA-011](12-findings-register.md)) |
| Two app instances | **Silently accepts** last-writer-wins ([ST-DATA-012](12-findings-register.md)) |

---

## 1. Persistence integrity

### 1.1 Roundtrip fidelity — this part is good

`save_encrypted → load_encrypted` of a 250-class density-0.5 state is
**semantically lossless**: `DeepDiff(original_normalized, loaded)` = 0 differences,
targets stay lists, no field loss. The `.egu` container correctly validates size,
magic, version, length header, and SHA-256 checksum before decrypting, and
nonces/salts are unique per save (verified). The cryptographic mechanics are
sound. → the problems are in *failure handling*, not the happy path.

One caveat ([ST-DATA-013](12-findings-register.md)): JSON coercion silently turns
non-string dict keys to strings (`42 → '42'`) and preserves `NaN`/`Infinity` as
invalid-per-spec JSON literals — latent, low impact, but a purity violation.

### 1.2 Key orphaning (ST-DATA-001, High)

`_load_or_create_key` treats any `key.bin` whose length ≠ 32 as "missing" and
**regenerates it during a load**, moving the old one to `backups/`. Reproduced:
save a state (loads fine) → truncate `key.bin` to 20 bytes → next load fails
`EguFileError` and a **new random key is silently written**, permanently orphaning
every prior `.egu`. A same-length bit-flip is worse: length stays 32, the wrong
key is kept, nothing self-heals. Because the timetable, settings, and learning
data all share this one key, a single bad sector = total, silent, unrecoverable
loss. Independently reproduced by the security pass with a 3-byte truncation.
→ [ST-DATA-001](12-findings-register.md#st-data-001), [ST-SEC-002](12-findings-register.md#st-sec-002).

### 1.3 Corrupt-container silent wipe (ST-DATA-014 / ST-DATA-005)

Both `_auto_load` and `_auto_save` rebuild from `{}` when the settings container
is unreadable — so a corrupt `app_settings.egu` (which *contains the entire
autosaved timetable* under key `"state"`) silently yields a fresh empty state, and
the next `_auto_save` overwrites the corrupt file with that empty state, making the
loss permanent. `_auto_save` additionally swallows **all** exceptions
([ST-DATA-005](12-findings-register.md)): with the settings path made read-only
mid-run, `_auto_save` returned normally, the file was unchanged, the sentinel edit
was lost, and **no error surfaced**.

### 1.4 Feedback-log destruction (ST-DATA-002)

A corrupt/truncated feedback log is swallowed to `[]` by
`load_encrypted_lines`; the next `append_encrypted_entry` then writes a
single-entry file. Reproduced: 5-entry history → 0 on corruption → 1 after the
next write. History is destroyed silently, and `entry_count()` reports 0,
misinforming the UI. → [ST-DATA-002](12-findings-register.md).

## 2. Orphaned references (deleted resources)

Placed classes retain `placed_day/time/classroom`. When the referenced axis is
removed:

- **Remove a time slot**: 8 of 9 downstream operations **crash** (analytics
  `compute_all_metrics`, CSV & XLSX export, reschedule, refresh) with
  `ValueError`/`IndexError`, because `slot_index` and slot-indexed arrays assume
  membership; only PDF survives and it **silently drops** the orphaned class.
  → [ST-DATA-003](12-findings-register.md#st-data-003).
- **Remove a day / room / lecturer / year**: **0 crashes** — those are membership
  lookups that tolerate the orphan, but the placement is now dangling (renders
  off-grid / disappears).

The normal-UI trigger is `SetupDialog._ok` ([ST-DATA-004](12-findings-register.md#st-data-004)):
it overwrites the 7 setup keys with **no reconciliation** of placed classes and
**no undo** — 4/4 removal types produced orphans against the live dialog logic. So
a routine "I removed Friday from the setup" produces the crashing state above.

## 3. Invalid placements

- **Colliding pins** commit unvalidated → real double-bookings in the committed
  schedule ([ST-SCHED-002](12-findings-register.md#st-sched-002), see
  [05 §1](05-scheduling-engine-stress-test.md#1-the-correctness-oracle--headline-result)).
- **Pin to a nonexistent slot** poisons the whole state: analytics + CSV/XLSX
  export + reschedule all crash ([ST-DATA-006](12-findings-register.md)).
- **Force-placed duration overflow** (`duration > slots`) crashes
  `analytics.busiest_slots` / `compute_all_metrics` with `IndexError`
  ([ST-DATA-007](12-findings-register.md)). (A *non-forced* over-long class is
  correctly left unplaced — the engine prevents it; only forced/pinned bypasses do.)
- **Ghost-day placements** from the solver itself
  ([ST-SCHED-003](12-findings-register.md#st-sched-003)).

## 4. Malformed class dicts

An un-normalized class dict (missing keys, e.g. loaded from a hand-edited or
older file that skipped normalization) crashes core read/optimize/export paths at
3+ sites: `get_placed_classes` → `KeyError 'placed'`, `optimize` → `KeyError
'pinned'`, `total_duration` → `KeyError 'duration'`
([ST-DATA-008](12-findings-register.md)). Normalization runs only as a side effect
of `_auto_load`/`_auto_save`, so any path that reaches the engine before an
autosave fires is exposed.

## 5. Recovery & rollback

- **Snapshot/restore works**: `snapshot_placements`/`restore_placements` and
  `rollback_schedule`/`reject_reschedule` are lossless (fingerprint-identical
  before/after) — the reschedule "accept or discard" flow correctly rolls back.
- **But** `schedule_new_classes` has no internal rollback: when the optimizer
  raises mid-batch, a half-added class leaks into state (12 → 13); only a manual
  `rollback_schedule` restores it ([ST-DATA-011](12-findings-register.md)).
- **Undo covers only `state['classes']`** ([ST-ARCH-012](12-findings-register.md)):
  setup/availability edits are irreversible, and undoing class actions across a
  setup change can restore placements referencing removed axes — feeding the §2/§3
  crash modes. Drag-from-Unplaced also pops an unrelated undo entry
  ([ST-DATA-009](12-findings-register.md)), and multi-select drag moves only the
  primary class ([ST-DATA-010](12-findings-register.md)).

## 6. Concurrency

No single-instance guard exists ([ST-DATA-012](12-findings-register.md)). Two
processes both load `app_settings.egu`, edit, and save; the last writer wins.
Demonstrated: instance-1 added a class and set `language='de'`; instance-2 (stale
snapshot) added a different class and saved last; final file lost both of
instance-1's changes. `os.replace` prevents torn files but not lost updates — a
user who simply opens DERSİS twice loses one window's work with no warning.

---

## Conclusion

The persistence *mechanics* (container format, crypto, roundtrip) are well built,
but the surrounding *failure handling* is systematically unsafe: corruption is
swallowed, keys self-destruct, orphaned references crash or dangle, and the undo
model covers only part of the state. These are the findings that answer the
brief's *"what would corrupt state or lose data?"* and drive
[roadmap Phase 0–1](14-implementation-roadmap.md). See malformed-input behavior in
[08](08-error-edge-case-audit.md), the crypto threat model in
[11](11-security-resilience-notes.md), and the state-ownership architecture in
[10](10-code-architecture-audit.md).

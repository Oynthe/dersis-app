"""Feedback log: Risk 6 (corruption -> silent history loss) and
Risk 11 (append + learn are O(N) per call -> O(N^2) to build the log).

R6 (High):  load_encrypted_lines() swallows ALL exceptions and returns [];
            append then rewrites the file, permanently destroying history
            (storage.py:411-431). Same swallow pattern hides a corrupt log
            from the user.
R11 (Med):  every feedback event calls append_encrypted_entry, which
            re-reads + re-decrypts + re-serializes + re-encrypts the WHOLE
            log; PreferenceLearner.learn() re-reads the whole log too.
"""
import os, sys, tempfile, time, struct

_sb = tempfile.mkdtemp(prefix="dersis_probe04_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")
sys.path.insert(0, r"C:\dev\dersis-app\stress-test\tests")

import scheduler_app.storage.storage as storage
from scheduler_app.learning.feedback_logger import FeedbackLogger
from scheduler_app.learning.preference_learner import PreferenceLearner

# ─────────────────────────────────────────────────────────────────────────
print("=" * 75)
print("R11: append_encrypted_entry cost as the log grows (per-append timing)")
print("=" * 75)
logger = FeedbackLogger()
log_path = logger.log_file

def sample_entry(i):
    return {
        "event": "manual_move",
        "class": {"name": f"Ders {i}", "targets": [{"year": "Y1", "branch": "A"}],
                  "duration": 2},
        "old_placement": {"day": "monday", "slot": "09:00", "room": "R1"},
        "new_placement": {"day": "tuesday", "slot": "10:00", "room": "R2"},
        "score_old": 1.0, "score_new": 0.5, "signal": "prefer_new",
    }

checkpoints = [0, 100, 250, 500, 1000, 2000]
timings = {}
N = 2000
t_start = time.perf_counter()
per_append = []
for i in range(N):
    a0 = time.perf_counter()
    storage.append_encrypted_entry(sample_entry(i), log_path)
    per_append.append((i + 1, (time.perf_counter() - a0) * 1000))
total_build = time.perf_counter() - t_start
print(f"   built {N} entries via append in {total_build:.2f}s")
print("   per-append time at various log sizes:")
for size, ms in per_append:
    if size in (1, 100, 250, 500, 1000, 1500, 2000):
        print(f"     log size {size:5d}: last append = {ms:7.2f} ms")
# ratio check for O(N)
first = per_append[99][1]
last = per_append[-1][1]
print(f"   append@100 = {first:.2f}ms vs append@2000 = {last:.2f}ms "
      f"(ratio {last/max(first,1e-9):.1f}x for 20x size => "
      f"{'LINEAR per-append (=> O(N^2) cumulative)' if last > first*3 else 'sub-linear'})")
egu_size = os.path.getsize(log_path)
print(f"   final log file size on disk: {egu_size/1024:.1f} KB for {N} entries")

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("R11: PreferenceLearner.learn() cost vs log size (full retrain each call)")
print("=" * 75)
learner = PreferenceLearner()
for size in (100, 500, 1000, 2000):
    # truncate view by rebuilding a log of exactly `size` entries
    entries = [sample_entry(i) for i in range(size)]
    storage.save_encrypted(entries, log_path)
    t0 = time.perf_counter()
    processed = learner.learn()
    dt = (time.perf_counter() - t0) * 1000
    print(f"     log={size:5d} entries -> learn() {dt:8.2f} ms "
          f"(signals processed={processed})")

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("R6: corrupt feedback log is silently swallowed and then OVERWRITTEN")
print("=" * 75)
# Build a real 5-entry log
good = [sample_entry(i) for i in range(5)]
storage.save_encrypted(good, log_path)
print("   history has", len(storage.load_encrypted_lines(log_path)), "entries")

# Corrupt it: flip a byte inside the ciphertext (checksum/AEAD will fail)
raw = bytearray(open(log_path, "rb").read())
# corrupt a byte in the middle payload region
raw[len(raw) // 2] ^= 0xFF
open(log_path, "wb").write(raw)

got = storage.load_encrypted_lines(log_path)
print(f"   load_encrypted_lines on corrupt file -> {got!r} "
      f"(len={len(got)}) -- exception SILENTLY swallowed")

# Now the app appends the next feedback event...
storage.append_encrypted_entry(sample_entry(999), log_path)
after = storage.load_encrypted_lines(log_path)
print(f"   after one append, log has {len(after)} entries "
      f"(the 5 corrupt-but-recoverable? entries are GONE)")
if len(after) == 1:
    print("   *** CONFIRMED: corrupt log silently reset; prior history destroyed on next write ***")

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("R6b: truncated / short log file")
print("=" * 75)
storage.save_encrypted([sample_entry(i) for i in range(3)], log_path)
raw = open(log_path, "rb").read()
open(log_path, "wb").write(raw[:20])  # truncate below min container size
print("   load_encrypted_lines(truncated) ->", storage.load_encrypted_lines(log_path),
      "(silently empty; entry_count would report 0)")

print("\nSandbox:", _sb)

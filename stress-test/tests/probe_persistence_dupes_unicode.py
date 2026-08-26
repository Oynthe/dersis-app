"""Probe 5+6+8: duplicates, unicode/adversarial strings, persistence roundtrip.

 5) duplicate class_code / classroom names / lecturer names -> any dedup?
 6) class names with newlines, 100k chars, HTML/<script>, path-like, formula-like
 8) save_encrypted -> load_encrypted a large/complex state: semantically identical?
    any field loss? (deepdiff)
No reschedule here, so no multiprocessing; still guarded for safety.
"""
import _eh_sandbox
_eh_sandbox.enter()

import os
import json
import copy
import traceback

from scheduler_app.translations import set_language
set_language("tr")

from scheduler_app.core.models import (
    new_state, new_class, mark_placed, normalize_state_classes,
)
from scheduler_app.core import analytics
from scheduler_app.data_io import exporter
from scheduler_app.storage import storage
from deepdiff import DeepDiff

EVID = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "evidence"))
os.makedirs(EVID, exist_ok=True)


def try_call(label, fn):
    try:
        r = fn()
        print(f"  OK       {label}")
        return ("OK", r)
    except Exception as e:
        tb = traceback.format_exc().splitlines()
        loc = ""
        for ln in reversed(tb):
            if "scheduler_app" in ln:
                loc = ln.strip()
                break
        print(f"  CRASH    {label}: {type(e).__name__}: {str(e)[:100]}")
        if loc:
            print(f"           {loc}")
        return ("CRASH", e)


def base_state():
    s = new_state()
    s["days"] = ["monday", "tuesday"]
    s["slots"] = ["09:00", "10:00", "11:00"]
    s["classrooms"] = ["R1", "R2"]
    s["classroom_capacities"] = {"R1": 30, "R2": 40}
    s["lecturers"] = ["L1", "L2"]
    s["years"] = {"Y1": ["A", "B"]}
    return s


def main():
    print("=" * 70)
    print("CASE 5a: DUPLICATE class_code (two classes same code), placed")
    print("=" * 70)
    s = base_state()
    for i in range(2):
        c = new_class(); c["class_code"] = "DUP101"; c["name"] = f"Dup{i}"
        c["lecturer"] = "L1"; c["targets"] = [{"year": "Y1", "branch": "A"}]
        c["duration"] = 1
        mark_placed(c, "monday", s["slots"][i], "R1")
        s["classes"].append(c)
    codes = [c["class_code"] for c in s["classes"]]
    print(f"  class_codes = {codes} (uids differ: "
          f"{s['classes'][0]['class_uid'] != s['classes'][1]['class_uid']})")
    try_call("analytics", lambda: analytics.compute_all_metrics(s))
    try_call("export xlsx (dup code)",
             lambda: exporter.export_schedule(s, "xlsx", os.path.join(EVID, "dup_code.xlsx")))
    print("  -> dedup? classes remain distinct by class_uid; class_code NOT unique")

    print("\n" + "=" * 70)
    print("CASE 5b: DUPLICATE classroom names")
    print("=" * 70)
    s = base_state()
    s["classrooms"] = ["R1", "R1"]           # duplicate room name
    s["classroom_capacities"] = {"R1": 30}
    c = new_class(); c["name"] = "InDupRoom"; c["lecturer"] = "L1"
    c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
    mark_placed(c, "monday", "09:00", "R1")
    s["classes"] = [c]
    print(f"  classrooms = {s['classrooms']}")
    try_call("export xlsx (dup room -> duplicate sheet name R_R1?)",
             lambda: exporter.export_schedule(s, "xlsx", os.path.join(EVID, "dup_room.xlsx")))
    from scheduler_app.core.models import get_classroom_export_labels
    labels = get_classroom_export_labels(s["classrooms"], s["classes"])
    print(f"  get_classroom_export_labels dedup -> {labels}")

    print("\n" + "=" * 70)
    print("CASE 5c: DUPLICATE lecturer names")
    print("=" * 70)
    s = base_state()
    s["lecturers"] = ["L1", "L1"]
    c = new_class(); c["name"] = "DupLect"; c["lecturer"] = "L1"
    c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
    mark_placed(c, "monday", "09:00", "R1")
    s["classes"] = [c]
    try_call("export xlsx (dup lecturer -> duplicate sheet T_L1?)",
             lambda: exporter.export_schedule(s, "xlsx", os.path.join(EVID, "dup_lect.xlsx")))

    print("\n" + "=" * 70)
    print("CASE 6: UNICODE / ADVERSARIAL class names")
    print("=" * 70)
    s = base_state()
    adversarial = {
        "newline": "Line1\nLine2\r\nLine3",
        "html": "<script>alert('x')</script><img src=x onerror=alert(1)>",
        "huge": "A" * 100000,
        "pathlike": "../../../../etc/passwd",
        "formula": "=cmd|'/c calc'!A1",           # Excel/CSV formula injection
        "unicode": "Ünïcödé 数学 \U0001F4A9 \u202e reversed",
        "sheetbad": "R:/\\*?[]name",              # illegal excel sheet chars
    }
    for i, (tag, nm) in enumerate(adversarial.items()):
        c = new_class(); c["class_code"] = nm; c["name"] = nm; c["lecturer"] = "L1"
        c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
        mark_placed(c, "monday" if i % 2 == 0 else "tuesday",
                    s["slots"][i % len(s["slots"])], "R1")
        s["classes"].append(c)
    try_call("analytics (adversarial names)", lambda: analytics.compute_all_metrics(s))
    r_csv = try_call("export csv (formula/CSV injection?)",
                     lambda: exporter.export_schedule(s, "csv", os.path.join(EVID, "adv.csv")))
    try_call("export xlsx (adversarial)",
             lambda: exporter.export_schedule(s, "xlsx", os.path.join(EVID, "adv.xlsx")))
    try_call("export pdf (adversarial: raw HTML into reportlab markup)",
             lambda: exporter.export_schedule(s, "pdf", os.path.join(EVID, "adv.pdf")))
    # Inspect CSV for un-neutralized formula injection
    csvp = os.path.join(EVID, "adv.csv")
    if os.path.exists(csvp):
        with open(csvp, "r", encoding="utf-8") as f:
            txt = f.read()
        formula_lines = [ln for ln in txt.splitlines() if ln.startswith("=") or ",=" in ln]
        print(f"  CSV formula-injection: {'PRESENT (unescaped =...)' if ('=cmd' in txt) else 'absent'};"
              f" lines starting with '=': {sum(1 for ln in txt.splitlines() if ln.startswith('='))}")

    print("\n" + "=" * 70)
    print("CASE 8: PERSISTENCE ROUNDTRIP (save_encrypted -> load_encrypted)")
    print("=" * 70)
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from _fixtures.dataset_gen import make_state
    big = make_state(n_days=6, n_slots=8, n_rooms=16, n_lecturers=40,
                     n_years=6, branches_per_year=3, n_classes=250,
                     density=0.5, seed=99)
    # place some to add placement fields + tuples-in-targets
    from scheduler_app.core.workflow import snapshot_placements
    for i, c in enumerate(big["classes"][:120]):
        mark_placed(c, big["days"][i % len(big["days"])],
                    big["slots"][i % len(big["slots"])],
                    big["classrooms"][i % len(big["classrooms"])])
    normalize_state_classes(big)
    original = copy.deepcopy(big)
    path = os.path.join(EVID, "roundtrip_big.egu")
    st = try_call("save_encrypted(big 250-class state)",
                  lambda: storage.save_encrypted(big, path))
    if st[0] == "OK":
        ld = try_call("load_encrypted", lambda: storage.load_encrypted(path))
        if ld[0] == "OK":
            loaded = ld[1]
            # load normalizes; normalize original identically for fair compare
            diff = DeepDiff(original, loaded, ignore_order=False)
            print(f"  DeepDiff(original_normalized, loaded) empty? "
                  f"{'YES (semantically identical)' if not diff else 'NO'}")
            if diff:
                # summarize diff types
                for k in diff:
                    n = len(diff[k]) if hasattr(diff[k], '__len__') else 1
                    print(f"    {k}: {n}")
                # show a couple of examples
                s_diff = json.dumps(diff, default=str)[:600]
                print(f"    sample: {s_diff}")
            # explicit tuple-vs-list check: JSON turns tuples into lists
            print(f"  original targets type: {type(original['classes'][0]['targets']).__name__}; "
                  f"loaded: {type(loaded['classes'][0]['targets']).__name__}")

    print("\n" + "=" * 70)
    print("CASE 8b: roundtrip with NON-STRING keys / tuples / NaN / set")
    print("=" * 70)
    tricky = base_state()
    tricky["classroom_capacities"] = {"R1": 30, 42: 99}   # int key
    tricky["weird_tuple"] = (1, 2, 3)
    tricky["nan_value"] = float("nan")
    tricky["inf_value"] = float("inf")
    c = new_class(); c["name"] = "K"; c["lecturer"] = "L1"
    c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
    tricky["classes"] = [c]
    p2 = os.path.join(EVID, "roundtrip_tricky.egu")
    st = try_call("save_encrypted(tricky: int key, tuple, NaN, inf)",
                  lambda: storage.save_encrypted(tricky, p2))
    if st[0] == "OK":
        ld = try_call("load_encrypted(tricky)", lambda: storage.load_encrypted(p2))
        if ld[0] == "OK":
            lt = ld[1]
            print(f"    int key 42 -> loaded keys: {list(lt['classroom_capacities'].keys())}")
            print(f"    tuple (1,2,3) -> {lt.get('weird_tuple')!r} ({type(lt.get('weird_tuple')).__name__})")
            print(f"    NaN -> {lt.get('nan_value')!r}; inf -> {lt.get('inf_value')!r}")

    print("\nDONE probe_persistence_dupes_unicode")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()

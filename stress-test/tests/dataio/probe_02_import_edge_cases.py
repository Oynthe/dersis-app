"""Importer edge cases: Risks 2, 3, 12.

R2 (Critical): blank joint_class_group cells become the string 'nan' and
               silently merge/delete unrelated classes (importer.py:297).
R3 (High):     blank / non-numeric numeric cells raise uncaught ValueError
               (importer.py:258-259).
R12:           first-row heuristic silently discards a real data row;
               zero-recognized-sheet workbook "succeeds" with empty state;
               unknown/reordered columns.
"""
import os, sys, tempfile, traceback

_sb = tempfile.mkdtemp(prefix="dersis_probe02_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")

import openpyxl
from scheduler_app.data_io.importer import load_scheduler_data_from_excel


def build_workbook(path, sheets):
    """sheets = {sheet_name: (header_list, [row_list, ...])}"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, (headers, rows) in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
        for r in rows:
            ws.append(r)
    wb.save(path)


TEACHERS_H = ["teacher_id", "name"]
ROOMS_H = ["room_id", "name", "capacity"]
BRANCHES_H = ["branch_id", "name"]
CLASSES_H = ["class_id", "class_code", "course_name", "teacher_id",
             "branch_id", "duration", "student_count", "joint_class_group"]


def base_sheets(class_rows):
    return {
        "Teachers": (TEACHERS_H, [["T1", "Ahmet"], ["T2", "Mehmet"]]),
        "Rooms": (ROOMS_H, [["R1", "Room A", 30], ["R2", "Room B", 20]]),
        "Branches": (BRANCHES_H, [["B1", "Alpha"], ["B2", "Beta"]]),
        "Classes": (CLASSES_H, class_rows),
    }


def show_report(rep):
    print("   is_valid:", rep.is_valid)
    for e in rep.errors:
        print("   ERROR:", e)
    for w in rep.warnings[:4]:
        print("   warn:", w)


# ─────────────────────────────────────────────────────────────────────────
print("=" * 75)
print("R2: THREE unrelated classes, ALL with BLANK joint_class_group")
print("=" * 75)
# Note: first data row id must be short & space-free or the heuristic drops it.
rows = [
    ["C1", "CS101", "Math", "T1", "B1", 1, 20, None],   # blank joint group
    ["C2", "CS102", "Physics", "T1", "B1", 1, 20, None],
    ["C3", "CS103", "Chem", "T2", "B2", 1, 20, None],
]
p = os.path.join(_sb, "r2.xlsx")
build_workbook(p, base_sheets(rows))
try:
    ds = load_scheduler_data_from_excel(p)
    n_in = 3
    n_out = len(ds.state["classes"])
    print(f"   classes IN = {n_in}, classes OUT = {n_out}")
    for c in ds.state["classes"]:
        print(f"     name={c['name']!r} joint_session={c.get('joint_session')} "
              f"targets={[(t['year'], t['branch']) for t in c.get('targets', [])]}")
    show_report(ds.report)
    if n_out < n_in:
        print(f"   *** CONFIRMED: {n_in - n_out} class(es) SILENTLY DELETED / merged ***")
    else:
        print("   (no merge observed)")
except Exception:
    print("   EXCEPTION:")
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("R3a: BLANK duration cell (numeric column)")
print("=" * 75)
rows = [["C1", "CS101", "Math", "T1", "B1", None, 20, "G1"]]
p = os.path.join(_sb, "r3a.xlsx")
build_workbook(p, base_sheets(rows))
try:
    ds = load_scheduler_data_from_excel(p)
    print("   no crash. classes out:", len(ds.state["classes"]))
    for c in ds.state["classes"]:
        print("     duration =", c.get("duration"), "participants =", c.get("participants"))
    show_report(ds.report)
except Exception as e:
    print(f"   *** CONFIRMED UNCAUGHT {type(e).__name__}: {e} ***")
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("R3b: NON-NUMERIC text in duration cell")
print("=" * 75)
rows = [["C1", "CS101", "Math", "T1", "B1", "two", 20, "G1"]]
p = os.path.join(_sb, "r3b.xlsx")
build_workbook(p, base_sheets(rows))
try:
    ds = load_scheduler_data_from_excel(p)
    print("   no crash. classes out:", len(ds.state["classes"]))
    show_report(ds.report)
except Exception as e:
    print(f"   *** CONFIRMED UNCAUGHT {type(e).__name__}: {e} ***")
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("R3c: NON-NUMERIC text in student_count cell")
print("=" * 75)
rows = [["C1", "CS101", "Math", "T1", "B1", 1, "many", "G1"]]
p = os.path.join(_sb, "r3c.xlsx")
build_workbook(p, base_sheets(rows))
try:
    ds = load_scheduler_data_from_excel(p)
    print("   no crash. classes out:", len(ds.state["classes"]))
    show_report(ds.report)
except Exception as e:
    print(f"   *** CONFIRMED UNCAUGHT {type(e).__name__}: {e} ***")
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("R12a: first-row heuristic — single valid class whose class_id has a space")
print("=" * 75)
rows = [["C 1", "CS101", "Math", "T1", "B1", 1, 20, "G1"]]  # id has a space
p = os.path.join(_sb, "r12a.xlsx")
build_workbook(p, base_sheets(rows))
try:
    ds = load_scheduler_data_from_excel(p)
    print("   classes out:", len(ds.state["classes"]),
          "(expected 1; 0 => real row silently discarded as 'description')")
    show_report(ds.report)
    if len(ds.state["classes"]) == 0:
        print("   *** CONFIRMED: valid row silently dropped by description heuristic ***")
except Exception:
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("R12b: workbook with ZERO recognized sheets — does it 'succeed'?")
print("=" * 75)
p = os.path.join(_sb, "r12b.xlsx")
build_workbook(p, {"RandomSheet": (["a", "b"], [[1, 2]])})
try:
    ds = load_scheduler_data_from_excel(p)
    print("   is_valid:", ds.report.is_valid,
          "| classes:", len(ds.state["classes"]),
          "| lecturers:", len(ds.state["lecturers"]))
    show_report(ds.report)
    if ds.report.is_valid and not ds.state["classes"]:
        print("   *** CONFIRMED: empty import reports is_valid=True (would wipe state) ***")
except Exception:
    traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("R12c: unknown extra column + reordered columns")
print("=" * 75)
H = ["duration", "class_id", "course_name", "teacher_id", "branch_id",
     "student_count", "surprise_col"]
rows = [[2, "C1", "Math", "T1", "B1", 20, "hello"]]
sheets = base_sheets([])
sheets["Classes"] = (H, rows)
p = os.path.join(_sb, "r12c.xlsx")
build_workbook(p, sheets)
try:
    ds = load_scheduler_data_from_excel(p)
    print("   classes out:", len(ds.state["classes"]))
    for c in ds.state["classes"]:
        print("     name=", c["name"], "duration=", c.get("duration"))
    show_report(ds.report)
except Exception:
    traceback.print_exc()

print("\nSandbox:", _sb)

"""R2 severity check: does the app's OWN generated template, re-imported,
trigger the blank-joint_class_group 'nan' merge? Also: empty-string vs
truly-blank cell behavior in pandas.

generate_excel_template() ships example classes C001-C003 with an EMPTY
joint_class_group and C004/C005 with 'J1'. If the empty cells collapse to
the 'nan' group, unrelated example classes silently merge on re-import.
"""
import os, sys, tempfile

_sb = tempfile.mkdtemp(prefix="dersis_probe06_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")

from scheduler_app.translations import set_language
set_language("tr")

from scheduler_app.data_io.template import generate_excel_template
from scheduler_app.data_io.importer import load_scheduler_data_from_excel

tpl = os.path.join(_sb, "scheduler_template.xlsx")
generate_excel_template(tpl)
print("template generated:", os.path.getsize(tpl), "bytes")

ds = load_scheduler_data_from_excel(tpl)
print("import valid:", ds.report.is_valid)
for e in ds.report.errors:
    print("  ERROR:", e)
print(f"\nClasses OUT: {len(ds.state['classes'])} "
      f"(template defines 5 example classes: C001..C005; "
      f"C004+C005 legitimately share group 'J1')")
print("Expected if correct: 4 classes (C004+C005 merge). "
      "Fewer/odd merges => blank-cell 'nan' bug.\n")
for c in ds.state["classes"]:
    print(f"  name={c['name']!r:30s} joint={c.get('joint_session')} "
          f"targets={[(t['year'], t['branch']) for t in c.get('targets', [])]}")

# Direct pandas check: how is an openpyxl empty-string cell read back?
print("\n--- pandas read of empty-string vs blank cells ---")
import openpyxl, pandas as pd
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["joint_class_group", "note"])
ws.append(["", "explicit empty string"])
ws.append([None, "explicit None/blank"])
p2 = os.path.join(_sb, "cells.xlsx"); wb.save(p2)
df = pd.read_excel(p2)
for i, row in df.iterrows():
    v = row["joint_class_group"]
    print(f"  {row['note']:24s}: value={v!r} type={type(v).__name__} "
          f"str()={str(v)!r} isna={pd.isna(v)} -> truthy_as_group={bool(str(v).strip())}")
print("\nSandbox:", _sb)

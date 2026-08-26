"""Focused check: do BENIGN special chars (&, <, >) in a course name crash the
PDF export?  _pdf_rich_paragraph injects the name straight into reportlab
paragraph markup (exporter.py:~406) without XML-escaping.
"""
import _eh_sandbox
_eh_sandbox.enter()
import os
from scheduler_app.translations import set_language
set_language("tr")
from scheduler_app.core.models import new_state, new_class, mark_placed
from scheduler_app.data_io import exporter

EVID = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "evidence"))


def base():
    s = new_state(); s["days"] = ["monday"]; s["slots"] = ["09:00", "10:00"]
    s["classrooms"] = ["R1"]; s["classroom_capacities"] = {"R1": 30}
    s["lecturers"] = ["L1"]; s["years"] = {"Y1": ["A"]}
    return s


for label, nm in [("ampersand", "R&D Seminar"), ("less_than", "Math < Physics"),
                  ("gt", "C++ > C"), ("plain", "Normal Course")]:
    s = base(); c = new_class(); c["name"] = nm; c["lecturer"] = "L1"
    c["targets"] = [{"year": "Y1", "branch": "A"}]; c["duration"] = 1
    mark_placed(c, "monday", "09:00", "R1"); s["classes"] = [c]
    try:
        exporter.export_schedule(s, "pdf", os.path.join(EVID, f"benign_{label}.pdf"))
        print(f"  {label:11} name={nm!r:20} -> PDF OK")
    except Exception as e:
        print(f"  {label:11} name={nm!r:20} -> CRASH {type(e).__name__}: {str(e)[:55]}")

print("--- tag-like tokens ---")
for label, nm in [("angle_word","Seminar <online>"), ("bracket_lab","Lab <B>"),
                  ("br_tag","Intro<br>Part2"), ("unknown_tag","<x>hi</x>"),
                  ("ampersand_semi","AT&amp;T Course")]:
    s = base(); c = new_class(); c["name"] = nm; c["lecturer"] = "L1"
    c["targets"] = [{"year":"Y1","branch":"A"}]; c["duration"]=1
    mark_placed(c,"monday","09:00","R1"); s["classes"]=[c]
    try:
        exporter.export_schedule(s,"pdf",os.path.join(EVID,f"tag_{label}.pdf"))
        print(f"  {label:14} name={nm!r:22} -> PDF OK")
    except Exception as e:
        print(f"  {label:14} name={nm!r:22} -> CRASH {type(e).__name__}: {str(e)[:45]}")

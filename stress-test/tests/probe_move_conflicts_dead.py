"""Probe: RelaxationSuggester._suggest_move_conflicts() is DEAD.

constraint_negotiator.py builds blocker tallies keyed by Python id():
    blocker_counts[id(existing)] += 1                       # :736
then looks them up in a dict keyed by cls_key() (a UUID string):
    id_to_cls = {cls_key(c): c for c in placed_classes}     # :740
    blocker = id_to_cls.get(cls_id)                          # :744 (cls_id is an int id())
    if blocker is None ...: continue                         # :745 -> always
An int id() never matches a UUID-string key, so `blocker` is always None
and every 'move_conflicting' suggestion is skipped. The feature emits
nothing even when blockers demonstrably exist.

We build a class that is unplaceable ONLY because movable placed classes
occupy every candidate slot, confirm real blockers exist, then assert
_suggest_move_conflicts returns [].
"""
import os
import sys
import tempfile

_sb = tempfile.mkdtemp(prefix="dersis_audit_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")

from scheduler_app.core.models import new_state, new_class, mark_placed, cls_key
from scheduler_app.constraint_validator import ConstraintValidator
from scheduler_app.candidate_generator import CandidateGenerator
from scheduler_app.constraint_negotiator import RelaxationSuggester


def build_state():
    st = new_state()
    st["days"] = ["monday"]
    st["slots"] = ["09:00", "10:00"]     # only 2 start slots
    st["classrooms"] = ["R001"]
    st["classroom_capacities"] = {"R001": 0}
    st["lecturers"] = ["Lect-1"]
    st["years"] = {"Year-1": ["A"]}

    # Two MOVABLE placed classes (same lecturer) occupying both slots,
    # blocking the target. They are movable (protection none) so they are
    # legitimate 'move_conflicting' candidates.
    def mk(code, slot):
        c = new_class()
        c["class_code"] = code
        c["name"] = code
        c["lecturer"] = "Lect-1"
        c["targets"] = [{"year": "Year-1", "branch": "A"}]
        c["duration"] = 1
        mark_placed(c, "monday", slot, "R001")
        return c

    b1 = mk("BLOCK1", "09:00")
    b2 = mk("BLOCK2", "10:00")

    # Target: same lecturer + same group -> conflicts with both blockers
    # at every slot -> unplaceable unless a blocker moves.
    tgt = new_class()
    tgt["class_code"] = "TARGET"
    tgt["name"] = "TARGET"
    tgt["lecturer"] = "Lect-1"
    tgt["targets"] = [{"year": "Year-1", "branch": "A"}]
    tgt["duration"] = 1

    st["classes"] = [b1, b2, tgt]
    return st, tgt


def main():
    st, tgt = build_state()
    validator = ConstraintValidator(st, exclude_ids={cls_key(tgt)})
    generator = CandidateGenerator(st, validator=validator)

    # Confirm target is genuinely unplaceable and blockers exist.
    placeable = False
    for d in st["days"]:
        for s in st["slots"]:
            if validator.check_placement(tgt, d, s, "R001"):
                placeable = True
    print(f"target placeable anywhere : {placeable} (expect False)")

    sugg = RelaxationSuggester(st, validator, generator)
    move_suggestions = sugg._suggest_move_conflicts(tgt)
    all_suggestions = sugg.suggest_for_class(tgt)
    move_in_all = [x for x in all_suggestions if x.get("type") == "move_conflicting"]

    # Prove blockers WOULD be found (replicate the tally to show it is
    # non-empty before the broken lookup discards it).
    from collections import defaultdict
    from scheduler_app.logic import (
        slot_index, total_duration, get_placed_classes, classroom_of,
        _active_targets, targets_overlap,
    )
    days, times, rooms = generator.get_search_space(tgt)
    td = total_duration(tgt)
    placed_classes = get_placed_classes(st)
    raw_id_tally = defaultdict(int)
    for day in days:
        for slot in times:
            for room in rooms:
                if validator.check_placement(tgt, day, slot, room):
                    continue
                si = slot_index(st, slot)
                slots_list = st["slots"][si:si+td]
                for existing in placed_classes:
                    if existing["pinned"] or existing.get("protection") == "locked":
                        continue
                    if existing.get("placed_day") != day:
                        continue
                    ex_si = slot_index(st, existing.get("placed_time"))
                    ex_slots = set(st["slots"][ex_si:ex_si+total_duration(existing)])
                    for s in slots_list:
                        if s in ex_slots and (
                                classroom_of(existing) == room
                                or existing["lecturer"] == tgt["lecturer"]):
                            raw_id_tally[id(existing)] += 1
                            break

    print(f"real blockers detected    : {len(raw_id_tally)} "
          f"(tallied by id(), counts={dict(raw_id_tally).values() and list(raw_id_tally.values())})")
    print(f"_suggest_move_conflicts() : {len(move_suggestions)} suggestions")
    print(f"move_conflicting in full  : {len(move_in_all)} suggestions")
    print("=" * 56)
    print(f"BLOCKERS EXIST BUT ZERO MOVE SUGGESTIONS: "
          f"{len(raw_id_tally) > 0 and len(move_suggestions) == 0}")


if __name__ == "__main__":
    main()

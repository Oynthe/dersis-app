"""CP-SAT constraint programming solver for timetable optimization.

Translates the scheduling problem into a Google OR-Tools CP-SAT model
with all hard constraints from the rule engine and soft objectives from
the scoring system. Used as an optional deep-optimization engine during
global rescheduling — the heuristic engine handles interactive placement.

Architecture:
  1. Build decision variables: each flexible class gets an assignment
     variable for day, time slot, and classroom.
  2. Hard constraints: lecturer conflicts, room conflicts, branch conflicts,
     pinned placements, allowed/excluded days/times/rooms, duration fitting,
     joint vs non-joint rules.
  3. Soft objectives: lecturer compactness, student compactness,
     fragmentation, day balance, room switching, time-of-day quality.
  4. Solve with time limit, optionally seeded with a heuristic solution.
  5. Return improved timetable or None if no improvement found.
"""

import time

try:
    from ortools.sat.python import cp_model
    HAS_ORTOOLS = True
except ImportError:
    HAS_ORTOOLS = False

from scheduler_app.logic import slot_index, total_duration, _active_targets
from scheduler_app.models import (
    cls_key,
    room_fits_class, needs_physical_room,
    get_physical_room_candidates,
    filter_class_days, filter_class_times,
    apply_lecturer_availability_filters,
)
from scheduler_app.placement_scorer import DEFAULT_WEIGHTS
from scheduler_app.timetable_scorer import TimetableScorer
from scheduler_app.translations import tr


def _cpsat_status_label(status_name):
    return tr(f"status.cpsat_status_{str(status_name).lower()}")


class CPSATScheduler:
    """CP-SAT based timetable optimizer.

    Builds a complete constraint model from the schedule state and solves
    it using Google OR-Tools CP-SAT solver. All hard constraints from the
    rule engine are encoded as CP-SAT constraints, and soft objectives
    are encoded as penalty terms in the objective function.
    """

    def __init__(self, state, weights=None, time_limit=15.0,
                 protected_ids=None, progress_callback=None):
        """
        Args:
            state: Schedule state dict.
            weights: Soft-objective weight overrides.
            time_limit: Solver time limit in seconds.
            protected_ids: Set of class ids that must not move.
            progress_callback: Optional callable(status_string) for UI.
        """
        if not HAS_ORTOOLS:
            raise RuntimeError(
                tr("errors.ortools_required"))

        self.state = state
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)
        self.time_limit = time_limit
        self.protected_ids = protected_ids or set()
        self.progress_callback = progress_callback

        self._days = state["days"]
        self._slots = state["slots"]
        self._rooms = list(state["classrooms"])
        # Virtual room index for non-physical classes (online/office).
        # Uses an index beyond all real rooms so it never conflicts.
        self._virtual_room_idx = len(self._rooms)
        self._n_days = len(self._days)
        self._n_slots = len(self._slots)
        self._n_rooms = len(self._rooms)

        # Index mappings
        self._day_idx = {d: i for i, d in enumerate(self._days)}
        self._slot_idx = {s: i for i, s in enumerate(self._slots)}
        self._room_idx = {r: i for i, r in enumerate(self._rooms)}

    def solve(self, heuristic_solution=None):
        """Build and solve the CP-SAT model.

        Args:
            heuristic_solution: Optional list of (cls, day, slot, room) from
                the heuristic optimizer to seed as initial solution hint.

        Returns:
            (placed_list, unplaced_list, solve_info) where:
            - placed_list: [(cls, day, slot, room), ...] for all placed classes
            - unplaced_list: [(cls, reason), ...] for unplaced classes
            - solve_info: dict with solver status, time, objective value
            Returns (None, None, solve_info) if no feasible solution found.
        """
        start_time = time.time()

        if self.progress_callback:
            self.progress_callback(tr("status.cpsat_building_model"))

        # Categorize classes
        pinned = [c for c in self.state["classes"] if c["pinned"]]
        locked = [c for c in self.state["classes"]
                  if not c["pinned"]
                  and c.get("protection") == "locked" and c["placed"]]
        locked_ids = {cls_key(c) for c in locked}
        protected = [c for c in self.state["classes"]
                     if not c["pinned"] and cls_key(c) not in locked_ids
                     and cls_key(c) in self.protected_ids
                     and c["placed"]]
        flexible = [c for c in self.state["classes"]
                    if not c["pinned"] and cls_key(c) not in locked_ids
                    and cls_key(c) not in self.protected_ids]

        if not flexible:
            # Nothing to optimize
            placed = []
            for c in pinned:
                room = c["pinned_classroom"] if needs_physical_room(c) else None
                placed.append((c, c["pinned_day"], c["pinned_time"], room))
            for c in locked:
                room = c["placed_classroom"] if needs_physical_room(c) else None
                placed.append((c, c["placed_day"], c["placed_time"], room))
            for c in protected:
                room = c["placed_classroom"] if needs_physical_room(c) else None
                placed.append((c, c["placed_day"], c["placed_time"], room))
            return placed, [], {"status": "TRIVIAL", "time": 0.0}

        model = cp_model.CpModel()

        # ══════════════════════════════════════════════════════════════
        #  DECISION VARIABLES
        # ══════════════════════════════════════════════════════════════

        # For each flexible class: day_var, slot_var, room_var
        # Plus a placed_var (BoolVar) to allow leaving a class unplaced
        day_vars = {}
        slot_vars = {}
        room_vars = {}
        placed_vars = {}
        # Assignment booleans: x[c, d, s, r] = 1 if class c at (d, s, r)
        # We use interval-based modeling instead for efficiency

        # Build unique index for each flexible class (class names may
        # collide so we use the list index for CP-SAT variable names).
        flex_idx = {cls_key(cls): i for i, cls in enumerate(flexible)}

        for cls in flexible:
            cid = cls_key(cls)
            fi = flex_idx[cid]

            # Compute valid domains
            valid_days = self._valid_days(cls)
            valid_start_slots = self._valid_start_slots(cls)
            valid_rooms = self._valid_rooms(cls)
            is_physical = needs_physical_room(cls)

            if not valid_days or not valid_start_slots or (is_physical and not valid_rooms):
                # Cannot be placed at all
                placed_vars[cid] = model.NewBoolVar(f"p{fi}")
                model.Add(placed_vars[cid] == 0)
                day_vars[cid] = model.NewIntVar(0, 0, f"d{fi}")
                slot_vars[cid] = model.NewIntVar(0, 0, f"s{fi}")
                room_vars[cid] = model.NewIntVar(0, 0, f"r{fi}")
                continue

            day_domain = [self._day_idx[d] for d in valid_days]
            slot_domain = [self._slot_idx[s] for s in valid_start_slots]
            if is_physical:
                room_domain = [self._room_idx[r] for r in valid_rooms]
            else:
                room_domain = [self._virtual_room_idx]

            placed_vars[cid] = model.NewBoolVar(f"p{fi}")
            day_vars[cid] = model.NewIntVarFromDomain(
                cp_model.Domain.FromValues(day_domain), f"d{fi}")
            slot_vars[cid] = model.NewIntVarFromDomain(
                cp_model.Domain.FromValues(slot_domain), f"s{fi}")
            room_vars[cid] = model.NewIntVarFromDomain(
                cp_model.Domain.FromValues(room_domain), f"r{fi}")

        # ══════════════════════════════════════════════════════════════
        #  HARD CONSTRAINTS
        # ══════════════════════════════════════════════════════════════

        # Build lists of all classes with their placement info for conflicts
        # Include pinned and protected in conflict checking
        all_placed_fixed = []
        for cls in pinned:
            r_idx = (self._room_idx.get(cls["pinned_classroom"], self._virtual_room_idx)
                     if needs_physical_room(cls)
                     else self._virtual_room_idx)
            all_placed_fixed.append({
                "cls": cls,
                "day": self._day_idx[cls["pinned_day"]],
                "slot": self._slot_idx[cls["pinned_time"]],
                "room": r_idx,
                "duration": total_duration(cls),
                "fixed": True,
            })
        for cls in locked:
            r_idx = (self._room_idx.get(cls["placed_classroom"], self._virtual_room_idx)
                     if needs_physical_room(cls)
                     else self._virtual_room_idx)
            all_placed_fixed.append({
                "cls": cls,
                "day": self._day_idx[cls["placed_day"]],
                "slot": self._slot_idx[cls["placed_time"]],
                "room": r_idx,
                "duration": total_duration(cls),
                "fixed": True,
            })
        for cls in protected:
            r_idx = (self._room_idx.get(cls["placed_classroom"], self._virtual_room_idx)
                     if needs_physical_room(cls)
                     else self._virtual_room_idx)
            all_placed_fixed.append({
                "cls": cls,
                "day": self._day_idx[cls["placed_day"]],
                "slot": self._slot_idx[cls["placed_time"]],
                "room": r_idx,
                "duration": total_duration(cls),
                "fixed": True,
            })

        # Pre-compute valid rooms per flexible class for conflict filtering
        flex_valid_rooms = {}
        for cls in flexible:
            flex_valid_rooms[cls_key(cls)] = set(
                self._room_idx[r] for r in self._valid_rooms(cls))

        # Pairwise conflict constraints between flexible classes
        for i, cls_a in enumerate(flexible):
            aid = cls_key(cls_a)
            if aid not in placed_vars:
                continue
            td_a = total_duration(cls_a)
            fi_a = flex_idx[aid]

            # Conflicts with fixed classes (pinned + protected)
            for fi_f, fixed in enumerate(all_placed_fixed):
                cls_b = fixed["cls"]
                if self._classes_can_conflict(cls_a, cls_b,
                        rooms_a=flex_valid_rooms.get(aid),
                        rooms_b={fixed["room"]}):
                    self._add_fixed_conflict(
                        model, cls_a, aid, day_vars, slot_vars, room_vars,
                        placed_vars, td_a, fixed,
                        tag=f"{fi_a}F{fi_f}")

            # Conflicts with other flexible classes
            for j in range(i + 1, len(flexible)):
                cls_b = flexible[j]
                bid = cls_key(cls_b)
                if bid not in placed_vars:
                    continue

                if self._classes_can_conflict(cls_a, cls_b,
                        rooms_a=flex_valid_rooms.get(aid),
                        rooms_b=flex_valid_rooms.get(bid)):
                    td_b = total_duration(cls_b)
                    fi_b = flex_idx[bid]
                    self._add_flexible_conflict(
                        model, cls_a, cls_b, aid, bid,
                        day_vars, slot_vars, room_vars, placed_vars,
                        td_a, td_b, tag=f"{fi_a}x{fi_b}")

        # ══════════════════════════════════════════════════════════════
        #  OBJECTIVE FUNCTION (soft constraints)
        # ══════════════════════════════════════════════════════════════

        penalties = []
        # Scale factor to convert float weights to integer coefficients
        SCALE = 100

        # ── Maximize placed classes (highest priority) ──
        for cls in flexible:
            cid = cls_key(cls)
            if cid in placed_vars:
                # Large bonus for placing a class
                penalties.append((-10000, placed_vars[cid]))

        # ── Lecturer compactness ──
        lect_classes = {}
        for cls in flexible:
            lect = cls["lecturer"]
            if lect:
                lect_classes.setdefault(lect, []).append(cls)
        for lect in pinned + protected:
            l = lect["lecturer"]
            if l:
                lect_classes.setdefault(l, [])

        lect_pair_id = 0
        for lect_name, classes_list in lect_classes.items():
            flex_in_lect = [c for c in classes_list if cls_key(c) in placed_vars]
            if len(flex_in_lect) < 2:
                continue
            w_gap = int(self.weights["lecturer_gap"] * SCALE)
            for i_l in range(len(flex_in_lect)):
                for j_l in range(i_l + 1, len(flex_in_lect)):
                    ca, cb = flex_in_lect[i_l], flex_in_lect[j_l]
                    aid_l, bid_l = cls_key(ca), cls_key(cb)
                    t = f"L{lect_pair_id}"
                    lect_pair_id += 1

                    same_day = model.NewBoolVar(f"sd{t}")
                    model.Add(
                        day_vars[aid_l] == day_vars[bid_l]
                    ).OnlyEnforceIf(same_day)
                    model.Add(
                        day_vars[aid_l] != day_vars[bid_l]
                    ).OnlyEnforceIf(same_day.Not())

                    both_placed = model.NewBoolVar(f"bp{t}")
                    model.AddBoolAnd([
                        placed_vars[aid_l], placed_vars[bid_l]
                    ]).OnlyEnforceIf(both_placed)
                    model.AddBoolOr([
                        placed_vars[aid_l].Not(), placed_vars[bid_l].Not()
                    ]).OnlyEnforceIf(both_placed.Not())

                    sdp = model.NewBoolVar(f"sdp{t}")
                    model.AddBoolAnd([same_day, both_placed]).OnlyEnforceIf(sdp)
                    model.AddBoolOr(
                        [same_day.Not(), both_placed.Not()]
                    ).OnlyEnforceIf(sdp.Not())

                    diff = model.NewIntVar(
                        -self._n_slots, self._n_slots, f"df{t}")
                    model.Add(
                        diff == slot_vars[aid_l] - slot_vars[bid_l])
                    abs_diff = model.NewIntVar(
                        0, self._n_slots, f"ad{t}")
                    model.AddAbsEquality(abs_diff, diff)

                    td_a_l = total_duration(ca)
                    td_b_l = total_duration(cb)
                    min_adj = max(td_a_l, td_b_l)
                    gap = model.NewIntVar(
                        0, self._n_slots, f"g{t}")
                    raw_gap = model.NewIntVar(
                        -self._n_slots, self._n_slots, f"rg{t}")
                    model.Add(raw_gap == abs_diff - min_adj)
                    model.AddMaxEquality(gap, [raw_gap, 0])

                    gap_active = model.NewIntVar(
                        0, self._n_slots, f"ga{t}")
                    model.Add(gap_active == gap).OnlyEnforceIf(sdp)
                    model.Add(gap_active == 0).OnlyEnforceIf(sdp.Not())
                    penalties.append((w_gap, gap_active))

        # ── Student group compactness ──
        group_classes = {}
        for cls in flexible:
            for t in cls.get("targets", []):
                gk = (t["year"], t["branch"])
                group_classes.setdefault(gk, []).append(cls)

        grp_pair_id = 0
        for gk, classes_list in group_classes.items():
            flex_in_grp = [c for c in classes_list if cls_key(c) in placed_vars]
            if len(flex_in_grp) < 2:
                continue
            w_gap_s = int(self.weights["student_gap"] * SCALE)
            for i_g in range(len(flex_in_grp)):
                for j_g in range(i_g + 1, len(flex_in_grp)):
                    ca, cb = flex_in_grp[i_g], flex_in_grp[j_g]
                    aid_g, bid_g = cls_key(ca), cls_key(cb)
                    t = f"G{grp_pair_id}"
                    grp_pair_id += 1

                    same_day_g = model.NewBoolVar(f"sd{t}")
                    model.Add(
                        day_vars[aid_g] == day_vars[bid_g]
                    ).OnlyEnforceIf(same_day_g)
                    model.Add(
                        day_vars[aid_g] != day_vars[bid_g]
                    ).OnlyEnforceIf(same_day_g.Not())

                    both_g = model.NewBoolVar(f"bp{t}")
                    model.AddBoolAnd([
                        placed_vars[aid_g], placed_vars[bid_g]
                    ]).OnlyEnforceIf(both_g)
                    model.AddBoolOr([
                        placed_vars[aid_g].Not(), placed_vars[bid_g].Not()
                    ]).OnlyEnforceIf(both_g.Not())

                    sdp_g = model.NewBoolVar(f"sdp{t}")
                    model.AddBoolAnd([same_day_g, both_g]).OnlyEnforceIf(sdp_g)
                    model.AddBoolOr(
                        [same_day_g.Not(), both_g.Not()]
                    ).OnlyEnforceIf(sdp_g.Not())

                    diff_g = model.NewIntVar(
                        -self._n_slots, self._n_slots, f"df{t}")
                    model.Add(diff_g == slot_vars[aid_g] - slot_vars[bid_g])
                    abs_diff_g = model.NewIntVar(
                        0, self._n_slots, f"ad{t}")
                    model.AddAbsEquality(abs_diff_g, diff_g)

                    td_a_g = total_duration(ca)
                    td_b_g = total_duration(cb)
                    min_adj_g = max(td_a_g, td_b_g)
                    gap_g = model.NewIntVar(
                        0, self._n_slots, f"g{t}")
                    raw_g = model.NewIntVar(
                        -self._n_slots, self._n_slots, f"rg{t}")
                    model.Add(raw_g == abs_diff_g - min_adj_g)
                    model.AddMaxEquality(gap_g, [raw_g, 0])

                    gap_act_g = model.NewIntVar(
                        0, self._n_slots, f"ga{t}")
                    model.Add(gap_act_g == gap_g).OnlyEnforceIf(sdp_g)
                    model.Add(gap_act_g == 0).OnlyEnforceIf(sdp_g.Not())
                    penalties.append((w_gap_s, gap_act_g))

        # ── End-of-day penalty ──
        w_eod = int(self.weights["end_of_day_penalty"] * SCALE)
        for cls in flexible:
            cid = cls_key(cls)
            if cid not in placed_vars:
                continue
            fi = flex_idx[cid]
            td = total_duration(cls)
            late = model.NewBoolVar(f"lt{fi}")
            model.Add(
                slot_vars[cid] + td >= self._n_slots
            ).OnlyEnforceIf(late)
            model.Add(
                slot_vars[cid] + td < self._n_slots
            ).OnlyEnforceIf(late.Not())
            la = model.NewBoolVar(f"la{fi}")
            model.AddBoolAnd([late, placed_vars[cid]]).OnlyEnforceIf(la)
            model.AddBoolOr(
                [late.Not(), placed_vars[cid].Not()]
            ).OnlyEnforceIf(la.Not())
            penalties.append((w_eod, la))

        # ── Slot position bias (minor tidiness) ──
        w_pos = max(1, int(self.weights["slot_position"] * SCALE))
        for cls in flexible:
            cid = cls_key(cls)
            if cid not in placed_vars:
                continue
            penalties.append((w_pos, slot_vars[cid]))

        # Build objective using cp_model.LinearExpr to avoid deeply
        # nested Python expression trees that can crash native code.
        if penalties:
            obj_vars = [var for _, var in penalties]
            obj_coeffs = [coeff for coeff, _ in penalties]
            model.Minimize(cp_model.LinearExpr.WeightedSum(obj_vars, obj_coeffs))

        # ══════════════════════════════════════════════════════════════
        #  HINT FROM HEURISTIC SOLUTION
        # ══════════════════════════════════════════════════════════════

        if heuristic_solution:
            hint_map = {}
            for cls, day, slot, room in heuristic_solution:
                if not cls["pinned"] and cls_key(cls) not in self.protected_ids:
                    hint_map[cls_key(cls)] = (day, slot, room)

            for cls in flexible:
                cid = cls_key(cls)
                if cid not in placed_vars:
                    continue
                hint = hint_map.get(cid)
                if hint:
                    d, s, r = hint
                    if d in self._day_idx and s in self._slot_idx:
                        if needs_physical_room(cls) and r in self._room_idx:
                            r_hint = self._room_idx[r]
                        elif not needs_physical_room(cls):
                            r_hint = self._virtual_room_idx
                        else:
                            model.AddHint(placed_vars[cid], 0)
                            continue
                        model.AddHint(placed_vars[cid], 1)
                        model.AddHint(day_vars[cid], self._day_idx[d])
                        model.AddHint(slot_vars[cid], self._slot_idx[s])
                        model.AddHint(room_vars[cid], r_hint)
                    else:
                        model.AddHint(placed_vars[cid], 0)
                else:
                    model.AddHint(placed_vars[cid], 0)

        # ══════════════════════════════════════════════════════════════
        #  SOLVE
        # ══════════════════════════════════════════════════════════════

        if self.progress_callback:
            self.progress_callback(tr("status.cpsat_solving_model"))

        # Validate the model before solving — catches construction
        # errors that would otherwise crash the native solver.
        validation_error = model.Validate()
        if validation_error:
            return None, None, {
                "status": "MODEL_INVALID",
                "status_label": _cpsat_status_label("MODEL_INVALID"),
                "time": time.time() - start_time,
                "objective": None,
                "engine": "cpsat",
                "error": validation_error,
            }

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit
        solver.parameters.num_workers = 1
        solver.parameters.log_search_progress = False

        status = solver.Solve(model)
        elapsed = time.time() - start_time

        status_name = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.MODEL_INVALID: "MODEL_INVALID",
            cp_model.UNKNOWN: "UNKNOWN",
        }.get(status, "UNKNOWN")

        solve_info = {
            "status": status_name,
            "status_label": _cpsat_status_label(status_name),
            "time": elapsed,
            "objective": solver.ObjectiveValue()
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
            "engine": "cpsat",
        }

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if self.progress_callback:
                self.progress_callback(
                    tr("status.cpsat_status_only").format(
                        status=_cpsat_status_label(status_name)
                    )
                )
            return None, None, solve_info

        # ══════════════════════════════════════════════════════════════
        #  EXTRACT SOLUTION
        # ══════════════════════════════════════════════════════════════

        placed_list = []
        unplaced_list = []

        # Add pinned
        for cls in pinned:
            room = cls["pinned_classroom"] if needs_physical_room(cls) else None
            placed_list.append((cls, cls["pinned_day"], cls["pinned_time"], room))
        # Add locked
        for cls in locked:
            room = cls["placed_classroom"] if needs_physical_room(cls) else None
            placed_list.append((cls, cls["placed_day"], cls["placed_time"], room))
        # Add protected
        for cls in protected:
            room = cls["placed_classroom"] if needs_physical_room(cls) else None
            placed_list.append((cls, cls["placed_day"], cls["placed_time"], room))

        # Extract flexible assignments
        for cls in flexible:
            cid = cls_key(cls)
            if cid not in placed_vars:
                unplaced_list.append(
                    (cls, tr("negotiation.no_valid_placements_match_constraints")))
                continue

            if solver.Value(placed_vars[cid]) == 1:
                d_idx = solver.Value(day_vars[cid])
                s_idx = solver.Value(slot_vars[cid])
                r_idx = solver.Value(room_vars[cid])
                day = self._days[d_idx]
                slot = self._slots[s_idx]
                room = (self._rooms[r_idx]
                        if r_idx < len(self._rooms) else None)
                placed_list.append((cls, day, slot, room))
            else:
                unplaced_list.append((cls, tr("negotiation.solver_could_not_place")))

        if self.progress_callback:
            self.progress_callback(
                tr("status.cpsat_status_with_placed").format(
                    status=_cpsat_status_label(status_name),
                    n=len(placed_list),
                )
            )

        return placed_list, unplaced_list, solve_info

    # ══════════════════════════════════════════════════════════════════
    #  HELPER METHODS
    # ══════════════════════════════════════════════════════════════════

    def _valid_days(self, cls):
        days = filter_class_days(cls, self._days)
        days, _ = apply_lecturer_availability_filters(
            self.state, cls.get("lecturer", ""), days, self._slots)
        return [d for d in days if d in self._day_idx]

    def _valid_start_slots(self, cls):
        times = filter_class_times(cls, self._slots)
        _, times = apply_lecturer_availability_filters(
            self.state, cls.get("lecturer", ""), self._days, times)
        td = total_duration(cls)
        return [t for t in times
                if t in self._slot_idx
                and self._slot_idx[t] + td <= self._n_slots]

    def _valid_rooms(self, cls):
        if not needs_physical_room(cls):
            return []  # handled via virtual room index
        return get_physical_room_candidates(self.state, cls, apply_capacity=True)

    def _classes_can_conflict(self, cls_a, cls_b,
                              rooms_a=None, rooms_b=None):
        """Check if two classes share any resource (lecturer, group, room).

        Only pairs that share at least one possible resource need pairwise
        constraints. Skipping unrelated pairs drastically reduces model size.
        """
        # Same lecturer
        if cls_a["lecturer"] and cls_a["lecturer"] == cls_b["lecturer"]:
            return True
        # Overlapping student groups
        for ta in cls_a.get("targets", []):
            for tb in cls_b.get("targets", []):
                if ta["year"] == tb["year"] and ta["branch"] == tb["branch"]:
                    return True
        # Overlapping possible rooms — only between two physical classes.
        # Non-physical classes never have room conflicts.
        if (needs_physical_room(cls_a) and needs_physical_room(cls_b)
                and rooms_a is not None and rooms_b is not None):
            if rooms_a & rooms_b:
                return True
            return False
        return True

    def _add_fixed_conflict(self, model, cls_a, aid, day_vars, slot_vars,
                            room_vars, placed_vars, td_a, fixed, tag=""):
        """Add constraints preventing cls_a from conflicting with a fixed class."""
        cls_b = fixed["cls"]
        fd = fixed["day"]
        fs = fixed["slot"]
        fr = fixed["room"]
        td_b = fixed["duration"]

        same_lect = (cls_a["lecturer"] and
                     cls_a["lecturer"] == cls_b["lecturer"])
        overlap_groups = self._targets_overlap(cls_a, cls_b)

        if aid not in placed_vars:
            return

        on_fixed_day = model.NewBoolVar(f"ofd{tag}")
        model.Add(day_vars[aid] == fd).OnlyEnforceIf(on_fixed_day)
        model.Add(day_vars[aid] != fd).OnlyEnforceIf(on_fixed_day.Not())

        active = model.NewBoolVar(f"fa{tag}")
        model.AddBoolAnd([on_fixed_day, placed_vars[aid]]).OnlyEnforceIf(
            active)
        model.AddBoolOr(
            [on_fixed_day.Not(), placed_vars[aid].Not()]
        ).OnlyEnforceIf(active.Not())

        before = model.NewBoolVar(f"bf{tag}")
        model.Add(slot_vars[aid] + td_a <= fs).OnlyEnforceIf(before)
        model.Add(slot_vars[aid] + td_a > fs).OnlyEnforceIf(before.Not())
        after = model.NewBoolVar(f"af{tag}")
        model.Add(slot_vars[aid] >= fs + td_b).OnlyEnforceIf(after)
        model.Add(slot_vars[aid] < fs + td_b).OnlyEnforceIf(after.Not())

        # Room conflict: same day + same room => no time overlap
        # (only between two face-to-face classes)
        if needs_physical_room(cls_a) and needs_physical_room(cls_b):
            same_room = model.NewBoolVar(f"sr{tag}")
            model.Add(room_vars[aid] == fr).OnlyEnforceIf(same_room)
            model.Add(room_vars[aid] != fr).OnlyEnforceIf(same_room.Not())

            ra = model.NewBoolVar(f"ra{tag}")
            model.AddBoolAnd([active, same_room]).OnlyEnforceIf(ra)
            model.AddBoolOr([active.Not(), same_room.Not()]).OnlyEnforceIf(
                ra.Not())
            model.AddBoolOr([before, after]).OnlyEnforceIf(ra)

        # Lecturer conflict: same day => no time overlap
        if same_lect:
            model.AddBoolOr([before, after]).OnlyEnforceIf(active)

        # Group conflict: same day => no time overlap
        if overlap_groups:
            model.AddBoolOr([before, after]).OnlyEnforceIf(active)

    def _add_flexible_conflict(self, model, cls_a, cls_b, aid, bid,
                               day_vars, slot_vars, room_vars, placed_vars,
                               td_a, td_b, tag=""):
        """Add no-overlap constraints between two flexible classes."""
        same_lect = (cls_a["lecturer"] and
                     cls_a["lecturer"] == cls_b["lecturer"])
        overlap_groups = self._targets_overlap(cls_a, cls_b)

        both_placed = model.NewBoolVar(f"bp{tag}")
        model.AddBoolAnd([placed_vars[aid], placed_vars[bid]]).OnlyEnforceIf(
            both_placed)
        model.AddBoolOr([
            placed_vars[aid].Not(), placed_vars[bid].Not()
        ]).OnlyEnforceIf(both_placed.Not())

        same_day = model.NewBoolVar(f"sd{tag}")
        model.Add(day_vars[aid] == day_vars[bid]).OnlyEnforceIf(same_day)
        model.Add(day_vars[aid] != day_vars[bid]).OnlyEnforceIf(
            same_day.Not())

        active = model.NewBoolVar(f"a{tag}")
        model.AddBoolAnd([both_placed, same_day]).OnlyEnforceIf(active)
        model.AddBoolOr(
            [both_placed.Not(), same_day.Not()]).OnlyEnforceIf(active.Not())

        before = model.NewBoolVar(f"bf{tag}")
        model.Add(slot_vars[aid] + td_a <= slot_vars[bid]).OnlyEnforceIf(
            before)
        model.Add(slot_vars[aid] + td_a > slot_vars[bid]).OnlyEnforceIf(
            before.Not())
        after = model.NewBoolVar(f"af{tag}")
        model.Add(slot_vars[bid] + td_b <= slot_vars[aid]).OnlyEnforceIf(
            after)
        model.Add(slot_vars[bid] + td_b > slot_vars[aid]).OnlyEnforceIf(
            after.Not())
        nto = model.NewBoolVar(f"nto{tag}")
        model.AddBoolOr([before, after]).OnlyEnforceIf(nto)
        model.AddBoolAnd([before.Not(), after.Not()]).OnlyEnforceIf(
            nto.Not())

        # Room conflict: same day, same room => no time overlap
        # (only between two face-to-face classes)
        if needs_physical_room(cls_a) and needs_physical_room(cls_b):
            same_room = model.NewBoolVar(f"sr{tag}")
            model.Add(room_vars[aid] == room_vars[bid]).OnlyEnforceIf(same_room)
            model.Add(room_vars[aid] != room_vars[bid]).OnlyEnforceIf(
                same_room.Not())

            rc = model.NewBoolVar(f"rc{tag}")
            model.AddBoolAnd([active, same_room]).OnlyEnforceIf(rc)
            model.AddBoolOr(
                [active.Not(), same_room.Not()]).OnlyEnforceIf(rc.Not())
            model.AddImplication(rc, nto)

        # Lecturer conflict
        if same_lect:
            model.AddImplication(active, nto)

        # Group conflict
        if overlap_groups:
            model.AddImplication(active, nto)

    def _targets_overlap(self, cls_a, cls_b):
        """Check if classes share any student group target."""
        for ta in cls_a.get("targets", []):
            for tb in cls_b.get("targets", []):
                if ta["year"] == tb["year"] and ta["branch"] == tb["branch"]:
                    return True
        return False

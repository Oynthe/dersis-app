"""Shared deterministic dataset generator for the DERSIS stress-test audit.

Builds state dicts matching scheduler_app.core.models.new_state()/new_class()
shapes at parameterized scale and constraint density. Fully deterministic via
seed. No Qt, no storage side effects.

Usage:
    import sys, os
    sys.path.insert(0, r"C:\\dev\\dersis-app")          # repo root
    sys.path.insert(0, r"C:\\dev\\dersis-app\\stress-test\\tests")
    from _fixtures.dataset_gen import make_state

    state = make_state(n_classes=120, density=0.5, seed=42)

Density 0.0 = almost unconstrained; 1.0 = heavily constrained (availability
windows, allowed/excluded days & times, room requirements, pins, capacity
pressure). Feasibility is NOT guaranteed at high density — that is the point.
"""
import random
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

DAY_POOL = ["monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday"]


def make_state(
    n_days=5,
    n_slots=8,
    n_rooms=8,
    n_lecturers=12,
    n_years=4,
    branches_per_year=2,
    n_classes=60,
    density=0.3,
    seed=42,
    online_fraction=0.1,
    max_duration=3,
    slot_start_hour=9,
):
    """Return a populated, unplaced state dict at the given scale/density."""
    from scheduler_app.core.models import new_state, new_class

    rng = random.Random(seed)
    state = new_state()
    state["days"] = DAY_POOL[:n_days]
    state["slots"] = [f"{slot_start_hour + i:02d}:00" for i in range(n_slots)]
    state["classrooms"] = [f"R{i+1:03d}" for i in range(n_rooms)]
    state["classroom_capacities"] = {
        r: rng.choice([0, 20, 30, 40, 60]) for r in state["classrooms"]
    }
    state["lecturers"] = [f"Lect-{i+1:03d}" for i in range(n_lecturers)]
    state["years"] = {
        f"Year-{y+1}": [chr(ord("A") + b) for b in range(branches_per_year)]
        for y in range(n_years)
    }

    # Lecturer availability: density controls how many lecturers are restricted
    # and how tight their windows are.
    for lect in state["lecturers"]:
        if rng.random() < density:
            avail = {
                "allowed_days": [], "allowed_hours": [],
                "excluded_days": [], "excluded_hours": [],
            }
            if rng.random() < 0.6:
                k = max(2, int(n_days * (1 - 0.6 * density)))
                avail["allowed_days"] = rng.sample(state["days"], min(k, n_days))
            if rng.random() < 0.6:
                k = max(2, int(n_slots * (1 - 0.6 * density)))
                avail["allowed_hours"] = rng.sample(state["slots"], min(k, n_slots))
            if rng.random() < 0.3:
                avail["excluded_days"] = rng.sample(state["days"], 1)
            state["lecturer_availability"][lect] = avail

    all_targets = [
        {"year": y, "branch": b}
        for y, branches in state["years"].items() for b in branches
    ]

    for i in range(n_classes):
        cls = new_class()
        cls["class_code"] = f"C{i+1:04d}"
        cls["name"] = f"Ders {i+1}"
        cls["lecturer"] = rng.choice(state["lecturers"])
        if len(all_targets) >= 2 and rng.random() <= 0.15:
            n_t = rng.randint(2, min(3, len(all_targets)))
        else:
            n_t = 1
        cls["targets"] = rng.sample(all_targets, n_t)
        cls["duration"] = rng.randint(1, max_duration)
        cls["participants"] = rng.choice([0, 15, 25, 35, 55])
        if rng.random() < online_fraction:
            cls["location_type"] = "online"

        # Constraint layers scale with density
        if rng.random() < density * 0.5:
            k = max(2, int(n_days * (1 - 0.5 * density)))
            cls["allowed_days"] = rng.sample(state["days"], min(k, n_days))
        if rng.random() < density * 0.4:
            k = max(2, int(n_slots * (1 - 0.5 * density)))
            cls["allowed_times"] = rng.sample(state["slots"], min(k, n_slots))
        if rng.random() < density * 0.3:
            cls["excluded_days"] = rng.sample(state["days"], 1)
        if rng.random() < density * 0.25 and cls["location_type"] != "online":
            cls["required_classrooms"] = rng.sample(
                state["classrooms"], max(1, int(n_rooms * 0.2)))
        if rng.random() < density * 0.15:
            cls["pinned"] = True
            cls["pinned_day"] = rng.choice(cls["allowed_days"] or state["days"])
            pool = cls["allowed_times"] or state["slots"]
            # keep the pin inside the grid for multi-hour classes
            idx_pool = [s for s in pool
                        if state["slots"].index(s) + cls["duration"] * len(cls["targets"] if not cls["joint_session"] else [1]) <= n_slots]
            cls["pinned_time"] = rng.choice(idx_pool or pool)
            if cls["location_type"] != "online":
                cls["pinned_classroom"] = rng.choice(
                    cls["required_classrooms"] or state["classrooms"])
        state["classes"].append(cls)

    return state


PRESETS = {
    "tiny":         dict(n_classes=5,    n_rooms=2,  n_lecturers=3,   n_years=1, density=0.1),
    "small":        dict(n_classes=25,   n_rooms=4,  n_lecturers=6,   n_years=2, density=0.2),
    "normal":       dict(n_classes=80,   n_rooms=8,  n_lecturers=15,  n_years=4, density=0.3),
    "large":        dict(n_classes=250,  n_rooms=16, n_lecturers=40,  n_years=6, branches_per_year=3, density=0.35),
    "very_large":   dict(n_classes=600,  n_rooms=30, n_lecturers=90,  n_years=8, branches_per_year=4, density=0.35),
    "pathological": dict(n_classes=1200, n_rooms=40, n_lecturers=150, n_years=10, branches_per_year=5, density=0.5),
}


def make_preset(name, seed=42, **overrides):
    kw = dict(PRESETS[name])
    kw.update(overrides)
    return make_state(seed=seed, **kw)


if __name__ == "__main__":
    for name in PRESETS:
        s = make_preset(name)
        print(f"{name}: classes={len(s['classes'])} rooms={len(s['classrooms'])} "
              f"lecturers={len(s['lecturers'])} slots={len(s['days'])}x{len(s['slots'])}")

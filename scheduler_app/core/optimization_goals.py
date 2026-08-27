"""User-facing optimization goal sliders mapped to internal scoring weights.

Provides a clean abstraction between the 6 user-facing slider goals
and the 14 internal PlacementScorer / TimetableScorer weights.
Each slider value (0-100) controls how much emphasis the optimizer
places on that particular goal. The mapping converts slider values
into a weight override dict that merges with DEFAULT_WEIGHTS.

This module is optional: if no goals are provided, the scheduler
uses DEFAULT_WEIGHTS unchanged (full backward compatibility).
"""

from scheduler_app.placement_scorer import DEFAULT_WEIGHTS


# ── Slider definitions ──────────────────────────────────────────────

GOAL_KEYS = [
    "lecturer_compactness",
    "student_compactness",
    "room_utilization",
    "fairness",
    "minimal_disruption",
    "early_hour_preference",
]

# Default slider values that reproduce DEFAULT_WEIGHTS exactly
DEFAULT_GOALS = {
    "lecturer_compactness":   60,
    "student_compactness":    50,
    "room_utilization":       40,
    "fairness":               35,
    "minimal_disruption":     50,
    "early_hour_preference":  30,
}


# ── Preset profiles ─────────────────────────────────────────────────

PRESETS = {
    "balanced": {
        "lecturer_compactness":   60,
        "student_compactness":    50,
        "room_utilization":       40,
        "fairness":               35,
        "minimal_disruption":     50,
        "early_hour_preference":  30,
    },
    "lecturer_priority": {
        "lecturer_compactness":   95,
        "student_compactness":    30,
        "room_utilization":       25,
        "fairness":               20,
        "minimal_disruption":     40,
        "early_hour_preference":  20,
    },
    "student_priority": {
        "lecturer_compactness":   30,
        "student_compactness":    95,
        "room_utilization":       25,
        "fairness":               40,
        "minimal_disruption":     40,
        "early_hour_preference":  25,
    },
    "minimal_change": {
        "lecturer_compactness":   30,
        "student_compactness":    30,
        "room_utilization":       20,
        "fairness":               20,
        "minimal_disruption":     95,
        "early_hour_preference":  15,
    },
    "space_efficient": {
        "lecturer_compactness":   40,
        "student_compactness":    40,
        "room_utilization":       95,
        "fairness":               30,
        "minimal_disruption":     30,
        "early_hour_preference":  20,
    },
    "morning_friendly": {
        "lecturer_compactness":   45,
        "student_compactness":    45,
        "room_utilization":       30,
        "fairness":               30,
        "minimal_disruption":     35,
        "early_hour_preference":  95,
    },
}

PRESET_LABEL_KEYS = {
    "balanced": "optimization.preset_balanced",
    "lecturer_priority": "optimization.preset_lecturer",
    "student_priority": "optimization.preset_student",
    "minimal_change": "optimization.preset_minimal_change",
    "space_efficient": "optimization.preset_space_efficient",
    "morning_friendly": "optimization.preset_morning",
}

DEFAULT_PRESET = "balanced"


# ── Weight mapping ──────────────────────────────────────────────────

# Each goal maps to internal weight keys with (base_multiplier) pairs.
# A slider at 50 reproduces approximately the DEFAULT_WEIGHTS value;
# at 0 the contribution is near-zero; at 100 it's roughly doubled.
_GOAL_WEIGHT_MAP = {
    "lecturer_compactness": {
        "lecturer_gap":         (5.0,   1.0),
        "lecturer_cluster":     (2.5,   1.0),
        "room_switch_penalty":  (0.8,   0.6),
        "fragmentation":        (1.8,   0.5),
    },
    "student_compactness": {
        "student_gap":          (2.5,   1.0),
        "student_cluster":      (1.2,   1.0),
    },
    "room_utilization": {
        "room_switch_penalty":  (0.8,   0.4),
        "fragmentation":        (1.8,   0.5),
    },
    "fairness": {
        "day_overload":         (1.5,   1.0),
        "day_spread":           (0.05,  1.0),
    },
    # ST-SCHED-015: this slider used to drive two weights, but the second fed
    # PlacementScorer._neighbor_impact, which measured 0.0 on every one of 3307
    # calls across the `small` and `normal` presets and has been removed. The
    # half left here is the half that ever did anything, so the slider behaves
    # exactly as before.
    "minimal_disruption": {
        "lookahead_penalty":    (3.0,   1.0),
    },
    "early_hour_preference": {
        "early_slot_bonus":     (0.3,   1.0),
        "midday_bonus":         (0.2,   1.0),
        "end_of_day_penalty":   (0.6,   1.0),
        "slot_position":        (0.01,  1.0),
    },
}


def goals_to_weights(goals):
    """Convert user-facing slider goals (0-100 each) to internal weight dict.

    The conversion scales each internal weight proportionally to its
    controlling slider(s). When multiple sliders affect the same weight,
    contributions are summed and capped.

    Args:
        goals: dict mapping goal key -> int slider value (0-100).
               If None, returns DEFAULT_WEIGHTS unchanged.

    Returns:
        dict: full weight dict compatible with PlacementScorer/TimetableScorer.
    """
    if goals is None:
        return dict(DEFAULT_WEIGHTS)

    # Start with zero accumulation
    accum = {k: 0.0 for k in DEFAULT_WEIGHTS}
    contrib_count = {k: 0 for k in DEFAULT_WEIGHTS}

    for goal_key, mapping in _GOAL_WEIGHT_MAP.items():
        slider = goals.get(goal_key, DEFAULT_GOALS.get(goal_key, 50))
        # Map 0-100 slider to a 0.0–2.0 multiplier (50 = 1.0x default)
        factor = slider / 50.0

        for weight_key, (base_val, influence) in mapping.items():
            accum[weight_key] += base_val * factor * influence
            contrib_count[weight_key] += 1

    # Build final weights
    result = dict(DEFAULT_WEIGHTS)
    for key in accum:
        if contrib_count[key] > 0:
            # Average contributions from multiple sliders
            result[key] = max(0.01, accum[key] / contrib_count[key])

    return result


def is_default(goals):
    """Check if the given goals match DEFAULT_GOALS (no user customization)."""
    if goals is None:
        return True
    return all(
        goals.get(k, DEFAULT_GOALS[k]) == DEFAULT_GOALS[k]
        for k in GOAL_KEYS
    )

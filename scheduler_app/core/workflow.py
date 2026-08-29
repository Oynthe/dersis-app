"""SchedulingWorkflow: UI-free business logic for class scheduling operations.

Extracts placement, scheduling, editing, rescheduling, and drop-validation
logic from the UI layer so that it can be tested and reused independently.
All methods operate on plain state dicts and return result objects — no Qt
imports, no dialog references, no widget manipulation.
"""

from __future__ import annotations

import copy
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from scheduler_app.models import (
    DEFAULT_OPTIMIZER_SEED,
    split_non_joint, needs_physical_room, room_fits_class,
    get_room_candidates,
    copy_editable_class_fields,
    mark_placed, mark_unplaced,
    cls_key,
)
# ST-ARCH-004: `logic.find_conflicts` and `logic.respects_constraints` are
# deliberately NOT imported here any more. They are the deprecated, weaker pair
# — neither checks grid membership or lecturer availability — and every
# decision in this module now goes through ConstraintValidator instead.
from scheduler_app.logic import find_slot_index, slots_fit, total_duration
# The one case-folding rule; `data_io/importer.py::_process_teachers` imports
# the same function so the two "is this the same teacher?" matchers cannot
# drift apart. It lives in the i18n leaf because that is the only package both
# `core` and `data_io` may import -- see tests/test_import_layering.py.
from scheduler_app.i18n.text_fold import fold_text
# ST-ARCH-010: the `optimized_*` entry points moved out of `logic.py` into
# `core/facade.py` so that `logic` could stop importing the engine from inside
# function bodies. This module is the only one under `core` allowed to import
# the facade -- see `tests/test_import_layering.py`. The names still land in
# this module's namespace, so the tests that monkeypatch
# `workflow.optimized_batch_schedule` are unaffected.
from scheduler_app.core.facade import (
    optimized_auto_place, optimized_batch_schedule,
    optimized_reschedule_all,
    score_placement, score_placement_explained,
    analyze_schedule, negotiate_after_optimization,
)


# ── Result dataclasses ───────────────────────────────────────────────────────


def drop_report(cls_item, reasons):
    """One placement the commit step could not accept, as a plain dict.

    ST-SCHED-001. A dropped lesson is data loss from the user's point of view,
    and "Ders 12 disappeared" is not something they can act on — "Ders 12 was
    removed because R001 is already taken on Monday at 09:00" is.
    ``apply_reschedule`` used to return bare class names, so even a UI that
    stopped throwing the list away could only report the first half.

    ``class_uid`` rides along because class names are not unique in a real
    dataset, so a name alone cannot be resolved back to a row.

    A plain dict rather than a richer type on purpose: this crosses into
    ui/app.py and into the results dialog, and a mapping needs no import and
    no isinstance dance to consume.
    """
    return {
        "name": cls_item.get("name", "?"),
        "class_uid": cls_item.get("class_uid"),
        "reasons": list(reasons or []),
        "reason": "; ".join(reasons or ()),
    }

@dataclass
class AutoPlaceResult:
    """Result of auto-placing a single class."""
    success: bool
    relocated: list = field(default_factory=list)
    placed_info: Optional[tuple] = None       # (day, slot, room) or None
    explanation: Optional[dict] = None        # placement explanation dict
    score: float = 0.0


@dataclass
class ScheduleNewResult:
    """Result of scheduling one or more new classes."""
    placed: list = field(default_factory=list)        # [(cls, day, slot, room), ...]
    unplaced: list = field(default_factory=list)       # [(cls, reason), ...]
    rescheduled: bool = False
    single_success: bool = False   # single class placed without dialog needed
    single_failed: bool = False    # single class could not be placed
    negotiation_report: Optional[dict] = None  # constraint negotiation report


@dataclass
class PlaceBatchResult:
    """Result of batch-placing multiple classes."""
    placed_count: int = 0
    unresolved_count: int = 0
    rescheduled: bool = False
    placed: list = field(default_factory=list)
    unplaced: list = field(default_factory=list)


# Distinct from None on purpose: None is a legitimate cached value, meaning
# "nothing was unplaced, so there is nothing to report".
_UNSET = object()


@dataclass
class RescheduleResult:
    """Result of a full reschedule operation.

    ST-PERF-007: ``negotiation_result`` is a lazily computed, memoised property
    rather than a field. The negotiation pass costs roughly as much as the solve
    itself (measured at ~10 s of wrapper overhead on a 25-class instance) and
    ran unconditionally whenever anything was unplaced, whether or not anyone
    ever looked at it.

    NOTE the field is *deleted*, not shadowed. Leaving the annotation in the
    dataclass body while adding the property makes the generated ``__init__``
    run ``self.negotiation_result = None``, which goes through the setter and
    permanently poisons the cache with None — every later read returns None and
    the negotiation tab silently disappears.
    """
    placed: list = field(default_factory=list)
    unplaced: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    summary: Optional[dict] = None
    analytics: Optional[dict] = None
    explanation: Optional[dict] = None
    _negotiation_factory: Optional[Callable] = field(default=None, repr=False)
    _negotiation_cache: Any = field(default=_UNSET, repr=False)

    @property
    def negotiation_result(self):
        """The negotiation report, computed on first read and then cached."""
        if self._negotiation_cache is _UNSET:
            self._negotiation_cache = (
                self._negotiation_factory() if self._negotiation_factory
                else None)
        return self._negotiation_cache

    @negotiation_result.setter
    def negotiation_result(self, value):
        # Kept so the attribute still behaves like a plain one for callers
        # that assign to it.
        self._negotiation_cache = value


@dataclass
class DropValidation:
    """Result of validating a drop at (day, slot)."""
    valid: bool = True
    reasons: list = field(default_factory=list)
    room: Optional[str] = None
    conflicts: list = field(default_factory=list)


@dataclass
class EditClassResult:
    """Result of editing a class — what state changes occurred."""
    placement_cleared: bool = False


class ReconcileReport(list):
    """What ``reconcile_placements`` repaired: the classes, AND what it took.

    A ``list`` subclass and not a dataclass, because the list *is* the existing
    contract. Four production sites and six tests read the return value, all of
    them through ``len()``, truthiness or ``any(c is cls for c in ...)``:
    ``ui/app.py`` (the Setup, context-menu-Setup and import repairs),
    ``tests/test_phase9_b4.py``, ``tests/test_setup_reconcile.py``,
    ``tests/test_form_affordances.py`` and ``tests/test_dashboard_metrics.py``.
    Every one of them keeps working unchanged; only a caller that wants the
    extra detail has to know this type exists.

    ``lost_room_requirements`` is a list of ``(cls, dropped_room_names)`` for
    the classes whose ``required_classrooms`` went from **non-empty to empty**
    — the one outcome of the sweep that is not a repair but a reversal. See
    ``reconcile_placements``' room-name comment for why those two cases cannot
    share a sentence.
    """

    def __init__(self, iterable: Any = ()) -> None:
        super().__init__(iterable)
        # Annotated, and the signature annotated with it: an annotation inside
        # an UNtyped def makes mypy print an `annotation-unchecked` note, and
        # the gate for this repo is a clean `mypy --config-file mypy.ini` run
        # (42 source files), not merely a zero exit.
        self.lost_room_requirements: list = []


# ── Snapshot helpers ─────────────────────────────────────────────────────────

def snapshot_placements(state):
    """Return {cls_key(cls): (day, time, room)} for all placed non-pinned classes."""
    return {
        cls_key(c): (c["placed_day"], c["placed_time"], c["placed_classroom"])
        for c in state["classes"]
        if c["placed"] and not c["pinned"]
    }


def restore_placements(state, snapshots):
    """Restore placements from a snapshot dict."""
    for cls in state["classes"]:
        snap = snapshots.get(cls_key(cls))
        if snap:
            mark_placed(cls, snap[0], snap[1], snap[2])
        elif cls["placed"] and not cls["pinned"]:
            # Class was placed during the operation but wasn't in the
            # original snapshot — unplace it to restore original state.
            mark_unplaced(cls)


def _same_name_other_case(first, second) -> bool:
    """True when two spellings are one name shouted, whispered or typed plainly.

    NOT a second identity rule, and never consulted as one: ``fold_text`` alone
    decides whether two names are the same teacher (ST-FUNC-012), and this is
    read only AFTER it has already said "yes". It answers the different, purely
    presentational question ``fold_text`` cannot -- *is the difference between
    these two strings worth interrupting a human over?* -- which it cannot
    answer precisely because it throws that difference away.

    Turkish casing, not Python's. ``str.casefold`` sends ``I`` to ``i`` and
    ``İ`` to ``i``+U+0307, so on this tree a bare ``casefold()`` calls four of
    these six measured pairs "different names" that any Turkish reader reads as
    one, and would have made the class form ask about all four:

        'İlgin' / 'İLGİN'              casefold: differ   here: same
        'Ilgın' / 'ILGIN'              casefold: differ   here: same
        'İlhan Demir' / 'ilhan demir'  casefold: differ   here: same
        'Işıl' / 'IŞIL'                casefold: differ   here: same
        'Ilgın' / 'İlgin'              casefold: differ   here: DIFFER (two
            people -- this is the pair the whole B3 fix exists for)
        'İlhan Demir' / 'Ilhan Demir'  casefold: differ   here: DIFFER (an
            ASCII-keyboard typing of İlhan is indistinguishable from a name
            really spelled with a dotless I, so the user is the only one who
            knows; asking is correct)

    Mapping the two Turkish capitals to their own lowercase before folding is
    the entire difference from ``str.casefold``. A Turkish school's rosters are
    routinely half shouted -- Excel's ``=UPPER()`` writes them that way -- so
    without this the prompt fires on the ordinary re-typing of a teacher who is
    already listed, and a prompt that fires on the common harmless case is a
    prompt users learn to click through before it ever reaches the harmful one.

    TWO folds, because one of them was measurably not enough. Turkish casing
    alone answers "no" to the plainest ASCII shout there is::

        'Ayse Yilmaz' / 'AYSE YILMAZ'   turkish: 'ayse yilmaz' / 'ayse yılmaz'

    -- the capital I in YILMAZ becomes a dotless ı and the lowercase i does
    not, which is *correct Turkish casing* and the wrong answer to the question
    this function asks. Measured on the shipped Phase 9 fix: that pair raised
    the prompt, i.e. exactly the harmless case the paragraph above says must
    stay quiet. Reading ``str.casefold`` as a second, weaker "yes" fixes it
    without loosening the pair the whole guard exists for -- neither fold
    merges 'Ilgın'/'İlgin' (turkish: ılgın/ilgin; casefold: ılgın/i̇lgin) or
    'İlhan Demir'/'Ilhan Demir', so all six verdicts listed above are
    unchanged. Verified pair by pair in
    ``tests/test_phase9_b3.py::test_the_form_keeps_quiet_when_it_has_nothing_to_say``.

    NFC first, because the two strings must be compared the way they RENDER.
    ``fold_text`` collapses ``i``+U+0307 onto ``i`` and this did not -- the
    ``İ``->``i`` replace runs before ``casefold`` and never sees a combining
    mark -- so a name pasted from an NFD source (macOS; this repo ships
    ``Dersis-mac.spec``) raised "is this the same teacher?" over two strings
    that are the same picture on screen. Composing first makes ``I``+U+0307
    the single codepoint U+0130 that the rest of this function already knows.
    """
    if _turkish_fold_case(first) == _turkish_fold_case(second):
        return True
    return _nfc(first).casefold() == _nfc(second).casefold()


def _nfc(text) -> str:
    """Strip, and compose to the shortest spelling of the same picture.

    ``normalize("NFC", "İlgin") == "İlgin"``. Without it every
    comparison below is between a decomposed string and a composed one, which
    is a difference no user can see and none of them meant.
    """
    return unicodedata.normalize("NFC", (text or "").strip())


def _turkish_fold_case(text) -> str:
    return _nfc(text).replace("I", "ı").replace("İ", "i").casefold()


def _resolve_roster_spelling(roster, clean):
    """Which roster entry a typed lecturer name resolves to, and whether that
    resolution is a reassignment worth interrupting a human over.

    Returns ``(spelling, is_collision)``. ``spelling`` is None only when
    nothing on the roster folds onto *clean* -- the caller then has a genuinely
    new teacher and may append. ``is_collision`` is True only for the case B3
    exists for: the roster holds a DIFFERENT human whose name happens to fold
    onto this one.

    ONE function because two were measurably one too many.
    ``register_lecturer`` (what happens) and ``find_lecturer_collision`` (what
    the user is told is about to happen) each had their own loop over the same
    roster, and the loops disagreed: both returned at the first fold match, so
    neither could see an EXACT match sitting later in the list. With
    ``['Ilgın', 'İlgin']`` on the roster -- which ``SetupDialog._ok`` builds
    without any fold check, so a school with both teachers has both -- typing
    'İlgin' was measured as::

        find_lecturer_collision -> 'Ilgın'      (docstring promised None)
        register_lecturer       -> 'Ilgın'      (a different human)

    so İlgin, a teacher who IS on the roster with their own availability
    record, could not be given a class from the form at all: Yes handed the
    lesson to Ilgın, No threw the form away. Silencing only the prompt would
    have turned that into the silent reassignment B3 was written to stop, so
    both halves read this one answer.

    The preference order is exact, then re-casing, then first-listed:

    * an EXACT match wins wherever it sits, because it is the string the user
      typed, it is on the roster, and it is the key its own
      ``lecturer_availability`` record is under;
    * failing that, a match differing only in case wins, for the same reason
      one entry earlier: 'İLGİN' is 'İlgin' shouted, and handing it to the
      'Ilgın' that happens to be listed first would be the same defect;
    * failing both, the first fold match -- ``register_lecturer``'s
      long-standing rule, unchanged for every roster that holds one spelling
      of a name, which is every roster the app itself can build.

    This is the fold site ``tests/test_text_fold.py::_SHARED_FOLD_SITES``
    names as ``core/workflow.py::register_lecturer``; the entry should follow
    the code here.
    """
    folded = fold_text(clean)
    first_match = None
    variant = None
    for known in roster:
        if fold_text((known or "").strip()) != folded:
            continue
        if known == clean:
            return known, False
        if variant is None and _same_name_other_case(known, clean):
            variant = known
        if first_match is None:
            first_match = known
    if variant is not None:
        return variant, False
    if first_match is None:
        return None, False
    return first_match, True


# ── SchedulingWorkflow ───────────────────────────────────────────────────────

class SchedulingWorkflow:
    """UI-free orchestrator for all scheduling business logic.

    Parameters
    ----------
    state : dict
        The application state dict (classes, days, slots, classrooms, …).
    get_weights : callable
        Returns the current learned scoring weights dict.
    feedback_logger : object, optional
        FeedbackLogger instance for logging placement decisions.
    preference_learner : object, optional
        PreferenceLearner instance for learning from feedback.
    """

    def __init__(self, state, get_weights, feedback_logger=None,
                 preference_learner=None):
        self.state = state
        self.get_weights = get_weights
        self._feedback = feedback_logger
        self._learner = preference_learner
        self._optimizing = False

    # ── Auto-place single class ──────────────────────────────────────────

    @property
    def is_optimizing(self):
        """True while an optimization operation is running."""
        return self._optimizing

    def auto_place(self, cls) -> AutoPlaceResult:
        """Automatically place *cls* using AI-assisted optimization.

        Returns an AutoPlaceResult with the outcome.  Does NOT mutate state
        on failure; on success the caller should apply placements.
        """
        self._optimizing = True
        try:
            return self._auto_place_impl(cls)
        finally:
            self._optimizing = False

    def _auto_place_impl(self, cls) -> AutoPlaceResult:
        existing = snapshot_placements(self.state)
        # Exclude the target class from snapshots
        existing.pop(cls_key(cls), None)

        weights = self.get_weights()
        success, placements, rescheduled = optimized_auto_place(
            self.state, cls, weights=weights)

        if not success:
            return AutoPlaceResult(success=False)

        relocated = []
        placed_info = None

        for c in self.state["classes"]:
            p = placements.get(cls_key(c))
            if p is None:
                continue
            day, slot, room = p
            if c is cls:
                effective_room = room if needs_physical_room(cls) else None
                if not cls["pinned"]:
                    mark_placed(cls, day, slot, effective_room)
                placed_info = (day, slot, effective_room)
            else:
                old = existing.get(cls_key(c))
                if old:
                    relocated.append({
                        "name": c["name"],
                        "old_day": old[0], "old_time": old[1], "old_room": old[2],
                        "new_day": day, "new_time": slot, "new_room": room,
                    })
                effective_room = room if needs_physical_room(c) else None
                mark_placed(c, day, slot, effective_room)

        # Score & explain the placement
        explanation = None
        score = 0.0
        if placed_info:
            s, breakdown, explanation = score_placement_explained(
                self.state, cls,
                placed_info[0], placed_info[1], placed_info[2],
                weights=weights)
            score = s
            if self._feedback:
                self._feedback.log_accepted_placement(
                    cls, placed_info[0], placed_info[1], placed_info[2],
                    score=s)

        return AutoPlaceResult(
            success=True,
            relocated=relocated,
            placed_info=placed_info,
            explanation=explanation,
            score=score,
        )

    # ── Schedule new classes ─────────────────────────────────────────────

    def schedule_new_classes(self, new_classes) -> ScheduleNewResult:
        """Add *new_classes* to state and run batch scheduling.

        Returns a ScheduleNewResult describing what happened.
        The caller decides whether to commit (apply placements) or rollback.
        """
        if not new_classes:
            return ScheduleNewResult()

        existing = snapshot_placements(self.state)

        # ST-DATA-011: the add is all-or-nothing. Without this, an optimizer,
        # negotiator or feedback-logger failure left a half-added class in
        # state["classes"] — sometimes already marked placed — that the user was
        # never told about and could not see a cause for.
        try:
            # Add new classes to state
            for cls in new_classes:
                self.state["classes"].append(cls)

            weights = self.get_weights()
            placed, unplaced, rescheduled = optimized_batch_schedule(
                self.state, new_classes, weights=weights)

            result = ScheduleNewResult(
                placed=placed,
                unplaced=unplaced,
                rescheduled=rescheduled,
            )

            # Fast path: all new classes placed without rescheduling existing ones
            new_ids = {cls_key(c) for c in new_classes}
            new_placed = [p for p in placed if cls_key(p[0]) in new_ids]
            if (len(new_placed) == len(new_classes) and not unplaced
                    and not rescheduled):
                for cls, day, slot, room in new_placed:
                    effective_room = room if needs_physical_room(cls) else None
                    if not cls["pinned"]:
                        mark_placed(cls, day, slot, effective_room)
                if self._feedback:
                    self._feedback.log_batch_result(
                        len(new_placed), 0, False, True)
                result.single_success = True
                return result

            # Single-class that could not be placed
            if len(new_classes) == 1 and not placed:
                cls = new_classes[0]
                from scheduler_app.constraint_negotiator import ConstraintNegotiator
                neg = ConstraintNegotiator(self.state)
                report = neg.negotiate_class(cls)
                result.single_failed = True
                result.negotiation_report = report

                # Remove the failed class from state regardless of pinned status
                if cls in self.state["classes"]:
                    self.state["classes"].remove(cls)
                return result

            return result
        except BaseException:
            # ORDER IS LOAD-BEARING, and the opposite of the public
            # rollback_schedule(): restore FIRST, while the new classes are
            # still in state["classes"], so restore_placements() can see them
            # and clear the placed=True the fast path may already have written.
            # Removing them first would leave exactly that orphan behind.
            # BaseException, not Exception: the optimizer is wall-clock bound
            # and multiprocess, so KeyboardInterrupt here is realistic.
            restore_placements(self.state, existing)
            for _cls in new_classes:
                if _cls in self.state["classes"]:
                    self.state["classes"].remove(_cls)
            raise

    def apply_schedule_result(self, result: ScheduleNewResult):
        """Commit the placements from a ScheduleNewResult."""
        for cls, day, slot, room in result.placed:
            if not cls["pinned"]:
                effective_room = room if needs_physical_room(cls) else None
                mark_placed(cls, day, slot, effective_room)
        for cls, _ in result.unplaced:
            if not cls["pinned"]:
                mark_unplaced(cls)
        if self._feedback:
            self._feedback.log_batch_result(
                len(result.placed), len(result.unplaced),
                result.rescheduled, True)

    def rollback_schedule(self, new_classes, existing_snapshots):
        """Remove new_classes from state and restore old placements."""
        for cls in new_classes:
            if cls in self.state["classes"]:
                self.state["classes"].remove(cls)
        restore_placements(self.state, existing_snapshots)

    # ── Batch placement ──────────────────────────────────────────────────

    def place_batch(self, candidates) -> PlaceBatchResult:
        """Auto-place a batch of candidate classes."""
        uniq = []
        seen = set()
        for cls in candidates:
            if cls is None or cls not in self.state["classes"]:
                continue
            cid = cls_key(cls)
            if cid in seen:
                continue
            seen.add(cid)
            uniq.append(cls)

        if not uniq:
            return PlaceBatchResult()

        weights = self.get_weights()
        placed, unplaced, rescheduled = optimized_batch_schedule(
            self.state, uniq, weights=weights)

        placed_map = {}
        for cls, day, slot, room in placed:
            placed_map[cls_key(cls)] = (day, slot, room)

        # Validate placements against live state before committing
        from scheduler_app.core.constraint_validator import ConstraintValidator
        batch_validator = ConstraintValidator(
            self.state,
            exclude_ids=set(placed_map.keys()))
        valid_days = set(self.state.get("days", []))
        valid_slots = set(self.state.get("slots", []))

        for cls in self.state["classes"]:
            p = placed_map.get(cls_key(cls))
            if p is not None:
                day, slot, room = p
                effective_room = room if needs_physical_room(cls) else None
                if (day in valid_days and slot in valid_slots
                        and batch_validator.check_placement(
                            cls, day, slot, effective_room)):
                    mark_placed(cls, day, slot, effective_room)
                    batch_validator.add_placement(cls, day, slot, effective_room)
                else:
                    # Placement invalid against live state — treat as unplaced
                    placed_map.pop(cls_key(cls))
                    if not cls.get("pinned"):
                        mark_unplaced(cls)

        unresolved_ids = set()
        for cls, _reason in unplaced:
            unresolved_ids.add(cls_key(cls))
            if not cls.get("pinned"):
                mark_unplaced(cls)

        placed_count = sum(1 for cls in uniq if cls_key(cls) in placed_map)
        unresolved_count = sum(1 for cls in uniq if cls_key(cls) in unresolved_ids)

        if self._feedback:
            self._feedback.log_batch_result(
                len(placed), len(unplaced), rescheduled, True)

        return PlaceBatchResult(
            placed_count=placed_count,
            unresolved_count=unresolved_count,
            rescheduled=rescheduled,
            placed=placed,
            unplaced=unplaced,
        )

    # ── Reschedule ───────────────────────────────────────────────────────

    def reschedule(self, weights, use_cpsat=False,
                   progress_callback=None,
                   seed=DEFAULT_OPTIMIZER_SEED,
                   cancel_token=None, **optimizer_kwargs) -> RescheduleResult:
        """Run full reschedule optimization. Returns proposed changes.

        `seed` defaults to a fixed value so the same timetable regenerates the
        same way (ST-SCHED-013); pass None to randomize deliberately. The seed
        actually used comes back as `result.summary['seed']`.
        """
        self._optimizing = True
        try:
            return self._reschedule_impl(weights, use_cpsat,
                                         progress_callback, seed,
                                         cancel_token, **optimizer_kwargs)
        finally:
            self._optimizing = False

    def _reschedule_impl(self, weights, use_cpsat, progress_callback,
                         seed=DEFAULT_OPTIMIZER_SEED, cancel_token=None,
                         **optimizer_kwargs):
        placed, unplaced, changes, summary = optimized_reschedule_all(
            self.state, weights=weights,
            progress_callback=progress_callback,
            use_cpsat=use_cpsat, seed=seed,
            cancel_token=cancel_token, **optimizer_kwargs)

        # ST-PERF-001: stop before the expensive analysis passes below. A user
        # who cancelled does not want to wait out a negotiation run for a
        # result that is about to be discarded.
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()

        # Build analytics
        from scheduler_app.explanation_engine import ExplanationEngine
        engine = ExplanationEngine()
        explanation = (engine.explain_reschedule_improvements(summary)
                       if summary else None)
        analytics = analyze_schedule(self.state, placed) if placed else None

        # ST-PERF-007: deferred, and pinned to the state as of NOW. ui/app.py
        # reads this on both sides of apply_reschedule(); analysing the live
        # state at read time would give the results dialog and the warning log
        # different answers for the same reschedule. The snapshot is also what
        # keeps the negotiator's mutate-and-restore estimators (ST-DATA-011)
        # away from the live timetable during a UI repaint.
        # Measured deepcopy cost: 0.49 ms at 25 classes, 3.06 ms at 250 —
        # against the 727 ms / 5.8 s pass it defers.
        negotiation_factory = None
        if unplaced:
            frozen_state = copy.deepcopy(self.state)
            by_uid = {cls_key(c): c for c in frozen_state["classes"]}
            frozen_unplaced = [(by_uid.get(cls_key(c), c), r)
                               for c, r in unplaced]

            # ST-SCHED-014: negotiate against the schedule the solver is
            # PROPOSING, not the one it started from.
            #
            # The snapshot above is taken before apply_reschedule, so without
            # this every class still sits where it was when the user pressed
            # Generate. The negotiator then answered "why can't this class be
            # placed?" against a timetable in which the solve had not happened:
            # it found the cells the proposal wants to use still free, called
            # every unplaced class `status='ok'` with eight valid options and no
            # suggestions, and `build_diagnostic_summary` — which recounts
            # `not placed and not pinned` off the state it is handed — reported
            # all 14 classes as unplaced when the solve had left 6. The results
            # dialog listed 8 successes and then argued with itself.
            #
            # Mirrors apply_reschedule: a pinned class is committed by leaving
            # it alone, since its position lives in the pinned_* fields.
            frozen_unplaced_ids = {cls_key(c) for c, _ in frozen_unplaced}
            for cls_item, day, slot, room in placed:
                twin = by_uid.get(cls_key(cls_item))
                if twin is None or twin["pinned"]:
                    continue
                mark_placed(twin, day, slot,
                            room if needs_physical_room(twin) else None)
            for twin_id in frozen_unplaced_ids:
                twin = by_uid.get(twin_id)
                if twin is not None and not twin["pinned"]:
                    mark_unplaced(twin)

            def negotiation_factory():
                return negotiate_after_optimization(
                    frozen_state, [], frozen_unplaced)

        return RescheduleResult(
            placed=placed,
            unplaced=unplaced,
            changes=changes,
            summary=summary,
            analytics=analytics,
            explanation=explanation,
            _negotiation_factory=negotiation_factory,
        )

    def apply_reschedule(self, result: RescheduleResult):
        """Commit reschedule placements, validating against current state.

        Screens the whole proposal through ``screen_placements`` — the same
        rule the optimizer checks itself against (ST-ARCH-004) — so state
        changes between optimization and apply are caught, and so "which
        schedules are legal" has exactly one answer in this codebase.

        Returns a list of :func:`drop_report` dicts, one per placement that
        could not be committed as proposed. Each carries ``name``,
        ``class_uid`` and a non-empty ``reason`` (ST-SCHED-001) — a bare list
        of names could only tell the user that a lesson vanished, never what
        to change to get it back.
        """
        from scheduler_app.core.constraint_validator import screen_placements
        from scheduler_app.models import PROTECTION_LOCKED

        immovable_ids = {
            cls_key(c) for c in self.state.get("classes", [])
            if not c.get("pinned")
            and c.get("protection") == PROTECTION_LOCKED and c.get("placed")
        }
        accepted, conflicts = screen_placements(
            self.state, result.placed, immovable_ids=immovable_ids)
        accepted_keys = {cls_key(c) for c, _, _, _ in accepted}

        # Every conflict is reported, never truncated: ST-SCHED-002 requires a
        # clashing pin to be named, and the invariants suite pins that. But the
        # two kinds are not the same event, so each entry says which it is.
        # `committed=True` -- a pin or locked class sitting exactly where the
        # user put it, which also clashes. Nothing failed; the user's
        # instruction stands. `committed=False` -- the commit step refused a
        # placement the optimizer proposed, i.e. the state changed underneath.
        rejected = [
            dict(drop_report(cls_item, reasons),
                 committed=cls_key(cls_item) in accepted_keys)
            for cls_item, _d, _s, _r, reasons in conflicts
        ]

        for cls_item, day, slot, room in accepted:
            if cls_item["pinned"]:
                # A pin is committed by leaving it alone: its position lives in
                # pinned_day/pinned_time, and mark_placed would duplicate it
                # into the placed_* fields.
                continue
            mark_placed(cls_item, day, slot, room)

        # Anything proposed but not accepted loses its placement. A pinned or
        # locked class is always accepted (reported, never dropped), so this
        # never unplaces one.
        for cls_item, _day, _slot, _room in result.placed:
            if cls_key(cls_item) not in accepted_keys:
                mark_unplaced(cls_item)

        for cls_item, _ in result.unplaced:
            if not cls_item["pinned"]:
                mark_unplaced(cls_item)

        if self._feedback:
            self._feedback.log_reschedule_accepted(result.changes)
        if self._learner:
            self._learner.learn()

        return rejected

    def reject_reschedule(self, snapshots, changes=None):
        """Rollback a rejected reschedule."""
        restore_placements(self.state, snapshots)
        if self._feedback:
            self._feedback.log_reschedule_rejected(changes or [])

    # ── Drop validation ──────────────────────────────────────────────────
    #
    # ST-ARCH-004. Hard-constraint validation used to exist in four divergent
    # implementations, and this — the drag-and-drop path, the one a user
    # exercises by hand all day — went through the weakest of them:
    # `logic.respects_constraints`, whose own docstring marks it deprecated
    # because it checks neither grid membership nor lecturer availability.
    # Every decision below now comes from ConstraintValidator, so the rule the
    # optimizer enforces and the rule a drag enforces cannot drift apart.
    #
    # The three-phase split (basic constraints -> pick a room -> room
    # constraints) is kept because ui/app.py reports each phase differently and
    # restructuring that dialog flow is Phase 6 work. What changed is that all
    # three phases now ask the same object.

    @staticmethod
    def _drop_validator(state, cls):
        """The authoritative validator for a drag of *cls*.

        `cls` is excluded from occupancy so that a placed class being moved
        never collides with the position it is being moved out of.
        """
        from scheduler_app.core.constraint_validator import ConstraintValidator
        return ConstraintValidator(state, exclude_ids={cls_key(cls)})

    @staticmethod
    def _availability_reasons(state, cls, day, slot):
        """Structured reasons for every hour of the block the lecturer is
        barred from. Empty when the lecturer is free for the whole block.

        The block, not just the start hour: a duration-2 class dropped at
        09:00 also occupies 10:00, and a lecturer blocked at 10:00 cannot
        teach it (ST-SCHED-005/009 are the same gap in other code paths).
        """
        from scheduler_app.models import lecturer_available_at
        lecturer = cls.get("lecturer", "")
        if not lecturer:
            return []
        si = find_slot_index(state, slot)
        if si is None:
            return []
        slots = state["slots"]
        out = []
        for off in range(total_duration(cls)):
            idx = si + off
            if idx >= len(slots):
                break
            if not lecturer_available_at(state, lecturer, day, slots[idx]):
                out.append(("lecturer_unavailable", lecturer, day, slots[idx]))
        return out

    @staticmethod
    def validate_drop(state, cls, day, slot, drag_backup=None) -> DropValidation:
        """Validate whether *cls* can be dropped at (day, slot).

        Returns a DropValidation with reasons if invalid.
        This is pure validation — no state mutation.
        """
        td = total_duration(cls)
        # ST-ARCH-013: variable-arity on purpose. Each record is
        # (key, *args), and ui/app.py dispatches on reasons[0] to pick the
        # sentence and its placeholders -- "not_enough_slots" carries three
        # extra values, "placement_invalid" none. mypy infers the narrowest
        # tuple from the first append without this, and then rejects every
        # other shape.
        reasons: list[tuple] = []

        # Same-day protection
        if cls.get("protection") == "same_day" and drag_backup:
            original_day = drag_backup.get("placed_day")
            if original_day and day != original_day:
                reasons.append(("restricted_to_day", original_day))

        # ST-DATA-003: `slots_fit` now returns False for a slot that is not on
        # the grid at all, which would otherwise expose the bare `.index(slot)`
        # below. Distinguish the two: "this hour does not exist" and "this hour
        # exists but the class does not fit after it" need different wording.
        slot_idx = find_slot_index(state, slot)
        if slot_idx is None:
            reasons.append(("slot_not_in_grid", slot))
        elif day not in state["days"]:
            reasons.append(("day_not_in_grid", day))
        elif not slots_fit(state, slot, td):
            slots_available = len(state["slots"]) - slot_idx
            reasons.append(("not_enough_slots", td, slots_available, slot))

        if cls["allowed_days"] and day not in cls["allowed_days"]:
            reasons.append(("day_not_allowed", day, cls["allowed_days"]))

        if cls.get("excluded_days") and day in cls["excluded_days"]:
            reasons.append(("day_excluded", day, cls["excluded_days"]))

        if cls["allowed_times"] and slot not in cls["allowed_times"]:
            reasons.append(("time_not_allowed", slot, cls["allowed_times"]))

        if cls.get("excluded_times") and slot in cls["excluded_times"]:
            reasons.append(("time_excluded", slot, cls["excluded_times"]))

        # ST-ARCH-004: the drag path never checked this. A lesson could be
        # dragged onto an hour its own lecturer had marked unavailable, and the
        # only thing that noticed was the room-conflict pass two phases later —
        # which reported it as a room problem.
        reasons.extend(
            SchedulingWorkflow._availability_reasons(state, cls, day, slot))

        if reasons:
            return DropValidation(valid=False, reasons=reasons)

        return DropValidation(valid=True)

    @staticmethod
    def find_drop_classroom(state, cls, day, slot, preferred_rooms=None,
                            validator=None):
        """Find the best classroom for a drop at (day, slot).

        Parameters
        ----------
        preferred_rooms : list, optional
            Ordered list of rooms to prefer (e.g. current filter, original room).

        Returns (room, conflicts) — room is None if no compatible room exists.
        """
        # ST-ARCH-004: `models.get_room_candidates` is the authority on which
        # rooms a lesson may occupy, and it answers `[None]` for online and
        # lecturer-office lessons, which need no room at all. This used to
        # filter `state["classrooms"]` by required/excluded/capacity without
        # ever asking `needs_physical_room`, with two consequences for a lesson
        # that needs no room: its (meaningless) room constraints could empty
        # the list and the drop was refused outright, and otherwise the drag
        # committed a *physical classroom* onto an online lesson — while
        # apply_reschedule stores None for the very same lesson. The same
        # lesson then showed a room or no room depending on whether the user
        # dragged it or the optimizer placed it, and exports and room-load
        # analytics disagreed with the timetable.
        rooms = list(get_room_candidates(state, cls))

        # Apply preference ordering. Meaningless for the [None] sentinel, and
        # `pref in rooms` simply never matches there.
        if preferred_rooms:
            for pref in reversed(preferred_rooms):
                if pref in rooms:
                    rooms = [pref] + [r for r in rooms if r != pref]

        # ST-ARCH-004: `check_placement` / `find_conflicts` off ONE validator,
        # rather than logic.find_conflicts -- which deliberately ignores the
        # class's own allowed/excluded days and times ("For full validation,
        # use ConstraintValidator.find_conflicts", says its own docstring) and
        # so could hand back a room for a cell the class was never allowed on.
        # Built once for the whole room scan rather than rebuilding occupancy
        # for every candidate room, which is what the old code did. Callers that
        # already hold a validator for this class pass it in: `check_drop_valid`
        # runs on every dragMoveEvent, i.e. on every mouse move during a drag,
        # so building occupancy twice per call is worth avoiding (measured
        # 0.66 ms/call at 250 classes with one build).
        if validator is None:
            validator = SchedulingWorkflow._drop_validator(state, cls)

        for room in rooms:
            if validator.check_placement(cls, day, slot, room):
                return room, []

        if rooms:
            return rooms[0], validator.find_conflicts(cls, day, slot, rooms[0])
        return None, ["no_compatible_classrooms"]

    @staticmethod
    def validate_drop_constraints(state, cls, day, slot, room) -> DropValidation:
        """Check classroom-level constraints after room selection.

        ST-ARCH-004: the verdict comes from ConstraintValidator, which -- unlike
        the deprecated ``logic.respects_constraints`` this used to call -- also
        enforces grid membership and lecturer availability across the block.
        """
        validator = SchedulingWorkflow._drop_validator(state, cls)
        if not validator.respects_constraints(cls, day, slot, room):
            reasons: list[tuple] = []   # variable-arity; see validate_drop
            if needs_physical_room(cls):
                if cls["required_classrooms"] and room not in cls["required_classrooms"]:
                    reasons.append(("classroom_not_required", room,
                                    cls["required_classrooms"]))
                if cls["excluded_classrooms"] and room in cls["excluded_classrooms"]:
                    reasons.append(("classroom_excluded", room))
                if not room_fits_class(state, room, cls):
                    from scheduler_app.models import get_room_capacity
                    cap = get_room_capacity(state, room)
                    reasons.append(("classroom_capacity", room, cap,
                                    cls.get("participants", 0)))
            reasons.extend(
                SchedulingWorkflow._availability_reasons(state, cls, day, slot))
            if not reasons:
                # The validator said no and none of the room rules explain it --
                # the class's own day/time rules, or the grid, did. Never return
                # valid=False with an empty reason list: ui/app.py renders
                # exactly these strings, so an empty list is a rejection dialog
                # with nothing in it (the ST-SCHED-009 failure mode).
                reasons.append(("placement_invalid",))
            return DropValidation(valid=False, reasons=reasons, room=room)
        return DropValidation(valid=True, room=room)

    def log_manual_move(self, cls, old_day, old_slot, old_room,
                        new_day, new_slot, new_room):
        """Log a manual drag-drop move for preference learning."""
        weights = self.get_weights()
        score_old = None
        score_new = None
        if old_day and old_slot and old_room:
            score_old = score_placement(
                self.state, cls, old_day, old_slot, old_room, weights=weights)
        score_new = score_placement(
            self.state, cls, new_day, new_slot, new_room, weights=weights)
        if self._feedback:
            self._feedback.log_manual_move(
                cls, old_day, old_slot, old_room,
                new_day, new_slot, new_room,
                score_old=score_old, score_new=score_new)
        if self._learner:
            self._learner.learn()

    # ── Edit class ───────────────────────────────────────────────────────

    @staticmethod
    def apply_class_edit(state, cls, updated) -> EditClassResult:
        """Apply edits from *updated* dict to *cls*, validating placement.

        Returns an EditClassResult describing what happened.
        """
        was_placed = cls.get("placed", False)
        old_day = cls.get("placed_day")
        old_slot = cls.get("placed_time")
        old_room = cls.get("placed_classroom")

        copy_editable_class_fields(cls, updated)

        if cls["pinned"]:
            mark_unplaced(cls)
        elif was_placed:
            mark_placed(cls, old_day, old_slot,
                        old_room if needs_physical_room(cls) else None)
        else:
            mark_unplaced(cls)

        # Check if placement is still valid after edit.
        # ST-ARCH-004: judged by ConstraintValidator, not logic.find_conflicts,
        # which does not look at the class's own constraints at all. Editing a
        # class to forbid the very day it sits on, or to hand it to a lecturer
        # who is unavailable then, used to leave the placement standing.
        placement_cleared = False
        if cls["placed"]:
            td = total_duration(cls)
            day = cls["placed_day"]
            slot = cls["placed_time"]
            room = cls["placed_classroom"] if needs_physical_room(cls) else None
            validator = SchedulingWorkflow._drop_validator(state, cls)
            if (not day or not slot
                    or not slots_fit(state, slot, td)
                    or not validator.check_placement(cls, day, slot, room)):
                mark_unplaced(cls)
                placement_cleared = True

        return EditClassResult(placement_cleared=placement_cleared)

    @staticmethod
    def validate_placements_after_edit(state) -> list:
        """Check all placed classes — return list of names whose placement
        became invalid (and unplace them).

        ST-ARCH-004: one ConstraintValidator for the whole sweep instead of a
        fresh ``logic.find_conflicts`` scan per class. That is both the
        authoritative rule set (the old one ignored the class's own
        allowed/excluded days, times and rooms, and room capacity) and O(n)
        occupancy construction instead of O(n) rescans of every placed class.

        ``check_placement_explained`` lifts each class's own placement out of
        the occupancy maps before judging it and puts it back afterwards, so a
        class never reports a conflict with itself.
        """
        from scheduler_app.core.constraint_validator import ConstraintValidator
        validator = ConstraintValidator(state)
        invalidated = []
        for cls in state["classes"]:
            if not cls.get("placed") or cls.get("pinned"):
                continue
            day = cls.get("placed_day")
            slot = cls.get("placed_time")
            room = (cls.get("placed_classroom")
                    if needs_physical_room(cls) else None)
            td = total_duration(cls)
            ok = bool(day) and bool(slot) and slots_fit(state, slot, td)
            if ok:
                ok, _reasons = validator.check_placement_explained(
                    cls, day, slot, room)
            if not ok:
                invalidated.append(cls["name"])
                mark_unplaced(cls)
                # The class is no longer placed, so its claim on those cells
                # must go too — otherwise every class judged after it sees a
                # cell that nothing occupies as occupied.
                validator.remove_placement(cls, day, slot, room)
        return invalidated

    @staticmethod
    def register_lecturer(state, name) -> Optional[str]:
        """Make a typed lecturer name real, and return its canonical spelling.

        ST-UI-020. ``AddClassDialog``'s lecturer combo is editable, so a user
        can type a name that is not in ``state["lecturers"]`` -- which is the
        obvious thing to do when adding a class for a teacher who is not on the
        list yet. Nothing registered it, and the consequences were invisible
        until much later:

        * ``reconcile_placements`` treats a lecturer not in the list as a
          deleted one, so the next Setup OK **unplaces the lesson** and reports
          it as "N placements cleared" -- attributed to whatever the user just
          changed in Setup, not to the name they typed hours earlier;
        * lecturer availability is keyed on ``state["lecturer_availability"]``
          and no UI can create a record for a name that is not in the list, so
          the teacher's unavailable hours never applied;
        * the class was still counted and drawn, so nothing looked wrong.

        Matching goes through ``scheduler_app.i18n.text_fold.fold_text`` so that
        "ayşe yılmaz" does not become a second teacher beside "Ayşe Yılmaz" --
        and neither does "ilhan demir" beside "İlhan Demir", which the bare
        ``casefold()`` this used to call DID split, measured on this tree.
        ``_process_teachers`` in the importer folds with the same function, so
        the two sides cannot drift apart. The FIRST spelling wins and is
        returned, because the existing one is the one availability records are
        keyed on -- with the one exception ``_resolve_roster_spelling``
        documents and measures: when the roster holds the typed spelling
        EXACTLY, that entry wins wherever it sits, because it is on the roster
        and it has its own availability record. Only a roster carrying two
        spellings that fold together can tell the two rules apart, and only
        ``SetupDialog`` can build one.
        Returns ``None`` for a blank name, which ``new_class()`` ships as the
        default and the core reads as "no lecturer constraint".
        """
        clean = (name or "").strip()
        if not clean:
            return None
        existing = state.setdefault("lecturers", [])
        spelling, _collides = _resolve_roster_spelling(existing, clean)
        if spelling is not None:
            return spelling
        existing.append(clean)
        return clean

    @staticmethod
    def find_lecturer_collision(state, name) -> Optional[str]:
        """The roster spelling that would quietly take this lesson, or None.

        ST-UI-020's read-only half, for a caller that has a human in front of
        it. ``register_lecturer`` answers a fold match by returning the spelling
        already on the roster, and all three ``ui/app.py`` call sites write that
        string straight onto ``cls["lecturer"]``. When the match is a genuine
        collision rather than a re-casing, that is a lesson changing owner: a
        class typed for *İlgin* becomes *Ilgın's*, is checked against *Ilgın's*
        availability, and *İlgin* never enters ``state["lecturers"]`` at all --
        so no UI can ever record their unavailable hours. Two real Turkish given
        names collide that way (Ilgın/İlgin, Sıla/Sila): ``fold_text`` sends
        every dotted and dotless I to ASCII ``i``.

        The importer refuses an entire workbook for that same collision
        (``errors.teacher_names_fold_together``), which is right where nobody is
        watching; the form used to accept it in silence, which is the one policy
        of the two that cannot be defended. This query is what lets the form
        speak while leaving ``register_lecturer``'s contract alone -- returning
        a tuple from it instead breaks all nine tests that pin the current
        return value, measured, whereas nothing that only *adds* a query breaks
        any of them.

        Returns None -- "nothing to say" -- for a blank name, for no match, for
        an exact match ANYWHERE on the roster, and for a match that differs
        only by case (``_same_name_other_case``). Those last two exemptions are
        load-bearing in two directions: re-typing a listed teacher in another
        casing must keep resolving silently to the roster spelling, and a
        dialog raised on an exact match would block
        ``tests/test_ui_affordances.py``'s ``_add_class_at`` run forever under
        the offscreen platform -- measured, a mutation that made an exact match
        prompt hung that module past a 150 s timeout rather than failing it.

        "Anywhere" is the word this got wrong when it shipped: the loop
        returned at the first fold match and so never reached a later exact
        one. ``_resolve_roster_spelling`` holds the corrected rule, and holds
        it for ``register_lecturer`` too, so the answer here cannot drift from
        the assignment it describes again.

        Read-only on purpose: ``.get`` rather than ``register_lecturer``'s
        ``setdefault``, no append. A UI asking "what would happen?" must not be
        what makes it happen. Both go through one resolver, so the name
        reported is exactly the name that would be assigned.
        """
        clean = (name or "").strip()
        if not clean:
            return None
        spelling, collides = _resolve_roster_spelling(
            state.get("lecturers") or [], clean)
        return spelling if collides else None

    @staticmethod
    def reconcile_placements(state) -> "ReconcileReport":
        """Clear every placement, pin or room constraint that points at an axis
        value the state no longer has. Returns the affected class dicts.

        ST-DATA-004. Removing a day, hour, room or lecturer in Setup used to
        leave the classes already placed there pointing at something that no
        longer exists — orphans reachable through completely ordinary UI use,
        which then crashed analytics, export and reschedule (ST-DATA-003).

        The constraint half is B4 and arrived three phases later: a dangling
        room name in ``required_classrooms`` / ``excluded_classrooms`` is the
        same orphan wearing different clothes. See the comment at the sweep.

        The return value is a ``ReconcileReport`` — a ``list`` of the affected
        classes, exactly as before, carrying one extra attribute
        (``lost_room_requirements``) for the subset whose room requirement was
        not narrowed but **erased**. Additive on purpose: every existing caller
        reads the list and nothing else.

        It lives in core rather than in ``SetupDialog`` so that import, undo and
        any future entry point get the same repair; dialogs writing live state
        is ST-ARCH-007. It only ever *clears* fields, never invents a placement,
        so it cannot corrupt a good file and needs no schema bump.

        A blank lecturer is deliberately treated as "unassigned", not "deleted":
        ``new_class()`` ships ``"lecturer": ""``, ``SetupDialog`` never puts ""
        into ``state["lecturers"]``, and the core reads blank as "no lecturer
        constraint". Treating it as an orphan would unplace every not-yet-staffed
        lesson on the first Setup OK.
        """
        days = set(state.get("days") or [])
        slots = set(state.get("slots") or [])
        rooms = set(state.get("classrooms") or [])
        lecturers = set(state.get("lecturers") or [])
        affected = ReconcileReport()
        for cls in state.get("classes", []):
            physical = needs_physical_room(cls)
            name = (cls.get("lecturer") or "").strip()
            lecturer_ok = (not name) or name in lecturers
            touched = False
            if cls.get("pinned"):
                day_bad = cls.get("pinned_day") not in days
                time_bad = cls.get("pinned_time") not in slots
                room_bad = physical and cls.get("pinned_classroom") not in rooms
                if day_bad or time_bad or room_bad or not lecturer_ok:
                    cls["pinned"] = False
                    if day_bad:
                        cls["pinned_day"] = None
                    if time_bad:
                        cls["pinned_time"] = None
                    if room_bad:
                        cls["pinned_classroom"] = None
                    touched = True
            if cls.get("placed") and (
                    cls.get("placed_day") not in days
                    or cls.get("placed_time") not in slots
                    or (physical and cls.get("placed_classroom") not in rooms)
                    or not lecturer_ok):
                mark_unplaced(cls)
                touched = True

            # B4 — the same repair, applied to the two fields that hold room
            # NAMES rather than a placement.
            #
            # `required_classrooms` / `excluded_classrooms` are literal names by
            # design: the importer resolves `required_room_type` down to names
            # (data_io/importer.py:456-468) because that is the one room
            # constraint the solver, the conflict graph, the negotiator and the
            # class dialog all already read. A name is not a stable key.
            # `SetupDialog._ok` assigns `state["classrooms"] = rooms` as a plain
            # list (ui/dialogs.py:1966), so renaming "Lab 1" to "Lab A" reaches
            # state as nothing but a different string and leaves the constraint
            # naming a room that no longer exists.
            #
            # Left alone the two fields fail in OPPOSITE directions, both
            # silently:
            #   required — `get_physical_room_candidates` (core/models.py:557)
            #     keeps only rooms that are IN the list, so a stale name
            #     intersects nothing, the candidate set collapses to [] and the
            #     class can never be placed again: not by drag, not by the
            #     greedy pass, not by the solver.
            #   excluded — the filter at core/models.py:559 matches nothing, so
            #     the exclusion quietly stops applying and the class becomes
            #     eligible for the very room the user forbade. That one produces
            #     a WRONG schedule rather than none, which is the worse of the
            #     two and is why this sweep covers both fields.
            #
            # Dropping the name is safe ONLY because the class then lands in
            # `affected`. `[]` means "any room" everywhere in core
            # (models.py:557), so an unreported drop would turn "must be in the
            # physics lab" into "anywhere at all" — ST-FUNC-009, the exact
            # failure the importer's type resolution was written to prevent.
            # `affected` is what raises the toast at ui/app.py; a version of
            # this sweep that repaired the field without reporting would be a
            # second bug, not a fix.
            #
            # That argument needed one more piece than it had, because
            # `affected` is a COUNT to both call sites and the two outcomes it
            # was covering are not the same event. Measured on one Setup OK
            # renaming "Lab 1" -> "Lab A" over two classes:
            #
            #   required ['Lab 1','Lab 2'] -> ['Lab 2']  candidates ['Lab 2']
            #   required ['Lab 1']         -> []         candidates ALL rooms
            #
            # and one message for both: "2 class(es) were repaired". Driven to
            # the end through `place_batch`, the second class — a physics lab
            # lesson — was auto-placed into "Hall A", the lecture hall. That is
            # the ST-FUNC-009 inversion arriving through the repair written to
            # prevent it. So the erasure is recorded separately, WITH the class
            # and the room names, in `lost_room_requirements`.
            #
            # Why erase at all, rather than keep the dangling name? Because
            # keeping it is not a state the app can hold. `AddClassDialog._ok`
            # rebuilds both room fields from the LIVE room list
            # (ui/dialogs.py:2695-2696), so opening Edit Class on that lesson
            # and pressing OK deletes the name anyway — no toast, no undo entry,
            # on a field the user never touched. Meanwhile the class is
            # unplaceable by every path in the app for a reason no screen
            # displays (`get_physical_room_candidates` -> []). The real choice
            # is between two deletions, and only one of them names the lesson
            # and the room in the warning log, where a human can read it and
            # type the room back. Undo covers the two Setup entry points and
            # the import transaction rolls back, so with the message there is a
            # way back from all three; without it there is none.
            #
            # Narrowing is deliberately NOT reported this way. The class is
            # still constrained, the schedule it produces is still legal, and a
            # school that renames one room must not get a wall of text —
            # `tests/test_setup_reconcile.py` holds the quiet cases quiet.
            #
            # `excluded_classrooms` is not tracked here either, and that is a
            # judgement, not an oversight: an exclusion naming a room that does
            # not exist forbids nothing already (models.py:559 matches on the
            # same exact `in`), so emptying it changes no scheduling outcome.
            # The residual harm is that the NAME is gone if the room comes
            # back, which is a smaller and different claim than "this lesson
            # can now go anywhere".
            #
            # Not guarded by `physical`, unlike the three placement checks
            # above. An online class carrying a stale room name is inert today
            # and becomes the bug above the moment the user switches it back to
            # face-to-face, and the cost of sweeping it is one toast the user
            # can act on rather than a constraint that silently evaporated.
            #
            # Repairing this here rather than in the dialog also matters because
            # the dialog DESTROYS the evidence: `AddClassDialog.__init__` builds
            # its room checkboxes from the live room list (ui/dialogs.py:2500,
            # 2513) and `_ok` rebuilds both fields from those same registries
            # (:2695-2696), so a stale name has no checkbox and merely opening
            # Edit Class and pressing OK deletes the constraint — no toast, no
            # undo entry, on a field the user never touched.
            for field in ("required_classrooms", "excluded_classrooms"):
                named = cls.get(field) or []
                kept = [r for r in named if r in rooms]
                if len(kept) != len(named):
                    cls[field] = kept
                    touched = True
                    if field == "required_classrooms" and named and not kept:
                        # `named` is the pre-sweep list object; `cls[field]`
                        # has already been rebound, so copy it rather than
                        # handing the caller a reference it could mutate.
                        affected.lost_room_requirements.append(
                            (cls, list(named)))

            if touched:
                affected.append(cls)
        return affected

    # ── Remove classes ───────────────────────────────────────────────────

    @staticmethod
    def remove_classes(state, classes) -> int:
        """Remove *classes* from state. Returns count of actually removed."""
        uniq = []
        seen = set()
        for cls in classes:
            if cls is None:
                continue
            cid = cls_key(cls)
            if cid in seen:
                continue
            seen.add(cid)
            if cls in state["classes"]:
                uniq.append(cls)
        for cls in uniq:
            if cls in state["classes"]:
                state["classes"].remove(cls)
        return len(uniq)

    # ── Unplace classes ──────────────────────────────────────────────────

    @staticmethod
    def unplace_classes(classes) -> int:
        """Mark classes as unplaced. Returns count."""
        count = 0
        for cls in classes:
            mark_unplaced(cls)
            count += 1
        return count

    # ── Split non-joint convenience ──────────────────────────────────────

    @staticmethod
    def split_non_joint(cls):
        """Split a non-joint class into per-target classes."""
        return split_non_joint(cls)

    # ── Quick-check for drop (no UI, fast) ───────────────────────────────

    @staticmethod
    def check_drop_valid(state, cls, day, slot, drag_backup=None,
                         preferred_rooms=None):
        """Fast boolean check: can cls be dropped at (day, slot)?"""
        if cls.get("protection") == "same_day" and drag_backup:
            original_day = drag_backup.get("placed_day")
            if original_day and day != original_day:
                return False

        # ST-ARCH-004: this used to re-implement the rules a third time -- the
        # same list as validate_drop, then the deprecated respects_constraints.
        # It drives the drag highlight, so any disagreement with the rules
        # _execute_drop actually applies shows the user a cell as droppable and
        # then refuses the drop.
        validator = SchedulingWorkflow._drop_validator(state, cls)
        room, conflicts = SchedulingWorkflow.find_drop_classroom(
            state, cls, day, slot, preferred_rooms=preferred_rooms,
            validator=validator)
        if room is None or conflicts:
            return False
        return validator.check_placement(cls, day, slot, room)

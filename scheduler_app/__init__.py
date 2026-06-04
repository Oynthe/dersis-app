"""Dersis — Modular GUI Class Scheduling Tool.

Package structure:
    core/       Scheduling engine, constraints, optimization, scoring
    learning/   Preference learner and feedback logging
    storage/    Encrypted persistence (.egu) and path management
    ui/         PyQt6 interface (app, dialogs, widgets, renderer)
    assets/     Icons and static resources
    data_io/    Excel/CSV import-export

Backward-compatible import shims:
    All public modules are re-exported at the package level so that existing
    imports like ``from scheduler_app.models import ...`` continue to work.
"""

import importlib
import sys
from importlib.abc import MetaPathFinder, Loader
from importlib.machinery import ModuleSpec

_SHIM_MAP = {
    # core/
    "scheduler_app.models":               "scheduler_app.core.models",
    "scheduler_app.logic":                "scheduler_app.core.logic",
    "scheduler_app.constants":            "scheduler_app.core.constants",
    "scheduler_app.candidate_generator":  "scheduler_app.core.candidate_generator",
    "scheduler_app.conflict_graph":       "scheduler_app.core.conflict_graph",
    "scheduler_app.constraint_negotiator":"scheduler_app.core.constraint_negotiator",
    "scheduler_app.constraint_propagator":"scheduler_app.core.constraint_propagator",
    "scheduler_app.constraint_validator": "scheduler_app.core.constraint_validator",
    "scheduler_app.cpsat_scheduler":      "scheduler_app.core.cpsat_scheduler",
    "scheduler_app.lns_strategies":       "scheduler_app.core.lns_strategies",
    "scheduler_app.schedule_optimizer":   "scheduler_app.core.schedule_optimizer",
    "scheduler_app.optimization_goals":   "scheduler_app.core.optimization_goals",
    "scheduler_app.analytics":            "scheduler_app.core.analytics",
    "scheduler_app.schedule_analytics":   "scheduler_app.core.schedule_analytics",
    "scheduler_app.schedule_impact_analyzer": "scheduler_app.core.schedule_impact_analyzer",
    "scheduler_app.explanation_engine":   "scheduler_app.core.explanation_engine",
    "scheduler_app.parallel_scorer":      "scheduler_app.core.parallel_scorer",
    "scheduler_app.placement_scorer":     "scheduler_app.core.placement_scorer",
    "scheduler_app.timetable_scorer":     "scheduler_app.core.timetable_scorer",
    "scheduler_app.workflow":             "scheduler_app.core.workflow",
    # learning/
    "scheduler_app.feedback_logger":      "scheduler_app.learning.feedback_logger",
    "scheduler_app.preference_learner":   "scheduler_app.learning.preference_learner",
    # ui/
    "scheduler_app.app":                  "scheduler_app.ui.app",
    "scheduler_app.dialogs":              "scheduler_app.ui.dialogs",
    "scheduler_app.widgets":              "scheduler_app.ui.widgets",
    "scheduler_app.renderer":             "scheduler_app.ui.renderer",
    "scheduler_app.dashboard":            "scheduler_app.ui.dashboard",
    "scheduler_app.icons":                "scheduler_app.ui.icons",
    "scheduler_app.translations":         "scheduler_app.ui.translations",
    "scheduler_app.tutorial":             "scheduler_app.ui.tutorial",
    "scheduler_app.first_run":            "scheduler_app.ui.first_run",
}


class _ShimFinder(MetaPathFinder):
    """Meta-path finder that redirects old flat imports to subpackage modules."""

    def find_module(self, fullname, path=None):
        if fullname in _SHIM_MAP:
            return _ShimLoader()
        return None

    def find_spec(self, fullname, path, target=None):
        if fullname in _SHIM_MAP:
            return ModuleSpec(fullname, _ShimLoader())
        return None


class _ShimLoader(Loader):
    """Loader that imports the real module and registers it under the alias."""

    def create_module(self, spec):
        return None  # Use default semantics

    def exec_module(self, module):
        real_name = _SHIM_MAP[module.__name__]
        real_mod = importlib.import_module(real_name)
        # Replace the module in sys.modules with the real one
        sys.modules[module.__name__] = real_mod
        # Copy attributes so the module object works
        module.__dict__.update(real_mod.__dict__)


# Install the finder (only once)
if not any(isinstance(f, _ShimFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _ShimFinder())

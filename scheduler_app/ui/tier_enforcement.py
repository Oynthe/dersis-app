"""Tier enforcement logic and upgrade UI for DERSIS scheduling application.

Provides:
- ``TierEnforcement`` — singleton that gates features and entity limits.
- ``UpgradeDialog`` — polished PyQt6 dialog shown when a user hits a limit.
- ``FeatureGateWidget`` — wrapper that disables a button and shows a tooltip
  when the user's tier does not include a required feature.
- ``gate_menu_action`` — helper to disable a QAction with upgrade tooltip.

Design principle: restricted features are **disabled + tooltip**, NOT
clickable-then-blocked.  Upgrade dialogs appear only from explicit upgrade
CTAs or when the user hits a limit at runtime (e.g. trying to add a
lecturer beyond the cap).
"""

from __future__ import annotations

import webbrowser
from typing import Callable

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon, QPainter, QColor, QPixmap, QFont, QAction
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from scheduler_app.translations import tr
import scheduler_app.ui.tier_translations  # noqa: F401 — registers upgrade.* keys
from scheduler_app.plans import (
    PLANS,
    TIER_FREE,
    TIER_INSTITUTIONAL,
    TIER_ORDER,
    TIER_STARTER,
    UNLIMITED,
    FeatureState,
    check_entity_limit,
    get_feature_state,
    get_limit,
    get_limit_state,
    get_plan,
    get_upgrade_tier,
    get_required_tier_for_limit,
    has_feature,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Offline build: there is no pricing/upgrade page. Kept as an empty string so
# any residual call site is a harmless no-op (see ``_open_pricing`` guard).
PRICING_PAGE_URL = ""

# Colour palette — matches the app's light theme
_CLR_BG = "#FFFFFF"          # white (matches QMenu, dialogs)
_CLR_BG_LIGHT = "#F1F5F9"   # slate-100 (info card background)
_CLR_TEXT = "#1E293B"        # slate-800 (primary text)
_CLR_TEXT_MUTED = "#64748B"  # slate-500 (secondary text)
_CLR_ACCENT = "#3B82F6"     # blue-500 (CTA button)
_CLR_ACCENT_HOVER = "#2563EB"  # blue-600
_CLR_BORDER = "#E2E8F0"     # slate-200 (matches app borders)
_CLR_LOCK = "#F59E0B"       # amber-500 (lock badge)


# ---------------------------------------------------------------------------
# TierEnforcement — singleton
# ---------------------------------------------------------------------------


class TierEnforcement:
    """Centralised gate-keeper for tier-based limits and features.

    Usage::

        enforcer = TierEnforcement.instance()
        enforcer.set_tier("starter")

        if not enforcer.require_feature("optimization", parent_widget):
            return  # user was shown the upgrade dialog
    """

    _instance: TierEnforcement | None = None

    def __init__(self) -> None:
        # Offline build: default to the institutional tier so every feature is
        # unlocked and no entity limit applies, with no server lookup required.
        self._tier_slug: str = TIER_INSTITUTIONAL
        self._tier_confirmed: bool = False
        self._gated_widgets: list[FeatureGateWidget] = []
        self._gated_actions: list[tuple[QAction, str]] = []
        self._on_tier_changed: list[Callable[[], None]] = []

    # -- singleton access ---------------------------------------------------

    @classmethod
    def instance(cls) -> TierEnforcement:
        """Return the global ``TierEnforcement`` instance, creating it on
        first call."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -- tier state ---------------------------------------------------------

    @property
    def tier_slug(self) -> str:
        return self._tier_slug

    @property
    def tier_confirmed(self) -> bool:
        """True once ``set_tier`` has been called with real data."""
        return self._tier_confirmed

    def set_tier(self, tier_slug: str) -> None:
        """Update the current tier (e.g. after login or heartbeat)."""
        if tier_slug in PLANS:
            self._tier_slug = tier_slug
        else:
            self._tier_slug = TIER_FREE
        self._tier_confirmed = True
        self.refresh_all_gates()

    def is_upgradeable(self) -> bool:
        """Return True if the user is on a plan that can be upgraded."""
        return self._tier_slug not in (TIER_INSTITUTIONAL,)

    # -- query helpers ------------------------------------------------------

    def can_add_entity(self, entity_type: str, current_count: int) -> bool:
        """Return ``True`` if the current tier allows adding one more
        *entity_type* given *current_count* already exist."""
        allowed, _ = check_entity_limit(self._tier_slug, entity_type, current_count)
        return allowed

    def can_use_feature(self, feature_name: str) -> bool:
        """Return ``True`` if the current tier includes *feature_name*."""
        return has_feature(self._tier_slug, feature_name)

    def get_feature_state(self, feature_name: str) -> FeatureState:
        """Return the full UI state for a feature."""
        return get_feature_state(self._tier_slug, feature_name)

    def get_limit_state(self, entity_type: str, current_count: int) -> FeatureState:
        """Return the full UI state for an entity limit."""
        return get_limit_state(self._tier_slug, entity_type, current_count)

    # -- gating helpers (show dialog when blocked) --------------------------

    def require_feature(
        self,
        feature_name: str,
        parent_widget: QWidget | None = None,
    ) -> bool:
        """Check whether *feature_name* is available.

        If it is **not**, an :class:`UpgradeDialog` is shown explaining
        which tier unlocks it.  Returns ``True`` when the feature is
        available (caller may proceed), ``False`` otherwise.
        """
        if self.can_use_feature(feature_name):
            return True

        upgrade_tier = get_upgrade_tier(self._tier_slug, feature_name)
        upgrade_plan = get_plan(upgrade_tier) if upgrade_tier else None

        dialog = UpgradeDialog(
            reason_type="feature",
            feature_or_entity=feature_name,
            upgrade_plan=upgrade_plan,
            parent=parent_widget,
        )
        dialog.exec()
        return False

    def require_entity_limit(
        self,
        entity_type: str,
        current_count: int,
        parent_widget: QWidget | None = None,
    ) -> bool:
        """Check whether adding one more *entity_type* is within limits.

        Shows an :class:`UpgradeDialog` when the limit would be exceeded.
        Returns ``True`` when the addition is allowed, ``False`` otherwise.
        """
        allowed, limit = check_entity_limit(
            self._tier_slug, entity_type, current_count
        )
        if allowed:
            return True

        needed = current_count + 1
        upgrade_slug = get_required_tier_for_limit(entity_type, needed)
        upgrade_plan = get_plan(upgrade_slug) if upgrade_slug else None

        dialog = UpgradeDialog(
            reason_type="limit",
            feature_or_entity=entity_type,
            current_count=current_count,
            limit=limit,
            upgrade_plan=upgrade_plan,
            parent=parent_widget,
        )
        dialog.exec()
        return False

    # -- gate registry (for bulk refresh on tier change) --------------------

    def register_gate_widget(self, gate: FeatureGateWidget) -> None:
        """Register a FeatureGateWidget so it refreshes on tier change."""
        self._gated_widgets.append(gate)

    def register_gated_action(self, action: QAction, feature_name: str) -> None:
        """Register a QAction that should be disabled when feature unavailable."""
        self._gated_actions.append((action, feature_name))

    def on_tier_changed(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked after every ``set_tier`` call."""
        self._on_tier_changed.append(callback)

    def refresh_all_gates(self) -> None:
        """Refresh all registered gates and actions for the current tier."""
        # Clean up dead widget refs
        self._gated_widgets = [g for g in self._gated_widgets if g is not None]
        for gate in self._gated_widgets:
            try:
                gate.refresh()
            except RuntimeError:
                pass  # widget deleted

        alive: list[tuple[QAction, str]] = []
        for action, feature_name in self._gated_actions:
            try:
                _apply_action_gate(action, feature_name, self._tier_slug)
                alive.append((action, feature_name))
            except RuntimeError:
                pass  # action deleted
        self._gated_actions = alive

        # Refresh export submenu gates
        for fn in getattr(self, '_export_submenu_refreshers', []):
            try:
                fn()
            except RuntimeError:
                pass

        # Fire general tier-change callbacks (e.g. upgrade button visibility)
        alive_cbs: list[Callable[[], None]] = []
        for cb in self._on_tier_changed:
            try:
                cb()
                alive_cbs.append(cb)
            except RuntimeError:
                pass  # prevent leaking deleted Qt objects
        self._on_tier_changed = alive_cbs


# ---------------------------------------------------------------------------
# Human-readable display names
# ---------------------------------------------------------------------------

_FEATURE_DISPLAY: dict[str, str] = {
    "export_pdf": "upgrade.feature.export_pdf",
    "export_excel": "upgrade.feature.export_excel",
    "export_csv": "upgrade.feature.export_csv",
    "bulk_scheduling": "upgrade.feature.bulk_scheduling",
    "auto_scheduling": "upgrade.feature.auto_scheduling",
    "optimization": "upgrade.feature.optimization",
    "ai_explanations": "upgrade.feature.ai_explanations",
    "auto_negotiation": "upgrade.feature.auto_negotiation",
    "revision_history": "upgrade.feature.revision_history",
    "advanced_analytics": "upgrade.feature.advanced_analytics",
    "multi_department": "upgrade.feature.multi_department",
    "api_access": "upgrade.feature.api_access",
    "sso": "upgrade.feature.sso",
    "custom_branding": "upgrade.feature.custom_branding",
}

_ENTITY_DISPLAY: dict[str, str] = {
    "max_schedules": "upgrade.entity.schedules",
    "max_classes": "upgrade.entity.classes",
    "max_classrooms": "upgrade.entity.classrooms",
    "max_lecturers": "upgrade.entity.lecturers",
    "max_years": "upgrade.entity.years",
    "max_devices": "upgrade.entity.devices",
}


def _display_name(key: str, mapping: dict[str, str]) -> str:
    """Return a translated display name, falling back to a title-cased key."""
    tr_key = mapping.get(key)
    if tr_key:
        translated = tr(tr_key)
        # If the translation system returned the key itself, fall back.
        if translated != tr_key:
            return translated
    # Fallback: strip prefix and title-case.
    return key.removeprefix("max_").replace("_", " ").title()


# ---------------------------------------------------------------------------
# Tooltip builder — single place for conversion-focused tooltip text
# ---------------------------------------------------------------------------


def build_feature_tooltip(feature_name: str, tier_slug: str) -> str:
    """Build a benefit-focused tooltip string for a locked feature.

    Format: "Upgrade to {Plan} to {benefit}"
    Returns empty string if feature is available.
    """
    state = get_feature_state(tier_slug, feature_name)
    if state.enabled:
        return ""
    tooltip_key = state.tooltip_message
    if tooltip_key:
        text = tr(tooltip_key)
        if text != tooltip_key:
            return text.replace("{plan}", state.required_plan)
    # Fallback
    name = _display_name(feature_name, _FEATURE_DISPLAY)
    return f"Upgrade to {state.required_plan} for {name}"


# ---------------------------------------------------------------------------
# gate_menu_action — disable a QAction with upgrade tooltip
# ---------------------------------------------------------------------------


def gate_export_submenu(submenu) -> None:
    """Disable the entire Export submenu when ALL export features are locked.

    Registers for auto-refresh on tier change.
    """
    from scheduler_app.plans import (
        FEATURE_EXPORT_PDF, FEATURE_EXPORT_EXCEL, FEATURE_EXPORT_CSV,
    )
    enforcer = TierEnforcement.instance()

    def _refresh():
        any_export = (
            enforcer.can_use_feature(FEATURE_EXPORT_PDF)
            or enforcer.can_use_feature(FEATURE_EXPORT_EXCEL)
            or enforcer.can_use_feature(FEATURE_EXPORT_CSV)
        )
        submenu.setEnabled(any_export)
        if not any_export:
            tooltip = build_feature_tooltip(FEATURE_EXPORT_PDF, enforcer.tier_slug)
            submenu.setToolTip("\U0001F512 " + tooltip)
        else:
            submenu.setToolTip("")

    _refresh()
    # Store refresh callback on the submenu so it survives GC
    submenu._tier_refresh = _refresh
    enforcer._export_submenu_refreshers = getattr(
        enforcer, '_export_submenu_refreshers', [])
    enforcer._export_submenu_refreshers.append(_refresh)


def gate_menu_action(
    action: QAction,
    feature_name: str,
) -> None:
    """Disable *action* and set its tooltip if the feature is locked.

    Call this after creating the action, and register it with
    TierEnforcement for auto-refresh on tier changes.
    """
    enforcer = TierEnforcement.instance()
    _apply_action_gate(action, feature_name, enforcer.tier_slug)
    enforcer.register_gated_action(action, feature_name)


def _apply_action_gate(action: QAction, feature_name: str, tier_slug: str) -> None:
    """Internal: apply enabled/disabled + tooltip to a QAction."""
    state = get_feature_state(tier_slug, feature_name)
    if state.enabled:
        action.setEnabled(True)
        action.setToolTip("")
    else:
        action.setEnabled(False)
        tooltip = build_feature_tooltip(feature_name, tier_slug)
        lock_prefix = "\U0001F512 "  # lock emoji
        action.setToolTip(lock_prefix + tooltip)


# ---------------------------------------------------------------------------
# UpgradeDialog
# ---------------------------------------------------------------------------


class UpgradeDialog(QDialog):
    """Modal dialog informing the user that a feature or limit requires a
    higher tier.

    Shown contextually when a user hits a limit at runtime (e.g. trying
    to add a lecturer beyond the cap).  NOT triggered from disabled buttons.

    Parameters
    ----------
    reason_type:
        ``"feature"`` or ``"limit"``.
    feature_or_entity:
        The feature flag name or entity-type constant that triggered the
        gate.
    current_count:
        (limits only) How many entities the user already has.
    limit:
        (limits only) The tier cap that was hit.
    upgrade_plan:
        The plan dict for the tier the user should upgrade to (may be
        ``None`` if no tier provides the capability).
    parent:
        Parent widget for modality.
    """

    def __init__(
        self,
        reason_type: str = "feature",
        feature_or_entity: str = "",
        current_count: int = 0,
        limit: int = 0,
        upgrade_plan: dict | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._upgrade_plan = upgrade_plan

        self.setWindowTitle(tr("upgrade.dialog.title"))
        self.setMinimumWidth(440)
        self.setMaximumWidth(560)
        self.setModal(True)
        self.setStyleSheet(self._build_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(16)

        # -- Lock icon + heading -------------------------------------------
        heading_layout = QHBoxLayout()
        heading_layout.setSpacing(10)

        lock_label = QLabel("\U0001F512")  # lock emoji as simple fallback
        lock_label.setFont(QFont("Segoe UI Emoji", 22))
        lock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading_layout.addWidget(lock_label)

        title_label = QLabel(tr("upgrade.dialog.heading"))
        title_label.setObjectName("heading")
        title_label.setWordWrap(True)
        heading_layout.addWidget(title_label, stretch=1)

        layout.addLayout(heading_layout)

        # -- Explanation text ----------------------------------------------
        explanation = self._build_explanation(
            reason_type, feature_or_entity, current_count, limit
        )
        explanation_label = QLabel(explanation)
        explanation_label.setObjectName("explanation")
        explanation_label.setWordWrap(True)
        layout.addWidget(explanation_label)

        # -- Upgrade tier info ---------------------------------------------
        if upgrade_plan:
            tier_name = upgrade_plan.get("name", "")
            price = upgrade_plan.get("price_monthly", 0)

            if price > 0:
                info_text = tr("upgrade.dialog.plan_info").replace(
                    "{plan}", tier_name
                ).replace("{price}", f"${price}")
            else:
                # Institutional / custom pricing
                info_text = tr("upgrade.dialog.plan_contact").replace(
                    "{plan}", tier_name
                )

            info_label = QLabel(info_text)
            info_label.setObjectName("planInfo")
            info_label.setWordWrap(True)
            layout.addWidget(info_label)

        # -- Buttons -------------------------------------------------------
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        later_btn = QPushButton(tr("upgrade.dialog.later"))
        later_btn.setObjectName("laterBtn")
        later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        later_btn.clicked.connect(self.reject)

        # Dynamic CTA: "Upgrade to {Plan}" instead of generic "View Plans"
        if upgrade_plan:
            cta_text = tr("upgrade.dialog.upgrade_to").replace(
                "{plan}", upgrade_plan.get("name", ""))
            # Fallback if translation key missing
            if cta_text == "upgrade.dialog.upgrade_to":
                cta_text = tr("upgrade.dialog.view_plans")
        else:
            cta_text = tr("upgrade.dialog.view_plans")

        plans_btn = QPushButton(cta_text)
        plans_btn.setObjectName("plansBtn")
        plans_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plans_btn.clicked.connect(self._open_pricing)

        button_layout.addStretch()
        button_layout.addWidget(later_btn)
        button_layout.addWidget(plans_btn)

        layout.addLayout(button_layout)

    # -- internals ---------------------------------------------------------

    def _build_explanation(
        self,
        reason_type: str,
        feature_or_entity: str,
        current_count: int,
        limit: int,
    ) -> str:
        if reason_type == "feature":
            name = _display_name(feature_or_entity, _FEATURE_DISPLAY)
            return (
                tr("upgrade.dialog.feature_locked")
                .replace("{feature}", name)
            )
        else:
            name = _display_name(feature_or_entity, _ENTITY_DISPLAY)
            limit_str = str(limit) if limit != UNLIMITED else tr("labels.unlimited")
            return (
                tr("upgrade.dialog.limit_reached")
                .replace("{entity}", name)
                .replace("{limit}", limit_str)
                .replace("{count}", str(current_count))
            )

    def _open_pricing(self) -> None:
        if PRICING_PAGE_URL:
            webbrowser.open(PRICING_PAGE_URL)
        self.accept()

    @staticmethod
    def _build_stylesheet() -> str:
        return f"""
            UpgradeDialog {{
                background-color: {_CLR_BG};
                border: 1px solid {_CLR_BORDER};
                border-radius: 10px;
                font-family: "Segoe UI", sans-serif;
            }}
            QLabel {{
                color: {_CLR_TEXT};
                font-size: 9pt;
                background: transparent;
            }}
            QLabel#heading {{
                font-size: 13pt;
                font-weight: 700;
                color: {_CLR_TEXT};
            }}
            QLabel#explanation {{
                font-size: 9pt;
                color: {_CLR_TEXT_MUTED};
            }}
            QLabel#planInfo {{
                font-size: 9pt;
                color: #1E40AF;
                font-weight: 500;
                padding: 10px 14px;
                background-color: #EFF6FF;
                border: 1px solid #BFDBFE;
                border-radius: 8px;
            }}
            QPushButton#laterBtn {{
                background-color: {_CLR_BG};
                color: {_CLR_TEXT_MUTED};
                border: 1px solid {_CLR_BORDER};
                border-radius: 6px;
                padding: 8px 20px;
                font-size: 9pt;
                font-family: "Segoe UI", sans-serif;
            }}
            QPushButton#laterBtn:hover {{
                background-color: {_CLR_BG_LIGHT};
                color: {_CLR_TEXT};
                border-color: #CBD5E1;
            }}
            QPushButton#plansBtn {{
                background-color: {_CLR_ACCENT};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 24px;
                font-size: 9pt;
                font-weight: 600;
                font-family: "Segoe UI", sans-serif;
            }}
            QPushButton#plansBtn:hover {{
                background-color: {_CLR_ACCENT_HOVER};
            }}
        """


# ---------------------------------------------------------------------------
# FeatureGateWidget
# ---------------------------------------------------------------------------


class FeatureGateWidget(QWidget):
    """Wrapper that disables a ``QPushButton`` and shows a conversion-focused
    tooltip when the user's tier does not include a required feature.

    The button is **not clickable** when locked — no dialog on click.
    Instead, the tooltip guides the user to the upgrade path.

    Usage::

        btn = QPushButton("Run Optimizer")
        gated = FeatureGateWidget(
            child=btn,
            feature_name="optimization",
        )
        toolbar_layout.addWidget(gated)

    When the feature *is* available on the current tier, the child widget
    behaves normally.  When it is *not*, the child is disabled with reduced
    opacity, a lock badge, and a tooltip explaining the upgrade.

    Call :meth:`refresh` after the tier changes (e.g. post-login) to
    update the visual state.
    """

    def __init__(
        self,
        child: QPushButton,
        feature_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._child = child
        self._feature_name = feature_name
        self._locked = False

        # Layout — the child fills this wrapper entirely.
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(child)

        # Lock badge (painted over the top-right corner of the child).
        self._lock_badge = QLabel(self)
        self._lock_badge.setFixedSize(18, 18)
        self._lock_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lock_badge.setStyleSheet(
            f"""
            QLabel {{
                background-color: {_CLR_LOCK};
                color: #ffffff;
                border-radius: 9px;
                font-size: 10px;
                font-weight: bold;
            }}
            """
        )
        self._lock_badge.setText("\U0001F512")
        self._lock_badge.setFont(QFont("Segoe UI Emoji", 8))
        self._lock_badge.hide()

        # Opacity effect for the "locked" look.
        self._opacity = QGraphicsOpacityEffect(child)
        child.setGraphicsEffect(self._opacity)

        # Register with enforcer for auto-refresh
        TierEnforcement.instance().register_gate_widget(self)
        self.refresh()

    # -- public API --------------------------------------------------------

    def refresh(self) -> None:
        """Re-evaluate the gate based on the current tier."""
        enforcer = TierEnforcement.instance()
        should_lock = not enforcer.can_use_feature(self._feature_name)

        if should_lock == self._locked:
            return  # no change

        self._locked = should_lock

        if should_lock:
            self._opacity.setOpacity(0.45)
            self._lock_badge.show()
            self._position_badge()
            # Disable the button — NO clicks, NO events
            self._child.setEnabled(False)
            self._child.setCursor(Qt.CursorShape.ForbiddenCursor)
            # Set conversion-focused tooltip
            tooltip = build_feature_tooltip(
                self._feature_name, enforcer.tier_slug)
            self._child.setToolTip("\U0001F512 " + tooltip)
        else:
            self._opacity.setOpacity(1.0)
            self._lock_badge.hide()
            self._child.setEnabled(True)
            self._child.setCursor(Qt.CursorShape.ArrowCursor)
            self._child.setToolTip("")

    # -- Qt overrides ------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_badge()

    # -- internals ---------------------------------------------------------

    def _position_badge(self) -> None:
        """Place the lock badge at the top-right of the child widget."""
        if self._child:
            x = self._child.geometry().right() - self._lock_badge.width() - 2
            y = self._child.geometry().top() + 2
            self._lock_badge.move(x, y)
            self._lock_badge.raise_()

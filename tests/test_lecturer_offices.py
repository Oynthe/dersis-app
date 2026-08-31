"""Lecturer offices are person-specific resources, not one shared classroom."""

from types import SimpleNamespace

from scheduler_app.core.logic import classroom_of, find_schedule_conflicts
from scheduler_app.core.models import (
    LOCATION_LECTURER_OFFICE,
    get_classroom_export_labels,
    get_lecturer_office_options,
    new_class,
    new_state,
)


def _office_class(name, lecturer, target):
    cls = new_class()
    cls.update({
        "name": name,
        "lecturer": lecturer,
        "location_type": LOCATION_LECTURER_OFFICE,
        "targets": [{"year": "1", "branch": target}],
        "placed": True,
        "placed_day": "monday",
        "placed_time": "09:00",
        "placed_classroom": None,
    })
    return cls


def test_office_resource_is_qualified_by_lecturer():
    ayse = _office_class("Ders A", "Ayşe Yılmaz", "A")
    mehmet = _office_class("Ders B", "Mehmet Kaya", "B")

    assert classroom_of(ayse) == "Ofis (Öğr. Elem.) — Ayşe Yılmaz"
    assert classroom_of(mehmet) == "Ofis (Öğr. Elem.) — Mehmet Kaya"
    assert classroom_of(ayse) != classroom_of(mehmet)


def test_different_lecturer_offices_can_overlap_without_a_room_conflict():
    state = new_state()
    state["days"] = ["monday"]
    state["slots"] = ["09:00"]
    state["years"] = {"1": ["A", "B"]}
    state["lecturers"] = ["Ayşe Yılmaz", "Mehmet Kaya"]
    state["classes"] = [
        _office_class("Ders A", "Ayşe Yılmaz", "A"),
        _office_class("Ders B", "Mehmet Kaya", "B"),
    ]

    assert find_schedule_conflicts(state) == []


def test_same_lecturer_still_cannot_teach_two_office_lessons_at_once():
    state = new_state()
    state["days"] = ["monday"]
    state["slots"] = ["09:00"]
    state["years"] = {"1": ["A", "B"]}
    state["lecturers"] = ["Ayşe Yılmaz"]
    state["classes"] = [
        _office_class("Ders A", "Ayşe Yılmaz", "A"),
        _office_class("Ders B", "Ayşe Yılmaz", "B"),
    ]

    conflicts = find_schedule_conflicts(state)
    assert len(conflicts) == 1
    assert conflicts[0]["kinds"] == ("lecturer",)


def test_classroom_exports_get_one_office_view_per_lecturer():
    classes = [
        _office_class("Ders A", "Ayşe Yılmaz", "A"),
        _office_class("Ders B", "Mehmet Kaya", "B"),
        _office_class("Ders C", "Ayşe Yılmaz", "C"),
    ]

    assert get_classroom_export_labels(["D101"], classes) == [
        "D101",
        "Ofis (Öğr. Elem.) — Ayşe Yılmaz",
        "Ofis (Öğr. Elem.) — Mehmet Kaya",
    ]


def test_classroom_view_gets_one_office_option_per_added_lecturer():
    assert get_lecturer_office_options([
        "Ayşe Yılmaz", "Mehmet Kaya", "Ayşe Yılmaz", "",
    ]) == [
        ("Ayşe Yılmaz", "Ofis (Öğr. Elem.) — Ayşe Yılmaz"),
        ("Mehmet Kaya", "Ofis (Öğr. Elem.) — Mehmet Kaya"),
    ]


def test_classroom_filter_addresses_one_lecturers_office():
    from scheduler_app.ui.app import (
        SchedulerApp,
        _decode_classroom_filter_value,
        _encode_classroom_filter_office,
    )
    from scheduler_app.ui.renderer import FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP

    value = _encode_classroom_filter_office("Ayşe::Yılmaz")
    assert _decode_classroom_filter_value(value) == (
        "office", "Ayşe::Yılmaz")

    combo = SimpleNamespace(currentData=lambda: value, currentText=lambda: "")
    window = SimpleNamespace(classroom_filter=combo)
    assert SchedulerApp._filter_classroom(
        window, _office_class("Ders A", "Ayşe::Yılmaz", "A")) is True
    assert SchedulerApp._filter_classroom(
        window, _office_class("Ders B", "Mehmet Kaya", "B")) is False
    assert SchedulerApp._filtered_render_mode(
        window, 0) == FILTER_MODE_VIRTUAL_CLASSROOM_OVERLAP

"""Localized workbook schema helpers for Excel import/export templates."""

from scheduler_app.translations import TRANSLATIONS, tr


WORKBOOK_SHEETS = {
    "teachers": {
        "title_key": "labels.teachers",
        "legacy_title": "Teachers",
        "columns": [
            ("teacher_id", "import.columns.teacher_id", "import.desc.teacher_id"),
            ("name", "import.columns.teacher_name", "import.desc.teacher_name"),
            ("allowed_days", "setup.allowed_days", "import.desc.allowed_days"),
            ("allowed_hours", "setup.allowed_hours", "import.desc.allowed_hours"),
            ("excluded_days", "setup.excluded_days", "import.desc.excluded_days"),
            ("excluded_hours", "setup.excluded_hours", "import.desc.excluded_hours"),
        ],
    },
    "rooms": {
        "title_key": "tabs.rooms",
        "legacy_title": "Rooms",
        "columns": [
            ("room_id", "import.columns.room_id", "import.desc.room_id"),
            ("name", "import.columns.room_name", "import.desc.room_name"),
            ("capacity", "setup.capacity", "import.desc.capacity"),
            ("room_type", "import.columns.room_type", "import.desc.room_type"),
        ],
    },
    "branches": {
        "title_key": "setup.branches",
        "legacy_title": "Branches",
        "columns": [
            ("branch_id", "import.columns.branch_id", "import.desc.branch_id"),
            ("name", "import.columns.branch_name", "import.desc.branch_name"),
        ],
    },
    "classes": {
        "title_key": "labels.classes",
        "legacy_title": "Classes",
        "columns": [
            ("class_id", "import.columns.class_id", "import.desc.class_id"),
            ("class_code", "labels.class_code", "import.desc.class_code"),
            ("course_name", "labels.course", "import.desc.course_name"),
            ("teacher_id", "import.columns.teacher_id", "import.desc.class_teacher_id"),
            ("branch_id", "import.columns.branch_id", "import.desc.class_branch_id"),
            ("duration", "labels.duration", "import.desc.duration"),
            ("student_count", "import.columns.student_count", "import.desc.student_count"),
            ("required_room_type", "import.columns.required_room_type", "import.desc.required_room_type"),
            ("allowed_rooms", "import.columns.allowed_rooms", "import.desc.allowed_rooms"),
            ("excluded_rooms", "import.columns.excluded_rooms", "import.desc.excluded_rooms"),
            ("joint_class_group", "import.columns.joint_class_group", "import.desc.joint_class_group"),
            ("location_type", "labels.location_type", "import.desc.location_type"),
        ],
    },
}


def _normalize_label(value):
    return str(value or "").strip().rstrip(":").strip()


def get_workbook_sheet_title(sheet_id):
    spec = WORKBOOK_SHEETS[sheet_id]
    return _normalize_label(tr(spec["title_key"]) or spec["legacy_title"])


def get_workbook_sheet_headers(sheet_id):
    headers = []
    for field, label_key, _ in WORKBOOK_SHEETS[sheet_id]["columns"]:
        label = _normalize_label(tr(label_key) or field)
        headers.append(label)
    return headers


def get_workbook_sheet_header_map(sheet_id):
    return {
        field: _normalize_label(tr(label_key) or field)
        for field, label_key, _ in WORKBOOK_SHEETS[sheet_id]["columns"]
    }


def get_workbook_sheet_description_map(sheet_id):
    return {
        field: tr(desc_key)
        for field, _, desc_key in WORKBOOK_SHEETS[sheet_id]["columns"]
    }


def get_workbook_sheet_reverse_header_map(sheet_id):
    reverse = {}
    for field, label_key, _ in WORKBOOK_SHEETS[sheet_id]["columns"]:
        reverse[_normalize_label(field).casefold()] = field
        reverse[_normalize_label(tr(label_key) or field).casefold()] = field
        for lang_dict in TRANSLATIONS.values():
            label = _normalize_label(lang_dict.get(label_key, field))
            if label:
                reverse[label.casefold()] = field
    return reverse


def get_workbook_sheet_alias_map():
    alias_map = {}
    for sheet_id, spec in WORKBOOK_SHEETS.items():
        names = {
            spec["legacy_title"],
            _normalize_label(tr(spec["title_key"]) or spec["legacy_title"]),
        }
        for lang_dict in TRANSLATIONS.values():
            label = _normalize_label(lang_dict.get(spec["title_key"], spec["legacy_title"]))
            if label:
                names.add(label)
        for name in names:
            alias_map[name.casefold()] = sheet_id
    return alias_map


def lookup_workbook_sheet_id(sheet_name):
    return get_workbook_sheet_alias_map().get(_normalize_label(sheet_name).casefold())


def canonicalize_workbook_columns(sheet_id, columns):
    reverse = get_workbook_sheet_reverse_header_map(sheet_id)
    renamed = {}
    for column in columns:
        key = _normalize_label(column).casefold()
        renamed[column] = reverse.get(key, column)
    return renamed

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


def get_workbook_sheet_description_texts(sheet_id):
    """Every string the template has ever written into a sheet's row 2.

    The importer drops that row rather than reading it as data. It used to
    recognize it by shape — "longer than 20 characters, or contains a space" —
    which no Chinese or Japanese sentence satisfies, so the zh and ja templates
    imported their own help text as a lecturer, a classroom and a branch; and
    which a class id like ``9 A`` does satisfy, so that class was dropped
    (ST-FUNC-010). Matching the actual strings, in every language, decides both
    cases on what the row *is* instead of how long it is.
    """
    texts = set()
    for _, _, desc_key in WORKBOOK_SHEETS[sheet_id]["columns"]:
        for lang_dict in TRANSLATIONS.values():
            text = lang_dict.get(desc_key)
            if text:
                texts.add(str(text).strip())
        # The active language, which falls back to English for any key its
        # own catalogue is missing — that is the string the template wrote.
        text = tr(desc_key)
        if text and text != desc_key:
            texts.add(str(text).strip())
    return texts


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


def get_workbook_sheet_titles(sheet_id):
    """Every title the app has ever written for one sheet, in any language."""
    spec = WORKBOOK_SHEETS[sheet_id]
    names = {
        spec["legacy_title"],
        _normalize_label(tr(spec["title_key"]) or spec["legacy_title"]),
    }
    for lang_dict in TRANSLATIONS.values():
        label = _normalize_label(lang_dict.get(spec["title_key"], spec["legacy_title"]))
        if label:
            names.add(label)
    return names


def get_workbook_sheet_alias_candidates():
    """Map each known sheet title to *every* sheet it could be naming.

    Sheet titles are not unique across the 22 shipped languages: Spanish calls
    its classroom sheet *Aulas* and Portuguese calls its class sheet *Aulas*.
    The single-valued map this replaced had to pick a winner per title, and
    classes always won, so a Spanish workbook's rooms sheet was read as its
    class sheet — the Spanish template could not be re-imported at all.
    Ambiguity is a property of the title, so it is recorded here and resolved
    per workbook by :func:`resolve_workbook_sheet_ids`.
    """
    candidates = {}
    for sheet_id in WORKBOOK_SHEETS:
        for name in get_workbook_sheet_titles(sheet_id):
            bucket = candidates.setdefault(name.casefold(), [])
            if sheet_id not in bucket:
                bucket.append(sheet_id)
    return candidates


def resolve_workbook_sheet_ids(sheet_names):
    """Decide which sheet of one workbook holds which kind of data.

    Two passes, so that a title only one sheet can be claims it before an
    ambiguous title gets a chance to: in a Spanish workbook *Clases* is
    unambiguously the class sheet, which leaves *Aulas* the rooms slot; in a
    Portuguese workbook *Salas* is unambiguously rooms, which leaves *Aulas*
    the classes slot. The answer therefore comes from the workbook itself and
    not from the language the reader's app happens to be running in — letting
    the active locale's titles win instead would repair Spanish by breaking
    the Portuguese workbook opened on a Spanish desktop.

    Returns ``{sheet_id: actual sheet title}`` for the sheets that resolved.
    """
    candidates = get_workbook_sheet_alias_candidates()
    resolved = {}
    ambiguous = []
    for actual_name in sheet_names:
        sheet_ids = candidates.get(_normalize_label(actual_name).casefold())
        if not sheet_ids:
            continue
        if len(sheet_ids) == 1:
            resolved.setdefault(sheet_ids[0], actual_name)
        else:
            ambiguous.append((actual_name, sheet_ids))

    for actual_name, sheet_ids in ambiguous:
        free = [s for s in sheet_ids if s not in resolved]
        if not free:
            continue
        # Only reachable when a workbook carries an ambiguous title and none of
        # the sheets it could name are spoken for — a rooms-only file called
        # *Aulas*, say. Nothing in the workbook can settle that, so the reader's
        # own language breaks the tie; with a complete workbook this branch
        # never decides anything, which the round-trip tests pin.
        for sheet_id in free:
            if _normalize_label(actual_name).casefold() == \
                    get_workbook_sheet_title(sheet_id).casefold():
                resolved[sheet_id] = actual_name
                break
        else:
            resolved[free[0]] = actual_name
    return resolved


def canonicalize_workbook_columns(sheet_id, columns):
    reverse = get_workbook_sheet_reverse_header_map(sheet_id)
    renamed = {}
    for column in columns:
        key = _normalize_label(column).casefold()
        renamed[column] = reverse.get(key, column)
    return renamed

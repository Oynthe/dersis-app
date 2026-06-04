"""Export pipeline for the scheduler.

Exports final timetable to Excel (.xlsx), CSV, and optionally PDF.
Does not modify scheduling logic or internal data.
"""

import csv
import os
from typing import Any

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

from scheduler_app.logic import (
    get_placed_classes, occupied_slots_of, classroom_of, total_duration,
    get_year_color, lighten_color, build_virtual_classroom_day_layout,
)
from scheduler_app.models import (
    get_classroom_export_labels,
    get_protection_label,
    effective_day, effective_time,
    slot_offset_for_target,
)
from scheduler_app.translations import tr
from scheduler_app.ui.badge_formatter import get_badge
from scheduler_app.ui.cell_formatter import plain_cell_text


class FinalSchedule:
    """Wrapper around scheduler state for export purposes."""

    def __init__(self, state: dict):
        self.state = state

    @property
    def days(self):
        return self.state["days"]

    @property
    def slots(self):
        return self.state["slots"]

    @property
    def classrooms(self):
        return self.state["classrooms"]

    @property
    def lecturers(self):
        return self.state.get("lecturers", [])

    @property
    def years(self):
        return self.state.get("years", {})

    def placed_classes(self):
        return get_placed_classes(self.state)

    def build_grid(self):
        """Build a (day, slot) -> list of class info dicts grid."""
        grid = {}
        for cls in self.placed_classes():
            for day, slot in occupied_slots_of(self.state, cls):
                entry = {
                    "class_code": cls.get("class_code", ""),
                    "name": cls["name"],
                    "lecturer": cls["lecturer"],
                    "room": classroom_of(cls),
                    "targets": cls.get("targets", []),
                    "cls": cls,
                }
                grid.setdefault((day, slot), []).append(entry)
        return grid


def _strip_hash(color):
    """Strip '#' from hex color string for openpyxl."""
    return color.lstrip("#")


def _cell_text(entry):
    """Format a schedule entry for a grid cell (plain text fallback)."""
    return plain_cell_text(entry)


def _rich_cell(entry):
    """Build a CellRichText with color-coded lines matching the app view."""
    blocks = []
    code = entry.get("class_code", "")
    if code:
        blocks.append(TextBlock(
            InlineFont(b=True, sz=9, color="1D4ED8"), code + "\n"))
    blocks.append(TextBlock(
        InlineFont(b=True, sz=10, color="1E293B"), entry["name"] + "\n"))
    if entry["lecturer"]:
        blocks.append(TextBlock(
            InlineFont(sz=9, color="475569"), entry["lecturer"] + "\n"))
    if entry["room"]:
        blocks.append(TextBlock(
            InlineFont(sz=9, color="16A34A"), entry["room"]))

    cls = entry.get("cls", {})
    emoji, label, color = get_badge(cls)
    if emoji:
        blocks.append(TextBlock(
            InlineFont(b=True, sz=8, color=_strip_hash(color)),
            "\n" + f"{emoji} {label}"))

    return CellRichText(*blocks)


def _entry_bg_color(entry, state):
    """Return the lightened background hex for a lesson cell."""
    cls = entry.get("cls", {})
    yr_name = ""
    targets = cls.get("targets", [])
    if targets:
        yr_name = targets[0].get("year", "")
    base = get_year_color(state, yr_name)
    light = lighten_color(base, 0.45)
    return _strip_hash(light)


def _export_excel(schedule: FinalSchedule, filepath: str):
    """Export to a multi-sheet Excel workbook."""
    if not HAS_OPENPYXL:
        raise RuntimeError(tr("errors.openpyxl_required_install"))

    wb = openpyxl.Workbook()
    grid = schedule.build_grid()

    day_hdr_fill = PatternFill("solid", fgColor="334155")
    day_hdr_font = Font(bold=True, size=11, color="FFFFFF")
    day_hdr_align = Alignment(horizontal="center", vertical="center")

    corner_fill = PatternFill("solid", fgColor="94A3B8")
    corner_font = Font(bold=True, size=11, color="FFFFFF")

    time_fill = PatternFill("solid", fgColor="475569")
    time_font = Font(bold=True, size=10, color="FFFFFF")
    time_align = Alignment(horizontal="center", vertical="center")

    cell_align = Alignment(wrap_text=True, vertical="center", horizontal="center")
    empty_fill = PatternFill("solid", fgColor="F8FAFC")

    grid_side = Side(style="thin", color="CBD5E1")
    grid_border = Border(left=grid_side, right=grid_side, top=grid_side, bottom=grid_side)

    def _write_grid_sheet(ws, title, filter_fn=None):
        """Write a grid-format sheet: rows=hours, columns=days."""
        ws.title = title

        c = ws.cell(row=1, column=1, value=tr("labels.time"))
        c.font = corner_font
        c.fill = corner_fill
        c.alignment = day_hdr_align
        c.border = grid_border

        for ci, day in enumerate(schedule.days, start=2):
            cell = ws.cell(row=1, column=ci, value=tr(f"weekdays.{day}"))
            cell.font = day_hdr_font
            cell.fill = day_hdr_fill
            cell.alignment = day_hdr_align
            cell.border = grid_border
            ws.column_dimensions[cell.column_letter].width = 25

        ws.column_dimensions["A"].width = 12
        ws.row_dimensions[1].height = 30

        for ri, slot in enumerate(schedule.slots, start=2):
            tc = ws.cell(row=ri, column=1, value=slot)
            tc.font = time_font
            tc.fill = time_fill
            tc.alignment = time_align
            tc.border = grid_border
            ws.row_dimensions[ri].height = 70

            for ci, day in enumerate(schedule.days, start=2):
                entries = grid.get((day, slot), [])
                if filter_fn:
                    entries = [e for e in entries if filter_fn(e)]
                cell = ws.cell(row=ri, column=ci)
                if entries:
                    if len(entries) == 1:
                        cell.value = _rich_cell(entries[0])
                    else:
                        parts = []
                        for idx, e in enumerate(entries):
                            if idx > 0:
                                parts.append(TextBlock(
                                    InlineFont(sz=8, color="94A3B8"),
                                    "\n---\n"))
                            code = e.get("class_code", "")
                            if code:
                                parts.append(TextBlock(
                                    InlineFont(b=True, sz=9, color="1D4ED8"),
                                    code + "\n"))
                            parts.append(TextBlock(
                                InlineFont(b=True, sz=10, color="1E293B"),
                                e["name"] + "\n"))
                            if e["lecturer"]:
                                parts.append(TextBlock(
                                    InlineFont(sz=9, color="475569"),
                                    e["lecturer"] + "\n"))
                            if e["room"]:
                                parts.append(TextBlock(
                                    InlineFont(sz=9, color="16A34A"),
                                    e["room"]))
                        cell.value = CellRichText(*parts)
                    bg_hex = _entry_bg_color(entries[0], schedule.state)
                    cell.fill = PatternFill("solid", fgColor=bg_hex)
                    cell.alignment = cell_align
                else:
                    cell.fill = empty_fill
                    cell.alignment = cell_align
                cell.border = grid_border

    def _rich_cell_for_class(cls, include_room=False, include_targets=False):
        """Build a CellRichText directly from a class dict."""
        parts = []
        code = cls.get("class_code", "")
        if code:
            parts.append(TextBlock(
                InlineFont(b=True, sz=9, color="1D4ED8"), code + "\n"))
        parts.append(TextBlock(
            InlineFont(b=True, sz=10, color="1E293B"), cls["name"] + "\n"))
        if cls.get("lecturer"):
            parts.append(TextBlock(
                InlineFont(sz=9, color="475569"), cls["lecturer"]))
        if include_room:
            room = classroom_of(cls)
            if room:
                parts.append(TextBlock(
                    InlineFont(sz=9, color="16A34A"), "\n" + room))
        if include_targets:
            groups = ", ".join(
                f"{t['year']}/{t['branch']}" for t in cls.get("targets", []))
            if groups:
                parts.append(TextBlock(
                    InlineFont(sz=8, color="6D28D9"), "\n" + groups))
        return CellRichText(*parts)

    def _write_virtual_room_sheet(ws, title, room):
        """Write a room sheet using fixed day subcolumns for virtual resources."""
        ws.title = title

        c = ws.cell(row=1, column=1, value=tr("labels.time"))
        c.font = corner_font
        c.fill = corner_fill
        c.alignment = day_hdr_align
        c.border = grid_border

        layout = build_virtual_classroom_day_layout(
            schedule.state, lambda cls, r=room: classroom_of(cls) == r)
        starts = {
            (block["row"], block["subcolumn"]): block
            for block in layout["blocks"]
        }
        covered = set(layout["occupied_subcolumns"])

        for group in layout["day_groups"]:
            col_start = 2 + group["subcolumn_start"]
            col_end = col_start + group["lane_count"] - 1
            if group["lane_count"] > 1:
                ws.merge_cells(
                    start_row=1, start_column=col_start,
                    end_row=1, end_column=col_end,
                )
            for col in range(col_start, col_end + 1):
                cell = ws.cell(row=1, column=col)
                cell.font = day_hdr_font
                cell.fill = day_hdr_fill
                cell.alignment = day_hdr_align
                cell.border = grid_border
                ws.column_dimensions[get_column_letter(col)].width = 25
            ws.cell(row=1, column=col_start, value=tr(f"weekdays.{group['day']}"))

        ws.column_dimensions["A"].width = 12
        ws.row_dimensions[1].height = 30
        total_subcolumns = layout["total_subcolumns"]

        for ri, slot in enumerate(schedule.slots, start=2):
            tc = ws.cell(row=ri, column=1, value=slot)
            tc.font = time_font
            tc.fill = time_fill
            tc.alignment = time_align
            tc.border = grid_border
            ws.row_dimensions[ri].height = 70

            row_index = ri - 2
            for subcolumn in range(total_subcolumns):
                col = subcolumn + 2
                key = (row_index, subcolumn)
                cell = ws.cell(row=ri, column=col)
                if key in starts:
                    block = starts[key]
                    span = block["span"]
                    if span > 1:
                        ws.merge_cells(
                            start_row=ri, start_column=col,
                            end_row=ri + span - 1, end_column=col,
                        )
                    cell.value = _rich_cell_for_class(
                        block["cls"],
                        include_room=False,
                        include_targets=True,
                    )
                    bg_hex = _entry_bg_color({"cls": block["cls"]}, schedule.state)
                    cell.fill = PatternFill("solid", fgColor=bg_hex)
                    cell.alignment = cell_align
                    cell.border = grid_border
                    for off in range(span):
                        ws.cell(row=ri + off, column=col).border = grid_border
                elif key in covered:
                    cell.border = grid_border
                else:
                    cell.fill = empty_fill
                    cell.alignment = cell_align
                    cell.border = grid_border

    _write_grid_sheet(wb.active, tr("export.master_schedule"))

    for lecturer in schedule.lecturers:
        if not lecturer:
            continue
        safe_name = lecturer[:28]
        ws = wb.create_sheet(f"T_{safe_name}")
        _write_grid_sheet(ws, f"T_{safe_name}",
                          filter_fn=lambda e, l=lecturer: e["lecturer"] == l)

    physical_rooms = set(schedule.classrooms)
    for room in get_classroom_export_labels(schedule.classrooms, schedule.placed_classes()):
        safe_name = room[:28]
        ws = wb.create_sheet(f"R_{safe_name}")
        if room not in physical_rooms:
            _write_virtual_room_sheet(ws, f"R_{safe_name}", room)
        else:
            _write_grid_sheet(ws, f"R_{safe_name}",
                              filter_fn=lambda e, r=room: e["room"] == r)

    for year, branches in schedule.years.items():
        for branch in branches:
            safe_name = f"{year}_{branch}"[:28]
            ws = wb.create_sheet(f"B_{safe_name}")

            def branch_filter(e, y=year, b=branch):
                return any(t["year"] == y and t["branch"] == b
                           for t in e.get("targets", []))

            _write_grid_sheet(ws, f"B_{safe_name}", filter_fn=branch_filter)

    wb.save(filepath)

# CSV export

def _export_csv(schedule: FinalSchedule, filepath: str):
    """Export to a flat CSV file."""
    grid = schedule.build_grid()
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            tr("labels.day"),
            tr("labels.time"),
            tr("labels.class_code"),
            tr("labels.course"),
            tr("labels.lecturer"),
            tr("labels.classroom"),
            tr("labels.year"),
            tr("labels.branch"),
        ])
        for cls in schedule.placed_classes():
            room = classroom_of(cls)
            code = cls.get("class_code", "")
            for day, slot in occupied_slots_of(schedule.state, cls):
                targets = cls.get("targets", [])
                if targets:
                    for t in targets:
                        writer.writerow([
                            day, slot, code, cls["name"], cls["lecturer"],
                            room, t["year"], t["branch"],
                        ])
                else:
                    writer.writerow([
                        day, slot, code, cls["name"], cls["lecturer"],
                        room, "", "",
                    ])


# ── PDF export (optional) ───────────────────────────────────────────────────


def _pdf_rich_paragraph(cls, cell_style, include_room=False, include_targets=False):
    """Build a color-coded Paragraph for a class in a PDF cell."""
    from reportlab.platypus import Paragraph

    parts = []
    code = cls.get("class_code", "")
    if code:
        parts.append(f'<font color="#1D4ED8" size="7"><b>{code}</b></font>')
    parts.append(f'<font color="#1E293B" size="8"><b>{cls["name"]}</b></font>')
    if cls.get("lecturer"):
        parts.append(f'<font color="#475569" size="7">{cls["lecturer"]}</font>')
    if include_room:
        room = classroom_of(cls)
        if room:
            parts.append(f'<font color="#16A34A" size="7">{room}</font>')
    if include_targets:
        groups = ", ".join(
            f"{t['year']}/{t['branch']}" for t in cls.get("targets", []))
        if groups:
            parts.append(f'<font color="#6D28D9" size="6">{groups}</font>')
    # Protection badge
    emoji, label, b_color = get_badge(cls)
    if emoji:
        parts.append(
            f'<font color="{b_color}" size="6"><b>{label}</b></font>')
    return Paragraph("<br/>".join(parts), cell_style)


def _export_pdf(schedule: FinalSchedule, filepath: str, mode: str = "everything"):
    """Export to PDF using reportlab, matching the timetable layout exactly.

    Supports four modes mirroring the Excel export:
    - "everything": one page per year with branch sub-columns and merged cells
    - "classroom":  one page per classroom
    - "group":      one page per year/branch combination
    - "lecturer":   one page per lecturer
    """
    try:
        from reportlab.lib import colors as rl_colors
        from reportlab.lib.pagesizes import A3, landscape
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak,
        )
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER
    except ImportError:
        raise RuntimeError(tr("errors.reportlab_required"))

    state = schedule.state
    placed = schedule.placed_classes()
    days = schedule.days
    slots = schedule.slots

    page_size = landscape(A3)
    doc = SimpleDocTemplate(
        filepath, pagesize=page_size,
        leftMargin=8 * mm, rightMargin=8 * mm,
        topMargin=8 * mm, bottomMargin=8 * mm,
    )
    avail_w = page_size[0] - 16 * mm

    # ── Paragraph styles ──────────────────────────────────────────────
    cell_style = ParagraphStyle(
        "CellContent", fontSize=7, leading=9,
        alignment=TA_CENTER, fontName="Helvetica",
    )
    hdr_style = ParagraphStyle(
        "HdrContent", fontSize=9, leading=11, alignment=TA_CENTER,
        textColor=rl_colors.white, fontName="Helvetica-Bold",
    )
    branch_hdr_style = ParagraphStyle(
        "BranchHdr", fontSize=8, leading=10, alignment=TA_CENTER,
        textColor=rl_colors.white, fontName="Helvetica-Bold",
    )
    time_style = ParagraphStyle(
        "TimeContent", fontSize=8, leading=10, alignment=TA_CENTER,
        textColor=rl_colors.white, fontName="Helvetica-Bold",
    )
    session_style = ParagraphStyle(
        "SessionNum", fontSize=8, leading=10, alignment=TA_CENTER,
        textColor=rl_colors.HexColor("#333333"), fontName="Helvetica-Bold",
    )
    title_style = ParagraphStyle(
        "PageTitle", fontSize=11, leading=14, alignment=TA_CENTER,
        fontName="Helvetica-Bold", textColor=rl_colors.HexColor("#1E293B"),
        spaceAfter=4 * mm,
    )

    # ── Color constants matching the app UI ───────────────────────────
    COL_DAY_HDR = rl_colors.HexColor("#334155")
    COL_BRANCH_HDR = rl_colors.HexColor("#475569")

    COL_CORNER = rl_colors.HexColor("#94A3B8")
    COL_TIME = rl_colors.HexColor("#475569")
    COL_SESSION = rl_colors.HexColor("#F1F5F9")
    COL_EMPTY = rl_colors.HexColor("#F8FAFC")
    COL_GRID = rl_colors.HexColor("#CBD5E1")

    MIN_ROW_H = 50  # minimum data-row height in points

    # ── Helper: build a filtered-sheet table (classroom / group / lecturer) ──

    def _build_filtered_table(filter_fn, include_room=False,
                              include_targets=False):
        """Build a reportlab Table for a simple Day×Slot grid."""
        n_days = len(days)
        time_w = 18 * mm
        day_w = (avail_w - time_w) / max(n_days, 1)
        col_widths = [time_w] + [day_w] * n_days

        # Header row
        header = [Paragraph(tr("labels.time"), hdr_style)]
        for day in days:
            header.append(Paragraph(tr(f"weekdays.{day}"), hdr_style))

        # Build occupancy map: (slot_index, day) -> (cls, span) or "covered"
        occupied_start = {}
        covered = set()
        for cls in placed:
            if not filter_fn(cls):
                continue
            c_day = effective_day(cls)
            c_start = effective_time(cls)
            if c_day not in days or c_start not in slots:
                continue
            start_si = slots.index(c_start)
            span = min(total_duration(cls), len(slots) - start_si)
            if span <= 0:
                continue
            key = (start_si, c_day)
            if key in occupied_start:
                continue
            occupied_start[key] = (cls, span)
            for off in range(span):
                covered.add((start_si + off, c_day))

        table_data = [header]
        style_cmds = [
            # Header row styling
            ("BACKGROUND", (0, 0), (-1, 0), COL_DAY_HDR),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, COL_GRID),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ]
        cell_bg_cmds = []
        row_heights = [24]  # header row

        for si, slot in enumerate(slots):
            data_row_idx = si + 1
            row = [Paragraph(slot, time_style)]
            style_cmds.append(
                ("BACKGROUND", (0, data_row_idx), (0, data_row_idx), COL_TIME))

            for di, day in enumerate(days):
                col_idx = di + 1
                key = (si, day)
                if key in occupied_start:
                    cls, span = occupied_start[key]
                    row.append(_pdf_rich_paragraph(
                        cls, cell_style,
                        include_room=include_room,
                        include_targets=include_targets))
                    yr_name = cls["targets"][0]["year"] if cls.get("targets") else ""
                    base = get_year_color(state, yr_name)
                    light = lighten_color(base, 0.45)
                    cell_bg_cmds.append(
                        ("BACKGROUND", (col_idx, data_row_idx),
                         (col_idx, data_row_idx), rl_colors.HexColor(light)))
                    if span > 1:
                        style_cmds.append(
                            ("SPAN", (col_idx, data_row_idx),
                             (col_idx, data_row_idx + span - 1)))
                        for off in range(span):
                            cell_bg_cmds.append(
                                ("BACKGROUND",
                                 (col_idx, data_row_idx + off),
                                 (col_idx, data_row_idx + off),
                                 rl_colors.HexColor(light)))
                elif key in covered:
                    row.append("")
                else:
                    row.append("")
                    cell_bg_cmds.append(
                        ("BACKGROUND", (col_idx, data_row_idx),
                         (col_idx, data_row_idx), COL_EMPTY))
            table_data.append(row)
            row_heights.append(MIN_ROW_H)

        # Default empty bg first, then override with lesson colors
        style_cmds.extend(cell_bg_cmds)

        tbl = Table(table_data, colWidths=col_widths, rowHeights=row_heights,
                    repeatRows=1)
        tbl.setStyle(TableStyle(style_cmds))
        return tbl

    def _build_virtual_room_table(room):
        """Build a virtual-classroom table with fixed day subcolumns."""
        layout = build_virtual_classroom_day_layout(
            state, lambda cls, r=room: classroom_of(cls) == r)
        total_subcolumns = layout["total_subcolumns"]
        time_w = 18 * mm
        data_w = (avail_w - time_w) / max(total_subcolumns, 1)
        col_widths = [time_w] + [data_w] * total_subcolumns

        header = [Paragraph(tr("labels.time"), hdr_style)]
        for group in layout["day_groups"]:
            header.append(Paragraph(tr(f"weekdays.{group['day']}"), hdr_style))
            header.extend([""] * (group["lane_count"] - 1))

        starts = {
            (block["row"], block["subcolumn"]): block
            for block in layout["blocks"]
        }
        covered = set(layout["occupied_subcolumns"])

        table_data = [header]
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), COL_DAY_HDR),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, COL_GRID),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ]
        for group in layout["day_groups"]:
            col_start = 1 + group["subcolumn_start"]
            col_end = col_start + group["lane_count"] - 1
            if group["lane_count"] > 1:
                style_cmds.append(("SPAN", (col_start, 0), (col_end, 0)))

        cell_bg_cmds = []
        row_heights = [24]
        for si, slot in enumerate(slots):
            data_row_idx = si + 1
            row = [Paragraph(slot, time_style)]
            style_cmds.append(
                ("BACKGROUND", (0, data_row_idx), (0, data_row_idx), COL_TIME))

            for subcolumn in range(total_subcolumns):
                col_idx = subcolumn + 1
                key = (si, subcolumn)
                if key in starts:
                    block = starts[key]
                    cls = block["cls"]
                    span = block["span"]
                    row.append(_pdf_rich_paragraph(
                        cls, cell_style,
                        include_room=False, include_targets=True))
                    yr_name = cls["targets"][0]["year"] if cls.get("targets") else ""
                    base = get_year_color(state, yr_name)
                    light = lighten_color(base, 0.45)
                    cell_bg_cmds.append(
                        ("BACKGROUND", (col_idx, data_row_idx),
                         (col_idx, data_row_idx), rl_colors.HexColor(light)))
                    if span > 1:
                        style_cmds.append(
                            ("SPAN", (col_idx, data_row_idx),
                             (col_idx, data_row_idx + span - 1)))
                        for off in range(span):
                            cell_bg_cmds.append(
                                ("BACKGROUND",
                                 (col_idx, data_row_idx + off),
                                 (col_idx, data_row_idx + off),
                                 rl_colors.HexColor(light)))
                elif key in covered:
                    row.append("")
                else:
                    row.append("")
                    cell_bg_cmds.append(
                        ("BACKGROUND", (col_idx, data_row_idx),
                         (col_idx, data_row_idx), COL_EMPTY))
            table_data.append(row)
            row_heights.append(MIN_ROW_H)

        style_cmds.extend(cell_bg_cmds)
        tbl = Table(table_data, colWidths=col_widths, rowHeights=row_heights,
                    repeatRows=1)
        tbl.setStyle(TableStyle(style_cmds))
        return tbl

    # ── Helper: build "everything" table for one year ─────────────────

    def _build_everything_table(yr):
        """Build the matrix-layout table for a single year (like the UI)."""
        branches = state["years"][yr]
        n_branches = len(branches)
        n_days = len(days)

        # Column widths: session# | time | (branches × days)
        session_w = 8 * mm
        time_w = 16 * mm
        data_cols = n_days * n_branches
        data_col_w = (avail_w - session_w - time_w) / max(data_cols, 1)
        col_widths = [session_w, time_w] + [data_col_w] * data_cols

        # Row 0: corner (merged 2 rows) + day headers (merged across branches)
        row0 = ["", Paragraph(tr("labels.time"), hdr_style)]
        for day in days:
            row0.append(Paragraph(tr(f"weekdays.{day}"), hdr_style))
            row0.extend([""] * (n_branches - 1))

        # Row 1: empty corners + branch sub-headers
        row1 = ["", ""]
        for _d in range(n_days):
            for br in branches:
                row1.append(Paragraph(br, branch_hdr_style))

        table_data = [row0, row1]

        style_cmds = [
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, COL_GRID),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            # Corner cells: merge rows 0-1 for session# and time columns
            ("SPAN", (0, 0), (0, 1)),
            ("SPAN", (1, 0), (1, 1)),
            ("BACKGROUND", (0, 0), (0, 1), COL_CORNER),
            ("BACKGROUND", (1, 0), (1, 1), COL_CORNER),
        ]

        # Day header merges and background
        for d_idx in range(n_days):
            col_start = 2 + d_idx * n_branches
            col_end = col_start + n_branches - 1
            if n_branches > 1:
                style_cmds.append(
                    ("SPAN", (col_start, 0), (col_end, 0)))
            for c in range(col_start, col_end + 1):
                style_cmds.append(
                    ("BACKGROUND", (c, 0), (c, 0), COL_DAY_HDR))
                style_cmds.append(
                    ("BACKGROUND", (c, 1), (c, 1), COL_BRANCH_HDR))

        # Build occupancy map: (slot_idx, day_idx, branch_idx) -> (kind, cls, span)
        occupied = {}
        for c in placed:
            c_day = effective_day(c)
            c_start = effective_time(c)
            if c_day not in days or c_start not in slots:
                continue
            d_idx = days.index(c_day)
            start_si = slots.index(c_start)
            dur = c["duration"]
            for t in c["targets"]:
                if t["year"] != yr or t["branch"] not in branches:
                    continue
                b_idx = branches.index(t["branch"])
                t_idx = c["targets"].index(t)
                slot_off = slot_offset_for_target(c, t_idx)
                actual_start = start_si + slot_off
                for d_off in range(dur):
                    si = actual_start + d_off
                    if si >= len(slots):
                        break
                    okey = (si, d_idx, b_idx)
                    if d_off == 0:
                        span = min(dur, len(slots) - actual_start)
                        occupied[okey] = ("start", c, span)
                    else:
                        occupied[okey] = ("span", None, 0)

        cell_bg_cmds = []
        row_heights = [24, 18]  # header rows

        for si, slot in enumerate(slots):
            data_row = si + 2  # after 2 header rows
            row = [
                Paragraph(str(si + 1), session_style),
                Paragraph(slot, time_style),
            ]
            style_cmds.append(
                ("BACKGROUND", (0, data_row), (0, data_row), COL_SESSION))
            style_cmds.append(
                ("BACKGROUND", (1, data_row), (1, data_row), COL_TIME))

            for d_idx in range(n_days):
                for b_idx in range(n_branches):
                    col = 2 + d_idx * n_branches + b_idx
                    okey = (si, d_idx, b_idx)
                    if okey in occupied:
                        kind, cls, span = occupied[okey]
                        if kind == "span":
                            row.append("")
                            continue
                        row.append(_pdf_rich_paragraph(
                            cls, cell_style,
                            include_room=True, include_targets=False))
                        yr_hex = get_year_color(state, yr).lstrip("#")
                        light_hex = lighten_color(f"#{yr_hex}", 0.45)
                        bg = rl_colors.HexColor(light_hex)
                        cell_bg_cmds.append(
                            ("BACKGROUND", (col, data_row),
                             (col, data_row), bg))
                        if span > 1:
                            style_cmds.append(
                                ("SPAN", (col, data_row),
                                 (col, data_row + span - 1)))
                            for off in range(1, span):
                                cell_bg_cmds.append(
                                    ("BACKGROUND",
                                     (col, data_row + off),
                                     (col, data_row + off), bg))
                    else:
                        row.append("")
                        cell_bg_cmds.append(
                            ("BACKGROUND", (col, data_row),
                             (col, data_row), COL_EMPTY))

            table_data.append(row)
            row_heights.append(MIN_ROW_H)

        style_cmds.extend(cell_bg_cmds)

        tbl = Table(table_data, colWidths=col_widths, rowHeights=row_heights,
                    repeatRows=2)
        tbl.setStyle(TableStyle(style_cmds))
        return tbl

    # ── Assemble pages ────────────────────────────────────────────────
    elements = []

    if mode == "everything":
        for yr in sorted(schedule.years.keys()):
            branches = schedule.years[yr]
            if not branches:
                continue
            if elements:
                elements.append(PageBreak())
            elements.append(
                Paragraph(f"{yr}", title_style))
            elements.append(_build_everything_table(yr))

    elif mode == "classroom":
        physical_rooms = set(schedule.classrooms)
        for room in get_classroom_export_labels(schedule.classrooms, placed):
            if elements:
                elements.append(PageBreak())
            elements.append(
                Paragraph(room, title_style))
            if room not in physical_rooms:
                elements.append(_build_virtual_room_table(room))
            else:
                elements.append(_build_filtered_table(
                    filter_fn=lambda c, r=room: classroom_of(c) == r,
                    include_room=False, include_targets=True))
    elif mode == "group":
        for yr in sorted(schedule.years.keys()):
            for br in schedule.years[yr]:
                if elements:
                    elements.append(PageBreak())
                elements.append(
                    Paragraph(f"{yr} / {br}", title_style))
                elements.append(_build_filtered_table(
                    filter_fn=lambda c, y=yr, b=br: any(
                        t["year"] == y and t["branch"] == b
                        for t in c.get("targets", [])),
                    include_room=True, include_targets=False))

    elif mode == "lecturer":
        lecturers = schedule.lecturers
        if not lecturers:
            lecturers = sorted({
                c.get("lecturer", "") for c in placed if c.get("lecturer")})
        for lecturer in lecturers:
            if not lecturer:
                continue
            if elements:
                elements.append(PageBreak())
            elements.append(
                Paragraph(lecturer, title_style))
            elements.append(_build_filtered_table(
                filter_fn=lambda c, l=lecturer: c.get("lecturer", "") == l,
                include_room=True, include_targets=True))

    if not elements:
        elements.append(Paragraph(tr("warnings.no_schedule_data"), title_style))

    doc.build(elements)


# ── Public entry point ──────────────────────────────────────────────────────

def export_schedule(schedule, format: str, filepath: str, mode: str = "everything"):
    """Export a schedule to the specified format.

    Args:
        schedule: Either a FinalSchedule instance or a state dict.
        format: One of 'xlsx', 'csv', 'pdf'.
        filepath: Output file path.
        mode: PDF/Excel layout mode – 'everything', 'classroom',
              'group', or 'lecturer'.
    """
    if isinstance(schedule, dict):
        schedule = FinalSchedule(schedule)

    fmt = format.lower().strip(".")
    if fmt in ("xlsx", "excel"):
        _export_excel(schedule, filepath)
    elif fmt == "csv":
        _export_csv(schedule, filepath)
    elif fmt == "pdf":
        _export_pdf(schedule, filepath, mode=mode)
    else:
        raise ValueError(
            tr("errors.unsupported_format").format(
                fmt=repr(format)))

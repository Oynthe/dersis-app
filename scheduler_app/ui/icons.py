"""Icon helper: generates QIcon instances from painted pixmaps or PNG files.

Uses PNG flag files from the flags/ directory for flag icons, and simple
QPainter shapes for other icons.
"""

import os
import tempfile

from PyQt6.QtGui import (
    QPixmap, QPainter, QColor, QFont, QPen, QBrush, QIcon, QPolygonF,
    QPainterPath,
)
from PyQt6.QtCore import Qt, QRect, QRectF, QPointF

# ── PNG flag icon loader ─────────────────────────────────────────────────

_FLAGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "flags")


def _png_flag_icon(png_filename, size=22):
    """Create a flag QIcon from a PNG file in the flags/ directory."""
    png_path = os.path.join(_FLAGS_DIR, png_filename)
    source = QPixmap(png_path)
    pm = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    return QIcon(pm)


# ── Arrow pixmaps for stylesheet image: url(...) ──────────────────────────

_arrow_dir = None


def _ensure_arrow_dir():
    """Create temp directory with arrow PNG files for stylesheet use."""
    global _arrow_dir
    if _arrow_dir is not None:
        return _arrow_dir

    _arrow_dir = os.path.join(tempfile.gettempdir(), "scheduler_arrows")
    os.makedirs(_arrow_dir, exist_ok=True)

    color = QColor("#475569")

    # Down arrow (for QComboBox and QSpinBox)
    pm = QPixmap(12, 12)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPolygon(QPolygonF([QPointF(2, 3), QPointF(10, 3), QPointF(6, 10)]))
    p.end()
    pm.save(os.path.join(_arrow_dir, "down.png"))

    # Up arrow (for QSpinBox)
    pm = QPixmap(12, 12)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPolygon(QPolygonF([QPointF(2, 10), QPointF(10, 10), QPointF(6, 3)]))
    p.end()
    pm.save(os.path.join(_arrow_dir, "up.png"))

    # Left arrow (for QTabBar scroll)
    pm = QPixmap(12, 12)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPolygon(QPolygonF([QPointF(9, 2), QPointF(9, 10), QPointF(3, 6)]))
    p.end()
    pm.save(os.path.join(_arrow_dir, "left.png"))

    # Right arrow (for QTabBar scroll)
    pm = QPixmap(12, 12)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPolygon(QPolygonF([QPointF(3, 2), QPointF(3, 10), QPointF(9, 6)]))
    p.end()
    pm.save(os.path.join(_arrow_dir, "right.png"))

    return _arrow_dir


def get_arrow_dir():
    """Return path to arrow images directory, creating if needed."""
    return _ensure_arrow_dir()


def _make_icon(size, draw_fn):
    """Create a QIcon by painting onto a transparent pixmap."""
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    draw_fn(p, size)
    p.end()
    return QIcon(pm)


def _text_icon(char, color, size=32, font_size=18):
    """Create an icon from a single Unicode character."""
    def draw(p, s):
        f = QFont("Segoe UI", font_size)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(QRect(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, char)
    return _make_icon(size, draw)


def icon_add_class():
    """Plus symbol in a rounded square — Add Class."""
    def draw(p, s):
        # Rounded background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#3B82F6")))
        p.drawRoundedRect(2, 2, s - 4, s - 4, 6, 6)
        # Plus sign
        p.setPen(QPen(QColor("white"), 3))
        mid = s // 2
        p.drawLine(mid, 8, mid, s - 8)
        p.drawLine(8, mid, s - 8, mid)
    return _make_icon(32, draw)


def icon_placement():
    """Grid with pin — Class Placement."""
    def draw(p, s):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#8B5CF6")))
        p.drawRoundedRect(2, 2, s - 4, s - 4, 6, 6)
        # Grid lines
        p.setPen(QPen(QColor("white"), 1.5))
        m = 7
        # Horizontal
        p.drawLine(m, s // 3, s - m, s // 3)
        p.drawLine(m, 2 * s // 3, s - m, 2 * s // 3)
        # Vertical
        p.drawLine(s // 3, m, s // 3, s - m)
        p.drawLine(2 * s // 3, m, 2 * s // 3, s - m)
    return _make_icon(32, draw)


def icon_reschedule():
    """Circular arrows — Reschedule."""
    def draw(p, s):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#059669")))
        p.drawRoundedRect(2, 2, s - 4, s - 4, 6, 6)
        # Circular arrow (simplified)
        p.setPen(QPen(QColor("white"), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRect(7, 7, s - 14, s - 14), 30 * 16, 300 * 16)
        # Arrowhead
        p.setPen(QPen(QColor("white"), 2))
        ax, ay = s - 10, 9
        p.drawLine(int(ax), int(ay), int(ax + 5), int(ay))
        p.drawLine(int(ax), int(ay), int(ax), int(ay + 5))
    return _make_icon(32, draw)


def icon_setup():
    """Gear — Setup."""
    def draw(p, s):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#64748B")))
        p.drawRoundedRect(2, 2, s - 4, s - 4, 6, 6)
        # Gear (simplified as circle with notches)
        mid = s // 2
        p.setPen(QPen(QColor("white"), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRect(9, 9, s - 18, s - 18))
        # Center dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("white")))
        p.drawEllipse(QRect(mid - 3, mid - 3, 6, 6))
        # Notches
        p.setPen(QPen(QColor("white"), 2.5))
        import math
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            x1 = mid + int(9 * math.cos(rad))
            y1 = mid + int(9 * math.sin(rad))
            x2 = mid + int(12 * math.cos(rad))
            y2 = mid + int(12 * math.sin(rad))
            p.drawLine(x1, y1, x2, y2)
    return _make_icon(32, draw)


def icon_new():
    return _text_icon("\u2795", "#3B82F6", 24, 12)


def icon_open():
    return _text_icon("\U0001F4C2", "#475569", 24, 12)


def icon_save():
    return _text_icon("\U0001F4BE", "#475569", 24, 12)


def icon_export():
    return _text_icon("\u21D7", "#475569", 24, 14)


# Menu item icons (smaller, simpler)
def icon_add_single():
    return _text_icon("+", "#3B82F6", 24, 16)


def icon_bulk_add():
    return _text_icon("++", "#3B82F6", 24, 12)


def icon_place():
    return _text_icon("\u25BC", "#8B5CF6", 24, 14)


def icon_unplace():
    return _text_icon("\u25B2", "#F59E0B", 24, 14)


def icon_delete():
    return _text_icon("\u2715", "#EF4444", 24, 14)


def icon_edit():
    return _text_icon("\u270E", "#475569", 24, 14)


# ── Flag icons (PNG-based) ─────────────────────────────────────────────


def flag_gb():
    return _png_flag_icon("united-kingdom-206592.png")


def flag_tr():
    return _png_flag_icon("turkey-206634.png")


def flag_de():
    return _png_flag_icon("germany-206690.png")


def flag_fr():
    return _png_flag_icon("france-206657.png")


def flag_es():
    return _png_flag_icon("spain-206724.png")


def flag_cn():
    return _png_flag_icon("china-206818.png")


def flag_ru():
    return _png_flag_icon("russia-206604.png")


def flag_br():
    return _png_flag_icon("brazil-206597.png")


def flag_se():
    return _png_flag_icon("sweden-206668.png")


def flag_dk():
    return _png_flag_icon("denmark-206678.png")


def flag_it():
    return _png_flag_icon("italy-206839.png")


def flag_nl():
    return _png_flag_icon("netherlands-206615.png")


def flag_pl():
    return _png_flag_icon("poland-206641.png")


def flag_in():
    return _png_flag_icon("india-206606.png")


def flag_id():
    return _png_flag_icon("indonesia-206643.png")


def flag_az():
    return _png_flag_icon("azerbaijan-206711.png")


def flag_ir():
    return _png_flag_icon("iran-206716.png")


def flag_sa():
    return _png_flag_icon("saudi-arabia-206719.png")


def flag_za():
    return _png_flag_icon("south-africa-206652.png")


def flag_jp():
    return _png_flag_icon("japan-206789.png")


def flag_kr():
    return _png_flag_icon("south-korea-206758.png")


def flag_pt():
    return _png_flag_icon("portugal-206628.png")

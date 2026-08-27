"""Probe 6 (GUI): HTML injection into the WarningLogPanel.

widgets.WarningLogPanel.log() builds  f'<span style="color:{c}">{msg}</span>'
and calls QTextEdit.setHtml(...) WITHOUT escaping msg (widgets.py:235-236).
app.py:2964 _refresh_warnings feeds year/branch labels straight into .log(),
so a year or branch named with markup lands in that HTML unescaped.

This probe constructs the widget headlessly (native platform, no show()),
logs adversarial messages, and reads the QTextEdit back to CONFIRM that the
raw markup is present in the document rather than escaped as text.
"""
import os
import sys
import tempfile

SANDBOX = tempfile.mkdtemp(prefix="dersis_eh_gui_")
os.environ["HOME"] = SANDBOX
os.environ["USERPROFILE"] = SANDBOX
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

EVID = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evidence")
os.makedirs(EVID, exist_ok=True)


def sa(x):
    """ASCII-safe repr for a cp1254 console."""
    return repr(x).encode("ascii", "backslashreplace").decode("ascii")


def main():
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from scheduler_app.translations import set_language
    set_language("tr")
    from scheduler_app.ui.widgets import WarningLogPanel

    panel = WarningLogPanel()

    payloads = {
        "img_onerror": "Y<img src=x onerror=alert(1)>/A: overload",
        "bold_break": "Yr<b>BOLD</b>/A <br> injected-break",
        "href": "Yr/A <a href='file:///C:/windows/system32'>link</a>",
        "entity": "Yr & <B> raw ampersand and tag",
    }
    for kind, msg in payloads.items():
        panel.log(msg, "warning")

    # Read back the rich-text document the panel produced
    doc_html = panel._log_area.toHtml()
    plain = panel._log_area.toPlainText()

    print("=" * 70)
    print("WarningLogPanel HTML injection check")
    print("=" * 70)

    # 1) Did the <img>/<b>/<a> tags get parsed as MARKUP (injection) or kept
    #    as literal text (safe)?  If the plain text still contains the literal
    #    '<img' then it was escaped/safe; if it's gone from plain text but the
    #    structure changed, it was interpreted as a tag.
    img_literal_in_plain = "<img" in plain
    b_literal_in_plain = "<b>BOLD</b>".lower() in plain.lower() or "<b>" in plain
    print(f"  plain text preserves literal '<img'? {img_literal_in_plain}  "
          f"(False => tag was interpreted as MARKUP, i.e. injected)")
    print(f"  plain text preserves literal '<b>'?  {b_literal_in_plain}")
    print(f"  rendered plain text sample: {sa(plain)}")

    # 2) Direct proof at the source: reconstruct exactly what log() builds and
    #    show it is NOT escaped before setHtml.
    msg = payloads["img_onerror"]
    built = f'<span style="color:#92400E">{msg}</span>'
    from html import escape
    print()
    print("  Source construction in widgets.py WarningLogPanel.log():")
    print("    built  = " + sa(built))
    print("    safe   = " + sa(escape(msg)))
    print(f"    escaped==built? {escape(msg) in built}  "
          f"(False => raw markup passed to setHtml -> INJECTION CONFIRMED)")

    # 3) Show the full document HTML actually contains a real <img> element
    has_img_element = "<img" in doc_html
    has_bold_element = "font-weight" in doc_html and "BOLD" in doc_html
    print()
    print(f"  QTextEdit document contains a real <img ...> element: {has_img_element}")
    print(f"  QTextEdit rendered <b>BOLD</b> as bold run: {has_bold_element}")

    with open(os.path.join(EVID, "warning_injection_doc.html"), "w", encoding="utf-8") as f:
        f.write(doc_html)
    print(f"  wrote rendered document -> evidence/warning_injection_doc.html")

    verdict = ("INJECTION CONFIRMED (markup interpreted, not escaped)"
               if (not img_literal_in_plain or has_img_element)
               else "safe (escaped)")
    print(f"\n  VERDICT: {verdict}")

    panel.deleteLater()
    app.quit()
    print("DONE probe_warning_html_injection")


if __name__ == "__main__":
    main()

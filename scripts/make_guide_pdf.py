"""Render docs/GUIDA_UTENTE.md to a PDF with Qt (no extra dependencies).

    python scripts/make_guide_pdf.py [output.pdf]
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QMarginsF, QSizeF
from PySide6.QtGui import QFont, QGuiApplication, QPageLayout, QPageSize, QPdfWriter, QTextDocument

ROOT = Path(__file__).resolve().parents[1]
CSS = """
body { font-family: 'Segoe UI', 'Inter', 'DejaVu Sans', sans-serif; font-size: 10pt; color: #1b1d24; }
h1 { font-size: 22pt; color: #4b3bd6; margin-bottom: 4px; }
h2 { font-size: 15pt; color: #4b3bd6; margin-top: 18px; border-bottom: 1px solid #c9c4f5; }
h3 { font-size: 12pt; color: #2b2f3a; margin-top: 12px; }
table { border-collapse: collapse; margin: 6px 0; }
th { background: #ece9ff; padding: 4px 6px; border: 1px solid #c9c4f5; }
td { padding: 4px 6px; border: 1px solid #d9dbe3; vertical-align: top; }
code, pre { font-family: 'Consolas', 'DejaVu Sans Mono', monospace; background: #f3f4f7; }
blockquote { color: #6a4b00; background: #fff6df; margin: 6px 0; padding: 6px; }
"""


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "Guida_DJ_Director_AI.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    md = (ROOT / "docs" / "GUIDA_UTENTE.md").read_text("utf-8")
    doc = QTextDocument()
    doc.setDefaultStyleSheet(CSS)
    doc.setDefaultFont(QFont("Segoe UI", 10))
    doc.setMarkdown(md)
    # setMarkdown ignores the default stylesheet for some elements: re-apply via HTML round trip
    html = doc.toHtml()
    doc2 = QTextDocument()
    doc2.setDefaultStyleSheet(CSS)
    doc2.setHtml(html)
    writer = QPdfWriter(str(out))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageMargins(QMarginsF(16, 16, 16, 16), QPageLayout.Millimeter)
    writer.setResolution(300)
    writer.setTitle("DJ Director AI — Guida all'utilizzo")
    writer.setCreator("DJ Director AI")
    doc2.print_(writer)        # QTextDocument paginates to the writer's page layout
    print(f"guide written to {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

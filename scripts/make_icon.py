"""Render the application icon (PNG + multi-size ICO) with Qt. Run once; outputs are committed."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter

app = QGuiApplication(sys.argv)
out = Path(__file__).resolve().parents[1] / "src" / "dj_director" / "ui" / "assets"
out.mkdir(parents=True, exist_ok=True)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#8f81ff"))
    g.setColorAt(1, QColor("#4b3bd6"))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 56 * s, 56 * s)
    # three "camera tiles", the middle one on air
    for i, x in enumerate((40, 104, 168)):
        p.setBrush(QColor("#ff4d5e") if i == 1 else QColor(255, 255, 255, 210))
        p.drawRoundedRect(QRectF(x * s, 92 * s, 48 * s, 72 * s), 10 * s, 10 * s)
    # waveform bar underneath
    p.setBrush(QColor(255, 255, 255, 160))
    for i, h in enumerate((10, 22, 14, 30, 18, 26, 12, 20, 8)):
        p.drawRoundedRect(QRectF((52 + i * 18) * s, (196 - h / 2) * s, 8 * s, h * s), 3 * s, 3 * s)
    p.end()
    return img


render(256).save(str(out / "icon.png"))
render(256).save(str(out / "icon.ico"))
print("icon written to", out)

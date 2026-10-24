"""Custom widgets: cards, status pills, meters, camera tiles, timeline."""

from __future__ import annotations

import threading
from typing import Callable, Optional, Sequence

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget)

from dj_director.ui import theme as T


def label(text: str = "", obj: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if obj:
        lab.setObjectName(obj)
    lab.setWordWrap(wrap)
    return lab


class Card(QFrame):
    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(18, 16, 18, 16)
        self.lay.setSpacing(8)
        if title:
            self.lay.addWidget(label(title.upper(), "CardTitle"))


class Dot(QWidget):
    def __init__(self, color: str = T.FAINT, size: int = 10) -> None:
        super().__init__()
        self._color = QColor(color)
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        if QColor(color) != self._color:
            self._color = QColor(color)
            self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


class StatusPill(QLabel):
    def __init__(self, text: str = "OFF") -> None:
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.set_status(text, T.FAINT)

    def set_status(self, text: str, color: str) -> None:
        self.setText(text)
        self.setStyleSheet(f"QLabel {{ color: {color}; background: {T.rgba(color, 0.13)};"
                           f" border: 1px solid {T.rgba(color, 0.4)};"
                           f" border-radius: 12px; padding: 4px 14px; font-weight: 700; letter-spacing: 1px; }}")


class LevelMeter(QWidget):
    """Horizontal level meter in dBFS (-60..0) with peak hold and clip indicator."""

    def __init__(self, height: int = 12) -> None:
        super().__init__()
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.level = -120.0
        self.peak = -120.0
        self.clip = False
        self.floor = -60.0

    def set_values(self, level_db: float, peak_db: float, clip: bool) -> None:
        self.level, self.clip = level_db, clip
        self.peak = max(peak_db, self.peak - 1.5)
        self.update()

    def _x(self, db: float) -> float:
        return max(0.0, min(1.0, (db - self.floor) / -self.floor)) * self.width()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.SURFACE_3))
        p.drawRoundedRect(r, 5, 5)
        w = self._x(self.level)
        color = T.LIVE if self.clip else (T.WARN if self.level > -6 else T.OK)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(0, 0, w, r.height()), 5, 5)
        px = self._x(self.peak)
        p.setBrush(QColor(T.TEXT))
        p.drawRect(QRectF(max(0, px - 2), 0, 2, r.height()))


class Bar(QWidget):
    """Thin 0..1 bar (confidence, opportunity)."""

    def __init__(self, color: str = T.ACCENT, height: int = 6) -> None:
        super().__init__()
        self.setFixedHeight(height)
        self.value = 0.0
        self.color = color

    def set_value(self, v: float, color: Optional[str] = None) -> None:
        self.value = max(0.0, min(1.0, v))
        if color:
            self.color = color
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.SURFACE_3))
        p.drawRoundedRect(r, 3, 3)
        p.setBrush(QColor(self.color))
        p.drawRoundedRect(QRectF(0, 0, r.width() * self.value, r.height()), 3, 3)


class CameraTile(QFrame):
    def __init__(self, name: str, role: str, master: bool, color: str) -> None:
        super().__init__()
        self.name = name
        self.color = color
        self.setMinimumSize(150, 86)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        top = QHBoxLayout()
        self.badge = label("MASTER" if master else role, "CardTitle")
        top.addWidget(self.badge)
        top.addStretch()
        self.state = label("", "Faint")
        top.addWidget(self.state)
        lay.addLayout(top)
        self.title = label(name)
        f = self.title.font()
        f.setPointSizeF(11.5)
        f.setWeight(QFont.DemiBold)
        self.title.setFont(f)
        lay.addWidget(self.title)
        lay.addStretch()
        self.set_state(False, False, True, True)

    def set_state(self, on_air: bool, pending: bool, enabled: bool, available: bool) -> None:
        if on_air:
            border, bg, txt = T.LIVE, T.rgba(T.LIVE, 0.12), "ON AIR"
        elif pending:
            border, bg, txt = T.WARN, T.rgba(T.WARN, 0.08), "NEXT"
        elif not available:
            border, bg, txt = T.BORDER, T.SURFACE, "MISSING"
        elif not enabled:
            border, bg, txt = T.BORDER, T.SURFACE, "DISABLED"
        else:
            border, bg, txt = T.BORDER, T.SURFACE, ""
        self.state.setText(txt)
        self.state.setStyleSheet(f"color: {T.LIVE if on_air else T.WARN if pending else T.FAINT};"
                                 " font-weight: 700;")
        self.setStyleSheet(f"CameraTile {{ background: {bg}; border: 2px solid {border}; border-radius: 12px;"
                           f" border-left: 5px solid {self.color}; }}")


class TimelineWidget(QWidget):
    """Shot timeline (simulation results / session history)."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(110)
        self.shots: list[tuple[str, float, float]] = []
        self.colors: dict[str, str] = {}
        self.marks: list[tuple[float, str]] = []      # (t, kind) music events
        self.sections: list[tuple[str, float]] = []
        self.duration = 1.0

    def set_data(self, shots, duration: float, cameras: Sequence[str], marks=(), sections=()) -> None:
        self.shots = list(shots)
        self.duration = max(duration, 1.0)
        self.colors = {c: T.CAMERA_COLORS[i % len(T.CAMERA_COLORS)] for i, c in enumerate(cameras)}
        self.marks = list(marks)
        self.sections = list(sections)
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(T.SURFACE))
        if not self.shots:
            p.setPen(QColor(T.FAINT))
            p.drawText(self.rect(), Qt.AlignCenter, "Run a simulation to see the shot timeline")
            return
        sx = (w - 20) / self.duration
        top, bar_h = 26, h - 56
        for name, a, b in self.shots:
            r = QRectF(10 + a * sx, top, max(1.0, (b - a) * sx - 1), bar_h)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self.colors.get(name, T.FAINT)))
            p.drawRoundedRect(r, 3, 3)
        for t, kind in self.marks:
            x = 10 + t * sx
            col = {"reentry": T.LIVE, "transition": T.WARN, "sparse_start": T.INFO}.get(kind, T.MUTED)
            p.setPen(QPen(QColor(col), 2))
            p.drawLine(int(x), top - 8, int(x), top - 2)
        p.setPen(QColor(T.FAINT))
        step = 30 if self.duration < 600 else 60 if self.duration < 1800 else 300
        t = 0
        while t <= self.duration:
            x = 10 + t * sx
            p.drawLine(int(x), top + bar_h + 2, int(x), top + bar_h + 6)
            p.drawText(int(x) + 2, h - 8, f"{int(t // 60)}:{int(t % 60):02d}")
            t += step
        x = 10
        for name, col in self.colors.items():
            p.setBrush(QColor(col))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(x, 6, 10, 10), 2, 2)
            p.setPen(QColor(T.MUTED))
            p.drawText(int(x) + 14, 15, name)
            x += 24 + p.fontMetrics().horizontalAdvance(name)


class Worker(QObject):
    """Run a callable on a plain thread and deliver the result on the GUI thread."""

    done = Signal(object, object)   # result, exception
    progress = Signal(float)

    def run(self, fn: Callable, *args) -> None:
        def target():
            try:
                res = fn(*args)
                self.done.emit(res, None)
            except Exception as exc:
                self.done.emit(None, exc)
        threading.Thread(target=target, daemon=True, name="ui-worker").start()

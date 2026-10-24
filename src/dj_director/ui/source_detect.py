"""Automatic audio-source detection panel (used by the Audio page and the wizard)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QGroupBox, QHBoxLayout, QHeaderView, QProgressBar,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from dj_director.ui.i18n import tr, tr_decision, tr_reason, tr_state
from dj_director.audio.backends import DeviceInfo
from dj_director.audio.devices import BUILTIN, DJ_HARDWARE, LINE_INTERFACE, VIRTUAL, classify
from dj_director.ui import theme as T
from dj_director.ui.widgets import Worker, label

KIND_LABEL = {LINE_INTERFACE: "Line input", DJ_HARDWARE: "DJ hardware", BUILTIN: "Room mic",
              VIRTUAL: "Virtual", "unknown": "Other"}


class SourceDetectBox(QGroupBox):
    chosen = Signal(object)          # DeviceInfo

    def __init__(self, parent=None) -> None:
        super().__init__(tr("Automatic detection"), parent)
        v = QVBoxLayout(self)
        v.addWidget(label(tr("Play music on the mixer, then press Detect: every input and every speaker "
                          "endpoint (WASAPI loopback) is listened to for about a second and the best "
                          "source carrying the mix is suggested."), "Muted", wrap=True))
        row = QHBoxLayout()
        self.btn = QPushButton(tr("Detect audio source"))
        self.btn.setObjectName("Primary")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        row.addWidget(self.btn)
        row.addWidget(self.progress, 1)
        self.btn_use = QPushButton(tr("Use selected"))
        self.btn_use.setEnabled(False)
        row.addWidget(self.btn_use)
        v.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([tr("Device"), tr("Type"), tr("Level"), tr("Activity"), tr("Result")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setVisible(False)
        self.table.setMinimumHeight(180)
        v.addWidget(self.table)
        self.summary = label("", "Muted", wrap=True)
        v.addWidget(self.summary)
        self.results = []
        self._worker = Worker()
        self._worker.done.connect(self._done)
        self._worker.progress.connect(lambda f: self.progress.setValue(int(f * 100)))
        self.btn.clicked.connect(self.start)
        self.btn_use.clicked.connect(self._use)
        self.table.itemSelectionChanged.connect(lambda: self.btn_use.setEnabled(bool(self.table.selectedItems())))

    def start(self) -> None:
        from dj_director.audio.probe import probe_devices
        self.btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.summary.setText(tr("Listening to each device…"))
        w = self._worker
        self._worker.run(lambda: probe_devices(progress=lambda f, name: w.progress.emit(f)))

    def _done(self, results, exc) -> None:
        self.btn.setEnabled(True)
        self.progress.setVisible(False)
        if exc is not None:
            self.summary.setText(tr("Detection failed: {0}").format(exc))
            return
        self.results = results or []
        self.table.setRowCount(0)
        self.table.setVisible(bool(self.results))
        for r in self.results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            c = classify(r.device.name)
            kind = tr(KIND_LABEL.get(c.kind, "Other")) + (tr(" (speaker loopback)") if r.device.kind == "loopback" else "")
            if r.error:
                res, col = tr("error: {0}").format(r.error[:60]), T.LIVE
            elif r.has_signal:
                res, col = (tr("music detected") if r.activity > 0.3 else tr("signal")), T.OK
            else:
                res, col = tr("silent"), T.FAINT
            vals = (r.device.name, kind, f"{r.level_db:.0f} dBFS" if not r.error else "—",
                    f"{r.activity:.2f}" if not r.error else "—", res)
            for i, txt in enumerate(vals):
                it = QTableWidgetItem(txt)
                if i == 4:
                    it.setForeground(QColor(col))
                self.table.setItem(row, i, it)
        best = self.results[0] if self.results else None
        if best is not None and best.has_signal:
            self.table.selectRow(0)
            extra = ""
            if classify(best.device.name).kind == BUILTIN:
                extra = " " + tr("Warning: it looks like a room microphone; prefer a line input if you have one.")
            self.summary.setText(tr("Suggested: {0}.").format(best.device.name) + extra)
        elif self.results:
            self.summary.setText(tr("No device is receiving sound. Is music playing? Is the mixer output "
                                 "connected to the interface input? If the level is exactly zero, check "
                                 "Windows Settings › Privacy › Microphone (allow desktop apps)."))
        else:
            self.summary.setText(tr("No audio devices found."))

    def _use(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows and rows[0].row() < len(self.results):
            self.chosen.emit(self.results[rows[0].row()].device)

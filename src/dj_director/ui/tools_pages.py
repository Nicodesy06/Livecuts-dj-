"""Simulation page (offline replay + baselines) and Log/diagnostics page."""

from __future__ import annotations

import copy
import os
import subprocess
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLineEdit, QProgressBar, QPushButton,
                               QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from dj_director.ui.i18n import tr, tr_action, tr_decision, tr_reason, tr_state
from dj_director.config.paths import log_dir
from dj_director.core.controller import AppController, AppSnapshot
from dj_director.core.events import DirectorAction
from dj_director.ui import theme as T
from dj_director.ui.settings_pages import page_scaffold
from dj_director.ui.widgets import Card, TimelineWidget, Worker, label

METRIC_ROWS = [
    ("cuts_per_minute", "Cuts per minute"), ("master_share", "Time on master"),
    ("mean_shot_s", "Mean shot (s)"), ("median_shot_s", "Median shot (s)"),
    ("shortest_shot_s", "Shortest shot (s)"), ("longest_shot_s", "Longest shot (s)"),
    ("duration_cv", "Shot length variation (CV)"), ("abab_patterns", "A-B-A-B patterns"),
    ("min_duration_violations", "Min-length violations"), ("cooldown_violations", "Cooldown violations"),
    ("max_consecutive_inserts", "Max consecutive inserts"), ("beat_synced_cuts", "Beat-synced cuts"),
    ("beat_alignment_median_ms", "Beat alignment, median (ms)"),
]


class SimulationPage(QWidget):
    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        page, v = page_scaffold(tr("Simulation"), tr("Replay a recorded set (or a synthetic one) through the real "
                                "analysis and director, faster than real time, without OBS or cameras. "
                                "Compare with a fixed-timer switcher and a random switcher."))
        QVBoxLayout(self).addWidget(page)
        self.layout().setContentsMargins(0, 0, 0, 0)
        box = QGroupBox(tr("Input"))
        f = QFormLayout(box)
        srow = QHBoxLayout()
        self.r_file = QRadioButton(tr("Audio file"))
        self.r_synth = QRadioButton(tr("Synthetic DJ set"))
        self.r_synth.setChecked(True)
        srow.addWidget(self.r_file)
        srow.addWidget(self.r_synth)
        srow.addStretch()
        f.addRow(tr("Source"), srow)
        frow = QHBoxLayout()
        self.path = QLineEdit()
        b = QPushButton(tr("Browse…"))
        b.clicked.connect(self._browse)
        frow.addWidget(self.path, 1)
        frow.addWidget(b)
        f.addRow(tr("File"), frow)
        self.bpm = QDoubleSpinBox(); self.bpm.setRange(70, 180); self.bpm.setValue(124)
        f.addRow(tr("Synthetic tempo"), self.bpm)
        self.preset = QComboBox()
        for name in ("natural", "minimal", "dynamic"):
            self.preset.addItem(tr(name.capitalize()), name)
        f.addRow(tr("Preset"), self.preset)
        f.addRow("", label(tr("Uses your configured cameras (or 3 demo cameras if none are configured)."), "Faint"))
        v.addWidget(box)
        row = QHBoxLayout()
        self.btn_run = QPushButton(tr("Run simulation"))
        self.btn_run.setObjectName("Primary")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        row.addWidget(self.btn_run)
        row.addWidget(self.progress, 1)
        self.btn_json = QPushButton(tr("Export JSON"))
        self.btn_csv = QPushButton(tr("Export CSV"))
        self.btn_json.setEnabled(False)
        self.btn_csv.setEnabled(False)
        row.addWidget(self.btn_json)
        row.addWidget(self.btn_csv)
        v.addLayout(row)
        tl = Card(tr("Shot timeline  (marks: red = re-entry, amber = transition, blue = sparse start)"))
        self.timeline = TimelineWidget()
        self.timeline.setMinimumHeight(130)
        tl.lay.addWidget(self.timeline)
        v.addWidget(tl)
        self.table = QTableWidget(len(METRIC_ROWS), 3)
        self.table.setHorizontalHeaderLabels([tr("Music-aware director"), tr("Timer (12 s)"), tr("Random")])
        self.table.setVerticalHeaderLabels([tr(n) for _, n in METRIC_ROWS])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setMinimumHeight(420)
        v.addWidget(self.table)
        self.note = label(tr("Metrics describe behaviour, not artistic quality: watch the timeline and judge."),
                          "Faint", wrap=True)
        v.addWidget(self.note)
        self.shots = QTableWidget(0, 4)
        self.shots.setHorizontalHeaderLabels([tr("Start"), tr("Length"), tr("Camera"), tr("Why")])
        self.shots.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.shots.setMinimumHeight(260)
        v.addWidget(self.shots)
        self.result = None
        self._worker = Worker()
        self._worker.done.connect(self._done)
        self._worker.progress.connect(lambda f: self.progress.setValue(int(f * 100)))
        self.btn_run.clicked.connect(self.run)
        self.btn_json.clicked.connect(lambda: self._export("json"))
        self.btn_csv.clicked.connect(lambda: self._export("csv"))

    def _browse(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, tr("Recorded set"), "", "Audio (*.wav *.flac *.ogg *.aiff *.aif)")
        if p:
            self.path.setText(p)
            self.r_file.setChecked(True)

    def run(self) -> None:
        from dj_director.simulation.replay import default_cameras, load_audio, run_replay
        from dj_director.simulation.synth import generate
        cfg = copy.deepcopy(self.ctrl.config)
        cfg.director.preset = self.preset.currentData()
        cams = [c for c in cfg.cameras if c.enabled] or default_cameras(3)
        use_file = self.r_file.isChecked() and self.path.text().strip()
        path, bpm = self.path.text().strip(), self.bpm.value()
        worker = self._worker

        def job():
            if use_file:
                audio, sr = load_audio(path)
                bt, secs, marks_sections = None, [], []
            else:
                tr = generate(bpm=bpm)
                audio, sr, bt = tr.audio, tr.sr, tr.beat_times
                secs = [s for _, s, _ in tr.sections[1:]]
            return run_replay(audio, sr, cams, cfg.director, cfg.analysis, seed=cfg.director.seed or 7,
                              beat_times=bt, section_starts=secs, log_holds=True,
                              progress=lambda f: worker.progress.emit(f))

        self.btn_run.setEnabled(False)
        self.progress.setValue(0)
        self._worker.run(job)

    def _done(self, r, exc) -> None:
        self.btn_run.setEnabled(True)
        if exc is not None:
            self.note.setText(tr("Simulation failed: {0}").format(exc))
            return
        self.progress.setValue(100)
        self.result = r
        cams = []
        for s in r.shots:
            if s.camera not in cams:
                cams.append(s.camera)
        self.timeline.set_data([(s.camera, s.start, s.end) for s in r.shots], r.duration, cams,
                               marks=[(e["t"], e["kind"]) for e in r.events])
        cols = [r.metrics, r.baselines.get("timer-12s"), r.baselines.get("random")]
        for i, (k, _) in enumerate(METRIC_ROWS):
            for j, m in enumerate(cols):
                val = getattr(m, k) if m else ""
                if k == "master_share" and m:
                    val = f"{val * 100:.0f}%"
                it = QTableWidgetItem("—" if val is None else str(val))
                it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(i, j, it)
        self.shots.setRowCount(0)
        for s in r.shots:
            row = self.shots.rowCount()
            self.shots.insertRow(row)
            for c, txt in enumerate((f"{int(s.start // 60)}:{s.start % 60:04.1f}", f"{s.duration:.1f}s",
                                     s.camera, f"[{s.timing or '-'}] {tr_reason(s.reason)}")):
                self.shots.setItem(row, c, QTableWidgetItem(txt))
        self.btn_json.setEnabled(True)
        self.btn_csv.setEnabled(True)
        self.note.setText(tr("{0:.1f} min analysed · {1} musical events · Metrics describe behaviour, "
                             "not artistic quality.").format(r.duration / 60, len(r.events)))

    def _export(self, kind: str) -> None:
        if not self.result:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export", f"simulation.{kind}", f"*.{kind}")
        if p:
            (self.result.export_json if kind == "json" else self.result.export_csv)(p)

    def refresh(self, s: AppSnapshot) -> None:
        pass


class LogPage(QWidget):
    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        page, v = page_scaffold(tr("Decisions & diagnostics"), tr("Every decision with the reason the engine actually "
                                "used. The full session log is saved as JSONL in the logs folder."))
        QVBoxLayout(self).addWidget(page)
        self.layout().setContentsMargins(0, 0, 0, 0)
        lat = Card(tr("Latency (indicative)"))
        self.lat = label("", "Muted", wrap=True)
        lat.lay.addWidget(self.lat)
        lat.lay.addWidget(label(tr("Capture = audio driver input latency · Analysis = time from sound to music "
                                "state (includes analysis window) · Decision = engine cost per tick · Command = "
                                "WebSocket round trip to OBS. Video presentation latency of your cameras is not "
                                "measurable here: tune the sync offset by recording a test and checking cuts "
                                "against the beat."), "Faint", wrap=True))
        v.addWidget(lat)
        row = QHBoxLayout()
        b = QPushButton(tr("Open logs folder"))
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir()))))
        row.addWidget(b)
        row.addStretch()
        self.path = label("", "Faint")
        row.addWidget(self.path)
        v.addLayout(row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([tr("Time"), tr("Action"), tr("Camera"), tr("Music state"), tr("Conf."), tr("Reason")])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(460)
        v.addWidget(self.table)
        self._n = -1

    def refresh(self, s: AppSnapshot) -> None:
        L = s.latency
        self.lat.setText(tr("Capture {0:.0f} ms · Analysis {1:.0f} ms · DSP cost {2:.1f} ms/iteration · Decision "
                            "{3:.2f} ms · Command round trip {4:.1f} ms · Sync offset {5:.0f} ms").format(
            L.capture_ms, L.analysis_ms, L.dsp_cost_ms, L.decision_ms, L.command_rtt_ms, L.offset_ms))
        self.path.setText(str(self.ctrl.decision_log.path))
        decs = list(self.ctrl.decisions)
        if len(decs) == self._n and (not decs or self.table.rowCount()):
            return
        self._n = len(decs)
        t0 = decs[0].t if decs else 0
        self.table.setRowCount(len(decs))
        for i, d in enumerate(reversed(decs)):
            vals = (f"{d.t - t0:8.1f}s", tr_action(d.action.value), d.candidate_camera or d.current_camera or "",
                    tr_state(d.music_state), f"{d.confidence:.2f}", tr_reason(d.reason))
            for c, txt in enumerate(vals):
                it = QTableWidgetItem(txt)
                if d.action == DirectorAction.SWITCH:
                    it.setForeground(QColor(T.TEXT))
                else:
                    it.setForeground(QColor(T.MUTED))
                self.table.setItem(i, c, it)

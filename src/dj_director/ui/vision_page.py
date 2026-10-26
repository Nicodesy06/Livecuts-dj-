"""Vision page: fast eye (quality, movement, mixer region), slow eye (AI model), MIDI probe."""

from __future__ import annotations

import copy
import time
import webbrowser
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox,
                               QProgressBar, QPushButton, QRadioButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from dj_director.core.controller import AppController, AppSnapshot
from dj_director.sensors.midi_probe import probe
from dj_director.ui import theme as T
from dj_director.ui.i18n import tr
from dj_director.ui.settings_pages import page_scaffold
from dj_director.ui.widgets import Card, Worker, label
from dj_director.visual.imaging import from_rgb
from dj_director.visual.providers import CLOUD_MODELS, LOCAL_MODELS, OllamaProvider, ProviderError, cloud_available

OLLAMA_DOWNLOAD = "https://ollama.com/download"
RATE_LABELS = {"low": "Low (5 stills/s, lightest for OBS; no beat reading)", "normal": "Normal (10 stills/s)",
               "high": "High (16 stills/s, best beat reading)"}
ISSUE_LABELS = {"black frame": "black / dark", "overexposed": "overexposed", "frozen image": "frozen image",
                "blurred or blocked": "blurred or blocked"}


class RoiEditor(QFrame):
    """Shows the latest still of a camera; drag to draw the region of interest (normalised)."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(384, 216)
        self.setObjectName("Card")
        self.pix: Optional[QPixmap] = None
        self.roi: Optional[tuple] = None
        self._drag: Optional[QPointF] = None
        self._cur: Optional[QPointF] = None
        self.setCursor(Qt.CrossCursor)

    def set_frame(self, rgb: Optional[np.ndarray]) -> None:
        self.pix = QPixmap.fromImage(from_rgb(rgb)) if rgb is not None else None
        self.update()

    def set_roi(self, roi: Optional[tuple]) -> None:
        self.roi = tuple(roi) if roi else None
        self.update()

    def _area(self) -> QRectF:
        r = QRectF(self.rect().adjusted(6, 6, -6, -6))
        w, h = r.width(), r.width() * 9 / 16
        if h > r.height():
            h, w = r.height(), r.height() * 16 / 9
        return QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h)

    def _norm(self, p: QPointF) -> QPointF:
        a = self._area()
        return QPointF(min(max((p.x() - a.x()) / a.width(), 0.0), 1.0),
                       min(max((p.y() - a.y()) / a.height(), 0.0), 1.0))

    def mousePressEvent(self, e) -> None:
        self._drag = self._cur = self._norm(e.position())

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None:
            self._cur = self._norm(e.position())
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is None:
            return
        a, b = self._drag, self._norm(e.position())
        self._drag = self._cur = None
        x, y = min(a.x(), b.x()), min(a.y(), b.y())
        w, h = abs(a.x() - b.x()), abs(a.y() - b.y())
        if w >= 0.05 and h >= 0.05:
            self.roi = (round(x, 4), round(y, 4), round(w, 4), round(h, 4))
            self.changed.emit()
        self.update()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        p = QPainter(self)
        a = self._area()
        if self.pix is not None:
            p.drawPixmap(a.toRect(), self.pix)
        else:
            p.fillRect(a, QColor(T.SURFACE_2))
            p.setPen(QColor(T.FAINT))
            p.drawText(a, Qt.AlignCenter, tr("No picture yet from OBS"))
        rect = None
        if self._drag is not None and self._cur is not None:
            x, y = min(self._drag.x(), self._cur.x()), min(self._drag.y(), self._cur.y())
            rect = (x, y, abs(self._drag.x() - self._cur.x()), abs(self._drag.y() - self._cur.y()))
        elif self.roi:
            rect = self.roi
        if rect:
            p.setPen(QPen(QColor(T.ACCENT), 2))
            p.setBrush(QColor(255, 220, 0, 40))
            p.drawRect(QRectF(a.x() + rect[0] * a.width(), a.y() + rect[1] * a.height(),
                              rect[2] * a.width(), rect[3] * a.height()))


class VisionPage(QWidget):
    COLS = ["Camera", "Picture", "Light", "Sharpness", "Movement", "On the beat", "State"]

    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        page, v = page_scaffold(tr("Vision"), tr(
            "Scene awareness. The fast eye looks at small stills of every camera scene (read from OBS, nothing is "
            "changed there): it avoids black, frozen, blurred or blocked shots and notices movement. The slow eye, "
            "optional, is an AI model that looks at all cameras every few seconds and tells the director what is "
            "interesting."))
        QVBoxLayout(self).addWidget(page)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self._last_preview = 0.0

        # ---------------------------------------------------------------- fast eye
        fast = QGroupBox(tr("Fast eye (always local, no AI)"))
        fg = QGridLayout(fast)
        self.enabled = QCheckBox(tr("Analyse the cameras (quality veto, movement, mixer region)"))
        self.rate = QComboBox()
        for k, txt in RATE_LABELS.items():
            self.rate.addItem(tr(txt), k)
        fg.addWidget(self.enabled, 0, 0, 1, 2)
        fg.addWidget(label(tr("Analysis rate"), "Muted"), 1, 0)
        fg.addWidget(self.rate, 1, 1)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in self.COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setMinimumHeight(170)
        fg.addWidget(self.table, 2, 0, 1, 2)
        fg.addWidget(label(tr("Region of interest: select a camera, then drag a rectangle on its picture (e.g. "
                              "around the mixer in the Detail shot). Movement is measured only there; novelty, not "
                              "mere presence, can call the shot."), "Muted", wrap=True), 3, 0, 1, 2)
        self.roi_cam = QComboBox()
        self.roi = RoiEditor()
        roi_row = QHBoxLayout()
        self.btn_roi_clear = QPushButton(tr("Use the whole picture"))
        roi_row.addWidget(label(tr("Camera"), "Muted"))
        roi_row.addWidget(self.roi_cam, 1)
        roi_row.addWidget(self.btn_roi_clear)
        fg.addLayout(roi_row, 4, 0, 1, 2)
        fg.addWidget(self.roi, 5, 0, 1, 2)
        self.frames_state = label("", "Faint", wrap=True)
        fg.addWidget(self.frames_state, 6, 0, 1, 2)
        v.addWidget(fast)

        # ---------------------------------------------------------------- slow eye
        slow = QGroupBox(tr("Slow eye (AI model looking at the cameras)"))
        sv = QVBoxLayout(slow)
        prov = QHBoxLayout()
        self.prov_group = QButtonGroup(self)
        self.prov_buttons = {}
        for i, (key, txt) in enumerate((("off", "Off"), ("local", "Local (offline, on this PC)"),
                                        ("cloud", "Cloud (online, Claude API)"))):
            rb = QRadioButton(tr(txt))
            self.prov_group.addButton(rb, i)
            self.prov_buttons[key] = rb
            prov.addWidget(rb)
        prov.addStretch()
        sv.addLayout(prov)
        iv = QHBoxLayout()
        self.interval = QDoubleSpinBox()
        self.interval.setRange(3, 60)
        self.interval.setSingleStep(1)
        self.interval.setSuffix(" s")
        iv.addWidget(label(tr("Look every"), "Muted"))
        iv.addWidget(self.interval)
        iv.addWidget(label(tr("The verdict guides the next choices for about two looks; cuts stay on the beat."),
                           "Faint", wrap=True), 1)
        sv.addLayout(iv)

        # local models
        self.local_box = Card(tr("Local models (Ollama)"))
        self.ollama_state = label("", "Muted", wrap=True)
        orow = QHBoxLayout()
        orow.addWidget(self.ollama_state, 1)
        self.btn_ollama_get = QPushButton(tr("Download Ollama"))
        self.btn_ollama_check = QPushButton(tr("Check again"))
        orow.addWidget(self.btn_ollama_get)
        orow.addWidget(self.btn_ollama_check)
        self.local_box.lay.addLayout(orow)
        self.local_box.lay.addWidget(label(tr(
            "Ollama runs AI models on this PC: nothing leaves the computer and no internet is needed during the "
            "show (only to download a model once). With an NVIDIA RTX 4060 (8 GB) the recommended model leaves "
            "enough video memory to OBS."), "Faint", wrap=True))
        self.model_rows: dict[str, dict] = {}
        grid = QGridLayout()
        for r, m in enumerate(LOCAL_MODELS):
            sel = QRadioButton(f"{m.name} · {m.size_gb:.1f} GB" + (tr(" · recommended") if m.recommended else ""))
            sel.setProperty("tag", m.tag)
            txt = label(tr("Pros: ") + tr(m.pros) + "\n" + tr("Cons: ") + tr(m.cons), "Muted", wrap=True)
            state = label("", "Faint")
            btn = QPushButton(tr("Download"))
            btn.clicked.connect(lambda _=False, tag=m.tag: self._download(tag))
            rm = QPushButton(tr("Remove"))
            rm.clicked.connect(lambda _=False, tag=m.tag: self._remove(tag))
            grid.addWidget(sel, r * 2, 0)
            grid.addWidget(state, r * 2, 1)
            grid.addWidget(btn, r * 2, 2)
            grid.addWidget(rm, r * 2, 3)
            grid.addWidget(txt, r * 2 + 1, 0, 1, 4)
            self.model_rows[m.tag] = {"sel": sel, "state": state, "btn": btn, "rm": rm}
        grid.setColumnStretch(0, 1)
        self.local_group = QButtonGroup(self)
        for i, row in enumerate(self.model_rows.values()):
            self.local_group.addButton(row["sel"], i)
        self.local_box.lay.addLayout(grid)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress_label = label("", "Faint")
        self.local_box.lay.addWidget(self.progress)
        self.local_box.lay.addWidget(self.progress_label)
        sv.addWidget(self.local_box)

        # cloud
        self.cloud_box = Card(tr("Cloud model (Claude API)"))
        cf = QFormLayout()
        self.cloud_model = QComboBox()
        for m in CLOUD_MODELS:
            self.cloud_model.addItem(m.name + (tr(" · recommended") if m.recommended else ""), m.tag)
        self.cloud_info = label("", "Muted", wrap=True)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText(tr("stored encrypted (Windows DPAPI)"))
        cf.addRow(tr("Model"), self.cloud_model)
        cf.addRow("", self.cloud_info)
        cf.addRow(tr("API key"), self.api_key)
        self.cloud_box.lay.addLayout(cf)
        self.cloud_box.lay.addWidget(label(tr(
            "Pros: the best judgement of the scene, no load on this PC. Cons: needs a stable internet connection "
            "during the show, an API key and has a cost per look (with one look every 8 s about 450 looks per hour); "
            "the camera stills are sent to Anthropic."), "Faint", wrap=True))
        if not cloud_available():
            self.cloud_box.lay.addWidget(label(tr("The 'anthropic' package is missing in this installation."),
                                               "Muted", wrap=True))
        sv.addWidget(self.cloud_box)
        self.judge_state = label("", "Muted", wrap=True)
        self.judge_view = label("", "Faint", wrap=True)
        sv.addWidget(self.judge_state)
        sv.addWidget(self.judge_view)
        v.addWidget(slow)

        # ---------------------------------------------------------------- MIDI
        midi = QGroupBox(tr("DJ hardware (MIDI)"))
        mv = QVBoxLayout(midi)
        mv.addWidget(label(tr(
            "Does your controller tell the PC what you touch? Connect it via USB, press the button and move "
            "faders and knobs for 30 seconds. Pioneer XDJ-RX3: in standalone mode it normally sends nothing "
            "(MIDI only in SOFTWARE CONTROL or MIXER MODE = MIDI CONTROL)."), "Muted", wrap=True))
        mrow = QHBoxLayout()
        self.btn_midi = QPushButton(tr("Run MIDI probe (30 s)"))
        mrow.addWidget(self.btn_midi)
        mrow.addStretch()
        mv.addLayout(mrow)
        self.midi_result = label("", "Faint", wrap=True)
        mv.addWidget(self.midi_result)
        v.addWidget(midi)

        row = QHBoxLayout()
        row.addStretch()
        self.btn_save = QPushButton(tr("Save vision settings"))
        self.btn_save.setObjectName("Primary")
        row.addWidget(self.btn_save)
        v.addLayout(row)
        v.addStretch()

        self._installed: list[str] = []
        self._ollama_version: Optional[str] = None
        self._pulling: Optional[str] = None
        self._check_worker = Worker()
        self._check_worker.done.connect(self._checked)
        self._pull_worker = Worker()
        self._pull_worker.done.connect(self._pulled)
        self._pull_worker.progress.connect(self._pull_progress)
        self._midi_worker = Worker()
        self._midi_worker.done.connect(self._midi_done)
        self._pull_status = ""

        self.btn_save.clicked.connect(self.save)
        self.btn_roi_clear.clicked.connect(self._clear_roi)
        self.roi.changed.connect(self._roi_changed)
        self.roi_cam.currentIndexChanged.connect(self._roi_cam_changed)
        self.prov_group.idToggled.connect(lambda _i, on: on and self._update_boxes())
        self.cloud_model.currentIndexChanged.connect(self._update_cloud_info)
        self.btn_ollama_get.clicked.connect(lambda: webbrowser.open(OLLAMA_DOWNLOAD))
        self.btn_ollama_check.clicked.connect(self.check_ollama)
        self.btn_midi.clicked.connect(self._midi)
        self._rois: dict[str, list[float]] = {}
        self.reload()

    # ------------------------------------------------------------------ load / save
    def reload(self) -> None:
        vc = self.ctrl.config.vision
        self.enabled.setChecked(vc.enabled)
        self.rate.setCurrentIndex(max(0, self.rate.findData(vc.rate)))
        self.prov_buttons.get(vc.judge_provider, self.prov_buttons["off"]).setChecked(True)
        self.interval.setValue(vc.judge_interval)
        row = self.model_rows.get(vc.local_model)
        if row is not None:
            row["sel"].setChecked(True)
        self.cloud_model.setCurrentIndex(max(0, self.cloud_model.findData(vc.cloud_model)))
        self.api_key.setText("")
        self.api_key.setPlaceholderText(tr("key saved (encrypted)") if self.ctrl.has_claude_key()
                                        else tr("stored encrypted (Windows DPAPI)"))
        self._rois = {k: list(v) for k, v in vc.rois.items()}
        names = [c.scene_name for c in self.ctrl.config.cameras if c.enabled]
        cur = self.roi_cam.currentText()
        self.roi_cam.blockSignals(True)
        self.roi_cam.clear()
        self.roi_cam.addItems(names)
        if cur in names:
            self.roi_cam.setCurrentText(cur)
        else:
            detail = next((c.scene_name for c in self.ctrl.config.cameras
                           if c.enabled and c.role.value in ("DETAIL", "OVERHEAD")), None)
            if detail:
                self.roi_cam.setCurrentText(detail)
        self.roi_cam.blockSignals(False)
        self._roi_cam_changed()
        self._update_boxes()
        self._update_cloud_info()
        self.check_ollama()

    def _provider(self) -> str:
        for k, b in self.prov_buttons.items():
            if b.isChecked():
                return k
        return "off"

    def _local_model(self) -> str:
        for tag, row in self.model_rows.items():
            if row["sel"].isChecked():
                return tag
        return self.ctrl.config.vision.local_model

    def save(self) -> None:
        cfg = copy.deepcopy(self.ctrl.config)
        vc = cfg.vision
        vc.enabled = self.enabled.isChecked()
        vc.rate = self.rate.currentData()
        vc.judge_provider = self._provider()
        vc.judge_interval = self.interval.value()
        vc.local_model = self._local_model()
        vc.cloud_model = self.cloud_model.currentData()
        vc.rois = {k: list(v) for k, v in self._rois.items()}
        if self.api_key.text().strip():
            self.ctrl.set_claude_key(self.api_key.text().strip())
            self.api_key.setText("")
            self.api_key.setPlaceholderText(tr("key saved (encrypted)"))
        if vc.judge_provider == "local" and self._ollama_version and \
                not OllamaProvider().has_model(vc.local_model, self._installed):
            QMessageBox.information(self, tr("Vision"), tr("Download the chosen model first (button Download)."))
        if vc.judge_provider == "cloud" and not self.ctrl.has_claude_key():
            QMessageBox.information(self, tr("Vision"), tr("Enter a Claude API key to use the cloud model."))
        self.ctrl.apply_config(cfg)
        self.ctrl.vision.restart_judge()

    # ------------------------------------------------------------------ ROI
    def _roi_cam_changed(self) -> None:
        self.roi.set_roi(self._rois.get(self.roi_cam.currentText()))
        self._last_preview = 0.0

    def _roi_changed(self) -> None:
        name = self.roi_cam.currentText()
        if name and self.roi.roi:
            self._rois[name] = list(self.roi.roi)

    def _clear_roi(self) -> None:
        self._rois.pop(self.roi_cam.currentText(), None)
        self.roi.set_roi(None)

    # ------------------------------------------------------------------ slow eye helpers
    def _update_boxes(self) -> None:
        p = self._provider()
        self.local_box.setVisible(p == "local")
        self.cloud_box.setVisible(p == "cloud")

    def _update_cloud_info(self) -> None:
        tag = self.cloud_model.currentData()
        m = next((m for m in CLOUD_MODELS if m.tag == tag), None)
        if m is not None:
            self.cloud_info.setText(tr("Pros: ") + tr(m.pros) + "\n" + tr("Cons: ") + tr(m.cons))

    def check_ollama(self) -> None:
        url = self.ctrl.config.vision.ollama_url
        self.ollama_state.setText(tr("Checking Ollama…"))

        def work():
            o = OllamaProvider(url)
            ver = o.version()
            return ver, (o.installed() if ver else [])
        self._check_worker.run(work)

    def _checked(self, res, exc) -> None:
        ver, installed = res if res else (None, [])
        self._ollama_version, self._installed = ver, installed
        if ver:
            self.ollama_state.setText(tr("Ollama {0} is running on this PC.").format(ver))
            self.ollama_state.setStyleSheet(f"color: {T.OK};")
        else:
            self.ollama_state.setText(tr("Ollama is not installed or not running. Install it (free), start it, "
                                         "then press Check again."))
            self.ollama_state.setStyleSheet(f"color: {T.WARN};")
        o = OllamaProvider()
        for tag, row in self.model_rows.items():
            have = bool(ver) and o.has_model(tag, installed)
            row["state"].setText(tr("installed") if have else tr("not installed"))
            row["btn"].setEnabled(bool(ver) and not have and self._pulling is None)
            row["rm"].setEnabled(bool(ver) and have and self._pulling is None)

    def _download(self, tag: str) -> None:
        m = next(m for m in LOCAL_MODELS if m.tag == tag)
        r = QMessageBox.question(self, tr("Download model"), tr(
            "Download {0} ({1:.1f} GB) with Ollama?\n\nPros: {2}\nCons: {3}\n\nThe download needs internet once; "
            "afterwards the model works offline.").format(m.name, m.size_gb, tr(m.pros), tr(m.cons)))
        if r != QMessageBox.Yes:
            return
        self._pulling = tag
        self.progress.setVisible(True)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        for row in self.model_rows.values():
            row["btn"].setEnabled(False)
            row["rm"].setEnabled(False)
        url = self.ctrl.config.vision.ollama_url
        worker = self._pull_worker

        def progress(status: str, frac: float) -> None:
            self._pull_status = status
            worker.progress.emit(frac)
        self._pull_worker.run(lambda: OllamaProvider(url).pull(tag, progress))

    def _pull_progress(self, frac: float) -> None:
        if frac < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(frac * 1000))
        self.progress_label.setText(self._pull_status)

    def _pulled(self, res, exc) -> None:
        tag, self._pulling = self._pulling, None
        self.progress.setVisible(False)
        if exc is not None:
            self.progress_label.setText(tr("Download failed: {0}").format(exc))
        else:
            self.progress_label.setText(tr("{0} downloaded: it works offline from now on.").format(tag))
            row = self.model_rows.get(tag)
            if row is not None:
                row["sel"].setChecked(True)
        self.check_ollama()

    def _remove(self, tag: str) -> None:
        if QMessageBox.question(self, tr("Remove model"), tr("Remove {0} from this PC?").format(tag)) != QMessageBox.Yes:
            return
        try:
            OllamaProvider(self.ctrl.config.vision.ollama_url).delete(tag)
        except ProviderError as e:
            QMessageBox.warning(self, tr("Remove model"), str(e))
        self.check_ollama()

    # ------------------------------------------------------------------ MIDI
    def _midi(self) -> None:
        self.btn_midi.setEnabled(False)
        self.midi_result.setText(tr("Listening for 30 s: move faders, knobs and buttons now…"))
        self._midi_worker.run(probe, 30.0)

    def _midi_done(self, res, exc) -> None:
        self.btn_midi.setEnabled(True)
        if exc is not None:
            self.midi_result.setText(str(exc))
            return
        if not res.devices:
            self.midi_result.setText(tr("No MIDI input found on this PC."))
        elif res.error:
            self.midi_result.setText(tr(res.error))
        elif not res.total:
            self.midi_result.setText(tr("No MIDI messages in {0:.0f} s from: {1}. In standalone mode the unit does not "
                                        "send MIDI: the director relies on cameras and audio.")
                                     .format(res.seconds, ", ".join(res.devices)))
        else:
            self.midi_result.setText(tr("MIDI received: ") + "; ".join(
                f"{d}: {n} ({', '.join(res.samples.get(d, [])[:3])})" for d, n in res.counts.items() if n))

    # ------------------------------------------------------------------ live view
    def refresh(self, s: AppSnapshot) -> None:
        vs = s.vision
        if vs is None:
            return
        state = {"off": tr("off"), "connecting": tr("connecting"), "waiting": tr("waiting for OBS"),
                 "running": tr("running"), "auth_failed": tr("OBS password rejected")}.get(vs.frames_state,
                                                                                         vs.frames_state)
        self.frames_state.setText(tr("Stills from OBS: {0}").format(state) +
                                  (f" · {vs.frames_error}" if vs.frames_error and vs.frames_state != "running" else ""))
        cams = vs.cameras
        if self.table.rowCount() != len(cams):
            self.table.setRowCount(len(cams))
        for r, c in enumerate(cams):
            if not c.has_frame or c.age > 3:
                vals = [c.name, tr("no picture"), "–", "–", "–", "–", tr("no information")]
            else:
                ok = tr(ISSUE_LABELS.get(c.issue, c.issue)) if c.issue else tr("OK")
                vals = [c.name, f"{c.fps:.1f} fps", f"{c.brightness * 100:.0f}%", f"{c.sharpness * 100:.0f}%",
                        f"{c.motion * 100:.0f}%" + (" ▣" if c.roi else ""), f"{c.coherence * 100:.0f}%", ok]
            for col, val in enumerate(vals):
                it = self.table.item(r, col)
                if it is None:
                    it = QTableWidgetItem()
                    self.table.setItem(r, col, it)
                it.setText(val)
                if col == 6:
                    it.setForeground(QColor(T.LIVE if c.issue else T.OK if c.has_frame else T.FAINT))
        now = time.monotonic()
        if now - self._last_preview > 0.5 and self.isVisible():
            self._last_preview = now
            self.roi.set_frame(self.ctrl.vision.fast.latest_rgb(self.roi_cam.currentText(), now))
        if vs.judge_provider == "off":
            self.judge_state.setText(tr("Slow eye off."))
            self.judge_view.setText("")
            return
        jstate = {"running": tr("running"), "starting": tr("starting"), "error": tr("error"),
                  "waiting for camera pictures": tr("waiting for camera pictures"), "off": tr("off")}.get(
            vs.judge_state, vs.judge_state)
        self.judge_state.setText(tr("Slow eye: {0}").format(jstate) + (f" · {vs.judge_error}" if vs.judge_error else ""))
        j = vs.judge
        if j is None:
            self.judge_view.setText("")
            return
        lines = [tr("Last look {0:.0f} s ago ({1:.1f} s, {2}): {3}").format(time.monotonic() - j.t, j.latency, j.model,
                                                                          j.note)]
        for name, cj in j.cameras.items():
            lines.append(f"• {name}: {cj.what} — " + (tr("interest {0:.0f}%").format(cj.interest * 100) if cj.usable
                                                      else tr("not usable: {0}").format(cj.issue)))
        if j.drop_pick:
            lines.append(tr("For the drop: {0}").format(j.drop_pick))
        self.judge_view.setText("\n".join(lines))

"""Configuration pages: Cameras, Audio, OBS, Director."""

from __future__ import annotations

import copy
import dataclasses
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLineEdit, QMessageBox, QPushButton, QRadioButton,
                               QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from dj_director.audio.backends import list_input_devices, list_loopback_devices, loopback_available
from dj_director.config.presets import PRESET_DESCRIPTIONS, PRESETS
from dj_director.config.schema import CameraConfig, CameraRole, DirectorParams, ROLE_DURATIONS
from dj_director.core.controller import AppController, AppSnapshot
from dj_director.ui import theme as T
from dj_director.ui.widgets import Card, LevelMeter, Worker, label


def page_scaffold(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
    outer = QWidget()
    outer.setObjectName("Page")
    lay = QVBoxLayout(outer)
    lay.setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    inner = QWidget()
    inner.setObjectName("Page")
    v = QVBoxLayout(inner)
    v.setContentsMargins(28, 24, 28, 24)
    v.setSpacing(14)
    v.addWidget(label(title, "PageTitle"))
    v.addWidget(label(subtitle, "PageSubtitle", wrap=True))
    scroll.setWidget(inner)
    lay.addWidget(scroll)
    return outer, v


# =========================================================================== cameras
class CamerasPage(QWidget):
    COLS = ["Use", "OBS scene", "Role", "Master", "Weight", "Min s", "Mean s", "Max s", "Cooldown s", "Status"]

    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        page, v = page_scaffold("Cameras", "Map OBS scenes to editorial roles. The master is the narrative "
                                "anchor: it gets the longest shots and the director returns to it naturally. "
                                "Durations left at 0 use the role defaults (scaled by the preset).")
        QVBoxLayout(self).addWidget(page)
        self.layout().setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.btn_refresh = QPushButton("Reload scenes from OBS")
        self.btn_save = QPushButton("Save camera map")
        self.btn_save.setObjectName("Primary")
        bar.addWidget(self.btn_refresh)
        bar.addStretch()
        self.status = label("", "Muted")
        bar.addWidget(self.status)
        bar.addWidget(self.btn_save)
        v.addLayout(bar)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setMinimumHeight(320)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setStyleSheet("QTableWidget QDoubleSpinBox, QTableWidget QComboBox { padding: 2px 6px;"
                                 " margin: 4px; border-radius: 6px; }")
        for col, w in ((0, 50), (2, 130), (3, 70), (9, 130)):
            self.table.setColumnWidth(col, w)
        for col in range(4, 9):
            self.table.setColumnWidth(col, 100)
        v.addWidget(self.table)
        v.addWidget(label("Role defaults (Natural): MASTER 8–90 s (mean 36), DETAIL 5–15 s, SIDE/WIDE 7–25 s, "
                          "OVERHEAD 5–18 s. Cooldown = minimum time before an insert is reused.", "Faint", wrap=True))
        v.addStretch()
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_save.clicked.connect(self.save)
        self._master_group = QButtonGroup(self)
        self.reload()

    def reload(self) -> None:
        cfg = self.ctrl.config
        obs_scenes = list(self.ctrl.obs.status().scenes) if self.ctrl.obs.connected else []
        known = {c.scene_name: c for c in cfg.cameras}
        names = list(obs_scenes) + [n for n in known if n not in obs_scenes]
        self.table.setRowCount(0)
        self._master_group = QButtonGroup(self)
        self._master_group.setExclusive(True)
        for name in names:
            c = known.get(name)
            r = self.table.rowCount()
            self.table.insertRow(r)
            use = QCheckBox()
            use.setChecked(bool(c and c.enabled))
            self.table.setCellWidget(r, 0, self._center(use))
            it = QTableWidgetItem(name)
            it.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 1, it)
            role = QComboBox()
            role.addItems([x.value for x in CameraRole])
            role.setCurrentText((c.role if c else self._guess_role(name)).value)
            self.table.setCellWidget(r, 2, role)
            master = QRadioButton()
            self._master_group.addButton(master, r)
            master.setChecked(bool(c and c.is_master))
            self.table.setCellWidget(r, 3, self._center(master))
            self.table.setCellWidget(r, 4, self._dspin(c.weight if c else 1.0, 0, 3, 0.1))
            for col, attr in ((5, "min_duration"), (6, "mean_duration"), (7, "max_duration")):
                self.table.setCellWidget(r, col, self._dspin(getattr(c, attr) if c else 0.0, 0, 1800, 1))
            self.table.setCellWidget(r, 8, self._dspin(c.cooldown if c else -1.0, -1, 600, 1))
            present = name in obs_scenes
            st = QTableWidgetItem("in OBS" if present else ("missing in OBS" if obs_scenes else "OBS offline"))
            st.setForeground(Qt.green if present else Qt.yellow)
            st.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 9, st)
        self.status.setText(f"{len(obs_scenes)} scenes in OBS" if obs_scenes else
                            "OBS not connected: showing saved cameras only")

    @staticmethod
    def _guess_role(name: str) -> CameraRole:
        n = name.lower()
        for key, role in (("master", CameraRole.MASTER), ("main", CameraRole.MASTER),
                          ("mixer", CameraRole.DETAIL), ("detail", CameraRole.DETAIL),
                          ("hand", CameraRole.DETAIL), ("deck", CameraRole.DETAIL),
                          ("side", CameraRole.SIDE), ("wide", CameraRole.WIDE),
                          ("crowd", CameraRole.WIDE), ("top", CameraRole.OVERHEAD),
                          ("overhead", CameraRole.OVERHEAD)):
            if key in n:
                return role
        return CameraRole.CUSTOM

    @staticmethod
    def _center(w: QWidget) -> QWidget:
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setAlignment(Qt.AlignCenter)
        h.addWidget(w)
        return box

    @staticmethod
    def _dspin(val: float, lo: float, hi: float, step: float) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(1)
        s.setValue(val)
        s.setSpecialValueText("default")
        return s

    def save(self) -> None:
        cfg = copy.deepcopy(self.ctrl.config)
        cams = []
        master_row = self._master_group.checkedId()
        for r in range(self.table.rowCount()):
            use = self.table.cellWidget(r, 0).findChild(QCheckBox).isChecked()
            name = self.table.item(r, 1).text()
            role = CameraRole(self.table.cellWidget(r, 2).currentText())
            vals = [self.table.cellWidget(r, c).value() for c in (4, 5, 6, 7, 8)]
            if not use and r != master_row:
                continue
            cams.append(CameraConfig(scene_name=name, role=role, enabled=use, is_master=(r == master_row),
                                     weight=vals[0], min_duration=vals[1], mean_duration=vals[2],
                                     max_duration=vals[3], cooldown=vals[4]))
        if cams and not any(c.is_master for c in cams):
            m = next((c for c in cams if c.role == CameraRole.MASTER), None)
            if m is None:
                QMessageBox.warning(self, "Master camera", "Please choose a master camera (the narrative anchor).")
                return
            m.is_master = True
        cfg.cameras = cams
        self.ctrl.apply_config(cfg)
        self.status.setText(f"Saved {len(cams)} cameras")

    def refresh(self, s: AppSnapshot) -> None:
        pass


# =========================================================================== audio
class AudioPage(QWidget):
    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        page, v = page_scaffold("Audio", "Feed the director the master output of your mixer (audio "
                                "interface / USB mixer input) — not a room microphone. Capture is read-only "
                                "and never alters the signal OBS records.")
        QVBoxLayout(self).addWidget(page)
        self.layout().setContentsMargins(0, 0, 0, 0)

        src = QGroupBox("Source")
        f = QFormLayout(src)
        self.backend = QComboBox()
        self.backend.addItem("Audio input (interface / mixer)", "input")
        self.backend.addItem("WASAPI loopback (what Windows plays)" +
                             ("" if loopback_available() else " — unavailable"), "loopback")
        self.backend.addItem("Audio file (testing)", "file")
        f.addRow("Type", self.backend)
        drow = QHBoxLayout()
        self.device = QComboBox()
        self.device.setMinimumWidth(380)
        self.btn_rescan = QPushButton("Rescan")
        drow.addWidget(self.device, 1)
        drow.addWidget(self.btn_rescan)
        f.addRow("Device", drow)
        self.channels = QComboBox()
        for label_, ch in (("1 + 2 (stereo → mono)", [0, 1]), ("1 only", [0]), ("2 only", [1]),
                           ("3 + 4", [2, 3]), ("5 + 6", [4, 5]), ("7 + 8", [6, 7])):
            self.channels.addItem(label_, ch)
        f.addRow("Channels", self.channels)
        frow = QHBoxLayout()
        self.file = QLineEdit()
        self.btn_browse = QPushButton("Browse…")
        frow.addWidget(self.file, 1)
        frow.addWidget(self.btn_browse)
        f.addRow("File", frow)
        self.loop = QCheckBox("Loop file")
        f.addRow("", self.loop)
        v.addWidget(src)

        ana = QGroupBox("Analysis (advanced)")
        g = QFormLayout(ana)
        self.silence = QDoubleSpinBox(); self.silence.setRange(-90, -20); self.silence.setSuffix(" dBFS")
        self.sens = QDoubleSpinBox(); self.sens.setRange(0.25, 3.0); self.sens.setSingleStep(0.05)
        self.min_bpm = QDoubleSpinBox(); self.min_bpm.setRange(40, 150)
        self.max_bpm = QDoubleSpinBox(); self.max_bpm.setRange(90, 260)
        self.prior = QDoubleSpinBox(); self.prior.setRange(60, 200)
        self.bpb = QSpinBox(); self.bpb.setRange(2, 12)
        g.addRow("Silence threshold", self.silence)
        g.addRow("Musical sensitivity", self.sens)
        g.addRow("BPM range min", self.min_bpm)
        g.addRow("BPM range max", self.max_bpm)
        g.addRow("Typical tempo (prior)", self.prior)
        g.addRow("Beats per bar (heuristic)", self.bpb)
        g.addRow("", label("Set the typical tempo near your genre (e.g. 95 for hip-hop, 174 for drum & bass) "
                           "to reduce half/double-time errors.", "Faint", wrap=True))
        v.addWidget(ana)

        mon = Card("Live input")
        self.meter = LevelMeter(16)
        mon.lay.addWidget(self.meter)
        self.mon_text = label("", "Muted", wrap=True)
        mon.lay.addWidget(self.mon_text)
        v.addWidget(mon)

        brow = QHBoxLayout()
        brow.addStretch()
        self.btn_apply = QPushButton("Apply audio settings")
        self.btn_apply.setObjectName("Primary")
        brow.addWidget(self.btn_apply)
        v.addLayout(brow)
        v.addStretch()

        self.backend.currentIndexChanged.connect(self._backend_changed)
        self.btn_rescan.clicked.connect(self._fill_devices)
        self.btn_browse.clicked.connect(self._browse)
        self.btn_apply.clicked.connect(self.apply)
        self.reload()

    def reload(self) -> None:
        cfg = self.ctrl.config
        a, an = cfg.audio, cfg.analysis
        self.backend.setCurrentIndex(max(0, self.backend.findData(a.backend)))
        self._fill_devices()
        idx = self.channels.findData(a.channels)
        self.channels.setCurrentIndex(idx if idx >= 0 else 0)
        self.file.setText(a.file_path)
        self.loop.setChecked(a.file_loop)
        self.silence.setValue(an.silence_db)
        self.sens.setValue(an.sensitivity)
        self.min_bpm.setValue(an.min_bpm)
        self.max_bpm.setValue(an.max_bpm)
        self.prior.setValue(an.prior_bpm)
        self.bpb.setValue(an.beats_per_bar)
        self._backend_changed()

    def _fill_devices(self) -> None:
        cfg = self.ctrl.config.audio
        self.device.clear()
        devs = list_loopback_devices() if self.backend.currentData() == "loopback" else list_input_devices()
        for d in devs:
            self.device.addItem(d.label, (d.name, d.host_api))
        if not devs:
            self.device.addItem("(no devices found)", None)
        for i in range(self.device.count()):
            data = self.device.itemData(i)
            if data and data[0] == cfg.device_name and (not cfg.host_api or data[1] == cfg.host_api):
                self.device.setCurrentIndex(i)

    def _backend_changed(self) -> None:
        b = self.backend.currentData()
        is_file = b == "file"
        for w in (self.file, self.btn_browse, self.loop):
            w.setEnabled(is_file)
        for w in (self.device, self.btn_rescan):
            w.setEnabled(not is_file)
        self._fill_devices()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Audio file", "", "Audio (*.wav *.flac *.ogg *.aiff *.aif)")
        if path:
            self.file.setText(path)

    def apply(self) -> None:
        cfg = copy.deepcopy(self.ctrl.config)
        cfg.audio.backend = self.backend.currentData()
        data = self.device.currentData()
        if data:
            cfg.audio.device_name, cfg.audio.host_api = data
        cfg.audio.channels = self.channels.currentData()
        cfg.audio.file_path = self.file.text().strip()
        cfg.audio.file_loop = self.loop.isChecked()
        an = cfg.analysis
        an.silence_db, an.sensitivity = self.silence.value(), self.sens.value()
        an.min_bpm, an.max_bpm, an.prior_bpm = self.min_bpm.value(), self.max_bpm.value(), self.prior.value()
        an.beats_per_bar = self.bpb.value()
        if an.min_bpm * 1.5 > an.max_bpm:
            QMessageBox.warning(self, "BPM range", "The BPM range must span at least a factor of 1.5.")
            return
        self.ctrl.apply_config(cfg, restart_audio=True)

    def refresh(self, s: AppSnapshot) -> None:
        a = s.audio
        self.meter.set_values(a.level_db, a.peak_db, a.clipping)
        if a.running:
            txt = (f"{a.device_name} · {a.sample_rate} Hz · {a.channels} ch · level {a.level_db:.1f} dBFS"
                   f" · peak {a.peak_db:.1f} dBFS · {'CLIPPING · ' if a.clipping else ''}"
                   f"signal {'present' if a.signal_present else 'absent'} · dropouts {a.overruns}"
                   f" · stream errors {a.stream_errors} · input latency {a.capture_latency_ms:.0f} ms")
            if s.music:
                txt += f" · analysis latency {s.music.analysis_latency_ms:.0f} ms"
        else:
            txt = a.error or "Not running"
        self.mon_text.setText(txt)


# =========================================================================== OBS
class ObsPage(QWidget):
    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        page, v = page_scaffold("OBS connection", "Enable the server in OBS: Tools › WebSocket Server Settings "
                                "(OBS 28 or newer includes obs-websocket 5). DJ Director only reads the scene "
                                "list and changes the Program scene; it never creates, deletes or edits scenes, "
                                "sources, filters, audio, profiles, recording or streaming.")
        QVBoxLayout(self).addWidget(page)
        self.layout().setContentsMargins(0, 0, 0, 0)
        box = QGroupBox("WebSocket server")
        f = QFormLayout(box)
        self.host = QLineEdit()
        self.port = QSpinBox(); self.port.setRange(1, 65535)
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("stored encrypted (Windows DPAPI)")
        self.auto = QCheckBox("Connect automatically and reconnect if OBS restarts")
        f.addRow("Host", self.host)
        f.addRow("Port", self.port)
        f.addRow("Password", self.password)
        f.addRow("", self.auto)
        v.addWidget(box)
        row = QHBoxLayout()
        self.btn_test = QPushButton("Test connection")
        self.btn_save = QPushButton("Save && connect")
        self.btn_save.setObjectName("Primary")
        row.addWidget(self.btn_test)
        row.addStretch()
        row.addWidget(self.btn_save)
        v.addLayout(row)
        self.result = label("", "Muted", wrap=True)
        v.addWidget(self.result)
        info = Card("Studio Mode")
        info.lay.addWidget(label("When Studio Mode is enabled, DJ Director still switches the PROGRAM scene "
                                 "(what is recorded/streamed). The Preview scene is never changed. A scene change "
                                 "you make yourself in OBS is detected as a manual override and respected.",
                                 "Muted", wrap=True))
        v.addWidget(info)
        self.status = label("", "Muted", wrap=True)
        v.addWidget(self.status)
        v.addStretch()
        self.btn_test.clicked.connect(self.test)
        self.btn_save.clicked.connect(self.save)
        self._worker = Worker()
        self._worker.done.connect(self._tested)
        self.reload()

    def reload(self) -> None:
        o = self.ctrl.config.obs
        self.host.setText(o.host)
        self.port.setValue(o.port)
        self.password.setText(self.ctrl.get_password())
        self.auto.setChecked(o.auto_connect)

    def test(self) -> None:
        self.result.setText("Testing…")
        self.btn_test.setEnabled(False)
        from dj_director.obs.controller import ObsController
        self._worker.run(ObsController.test_connection, self.host.text().strip(), self.port.value(),
                         self.password.text())

    def _tested(self, res, exc) -> None:
        self.btn_test.setEnabled(True)
        if exc:
            self.result.setText(f"Failed: {exc}")
            return
        ok, msg, scenes = res
        color = T.OK if ok else T.LIVE
        self.result.setStyleSheet(f"color: {color};")
        self.result.setText(("Connected: " if ok else "Failed: ") + msg +
                            (f" — {len(scenes)} scenes: " + ", ".join(scenes[:12]) if ok else ""))

    def save(self) -> None:
        cfg = copy.deepcopy(self.ctrl.config)
        cfg.obs.host = self.host.text().strip() or "127.0.0.1"
        cfg.obs.port = self.port.value()
        cfg.obs.auto_connect = self.auto.isChecked()
        self.ctrl.set_password(self.password.text())
        self.ctrl.apply_config(cfg, reconnect_obs=True)
        self.result.setText("Saved. Connecting…")

    def refresh(self, s: AppSnapshot) -> None:
        o = s.obs
        self.status.setText(f"State: {o.state}" + (f" · OBS {o.obs_version} · websocket {o.ws_version}"
                                                   f" · Studio Mode {'on' if o.studio_mode else 'off'}"
                                                   f" · Program: {o.program_scene}" if o.connected else
                                                   (f" · {o.error}" if o.error else "")))


# =========================================================================== director
PARAM_LABELS = {
    "cut_threshold": ("Cut threshold (higher = fewer cuts)", 0.3, 3.0, 0.05),
    "master_duration_scale": ("Master shot length ×", 0.2, 4.0, 0.05),
    "insert_duration_scale": ("Insert shot length ×", 0.2, 4.0, 0.05),
    "duration_spread": ("Shot length variation", 0.0, 1.0, 0.05),
    "min_cut_gap": ("Minimum gap between cuts (s)", 0.5, 120, 0.5),
    "max_cuts_per_minute": ("Max cuts per minute", 0.5, 30, 0.5),
    "max_consecutive_inserts": ("Max inserts before returning to master", 1, 8, 1),
    "music_trigger_gain": ("Musical sensitivity of cuts", 0.0, 3.0, 0.05),
    "musical_cut_confidence": ("Min confidence for music-triggered cuts", 0.0, 1.0, 0.05),
    "sync_confidence": ("Min beat confidence for beat sync", 0.0, 1.0, 0.05),
    "downbeat_confidence": ("Min downbeat confidence (heuristic)", 0.0, 1.0, 0.05),
    "max_sync_wait": ("Max wait for beat sync (s)", 0.0, 8.0, 0.1),
    "hysteresis": ("Hysteresis (resistance to change)", 0.0, 2.0, 0.05),
    "variety_jitter": ("Variety randomness", 0.0, 1.0, 0.05),
}


class DirectorPage(QWidget):
    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        page, v = page_scaffold("Director", "Choose a style. Presets are parameter sets of the same engine; "
                                "Natural is recommended. Advanced settings are optional.")
        QVBoxLayout(self).addWidget(page)
        self.layout().setContentsMargins(0, 0, 0, 0)
        prow = QHBoxLayout()
        self.preset_group = QButtonGroup(self)
        self.preset_cards = {}
        for i, name in enumerate(("minimal", "natural", "dynamic")):
            card = Card()
            rb = QRadioButton(name.capitalize() + ("  (recommended)" if name == "natural" else ""))
            f = rb.font(); f.setPointSizeF(12); f.setBold(True); rb.setFont(f)
            card.lay.addWidget(rb)
            card.lay.addWidget(label(PRESET_DESCRIPTIONS[name], "Muted", wrap=True))
            p = PRESETS[name]
            card.lay.addWidget(label(f"Master ×{p.master_duration_scale:g} · inserts ×{p.insert_duration_scale:g}"
                                     f" · ≤{p.max_cuts_per_minute:g} cuts/min", "Faint", wrap=True))
            self.preset_group.addButton(rb, i)
            self.preset_cards[name] = rb
            prow.addWidget(card)
        v.addLayout(prow)

        beh = QGroupBox("Behaviour")
        f = QFormLayout(beh)
        self.override = QComboBox()
        self.override.addItem("Respect for a while, then resume", "timed")
        self.override.addItem("Respect until I press Resume", "until_resume")
        self.override.addItem("Adopt the new shot and continue", "adopt")
        self.override_s = QDoubleSpinBox(); self.override_s.setRange(1, 3600); self.override_s.setSuffix(" s")
        self.nosig = QComboBox()
        self.nosig.addItem("Return to master", "master")
        self.nosig.addItem("Hold current shot", "hold")
        self.offset = QDoubleSpinBox(); self.offset.setRange(-500, 1000); self.offset.setSuffix(" ms")
        self.seed = QSpinBox(); self.seed.setRange(0, 2_000_000_000); self.seed.setSpecialValueText("random")
        self.shadow = QCheckBox("Shadow mode by default (log decisions, never command OBS)")
        self.logd = QCheckBox("Write decision log (JSONL) for every session")
        f.addRow("Manual change in OBS", self.override)
        f.addRow("Manual override duration", self.override_s)
        f.addRow("When the audio signal is lost", self.nosig)
        f.addRow("Sync offset (send cut earlier by)", self.offset)
        f.addRow("Random seed (fixed = reproducible)", self.seed)
        f.addRow("", self.shadow)
        f.addRow("", self.logd)
        v.addWidget(beh)

        self.adv = QGroupBox("Advanced (overrides the preset)")
        self.adv.setCheckable(True)
        g = QGridLayout(self.adv)
        self.param_widgets = {}
        for i, (k, (txt, lo, hi, step)) in enumerate(PARAM_LABELS.items()):
            w = QSpinBox() if isinstance(getattr(DirectorParams(), k), int) else QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setSingleStep(step)
            self.param_widgets[k] = w
            g.addWidget(label(txt, "Muted"), i // 2, (i % 2) * 2)
            g.addWidget(w, i // 2, (i % 2) * 2 + 1)
        self.prefer_db = QCheckBox("Prefer downbeats when confident")
        g.addWidget(self.prefer_db, len(PARAM_LABELS) // 2 + 1, 0, 1, 2)
        v.addWidget(self.adv)

        row = QHBoxLayout()
        self.btn_reset = QPushButton("Reset advanced to preset")
        self.btn_save = QPushButton("Save director settings")
        self.btn_save.setObjectName("Primary")
        row.addWidget(self.btn_reset)
        row.addStretch()
        row.addWidget(self.btn_save)
        v.addLayout(row)
        v.addStretch()
        self.preset_group.idToggled.connect(
            lambda _id, checked: checked and self._show_params(keep_overrides=False))
        self.btn_reset.clicked.connect(lambda: self._show_params(keep_overrides=False))
        self.btn_save.clicked.connect(self.save)
        self.reload()

    def _preset_name(self) -> str:
        return ("minimal", "natural", "dynamic")[max(0, self.preset_group.checkedId())]

    def _show_params(self, keep_overrides: bool = True) -> None:
        base = PRESETS[self._preset_name()]
        ov = self.ctrl.config.director.overrides if keep_overrides else {}
        params = dataclasses.replace(base, **ov)
        for k, w in self.param_widgets.items():
            w.setValue(getattr(params, k))
        self.prefer_db.setChecked(params.prefer_downbeat)
        if not keep_overrides:
            self.adv.setChecked(False)

    def reload(self) -> None:
        d = self.ctrl.config.director
        self.preset_cards.get(d.preset, self.preset_cards["natural"]).setChecked(True)
        self.override.setCurrentIndex(max(0, self.override.findData(d.override_policy)))
        self.override_s.setValue(d.override_seconds)
        self.nosig.setCurrentIndex(max(0, self.nosig.findData(d.no_signal_policy)))
        self.offset.setValue(d.latency_offset_ms)
        self.seed.setValue(d.seed)
        self.shadow.setChecked(self.ctrl.config.shadow_mode)
        self.logd.setChecked(self.ctrl.config.log_decisions)
        self.adv.setChecked(bool(d.overrides))
        self._show_params(keep_overrides=True)

    def save(self) -> None:
        cfg = copy.deepcopy(self.ctrl.config)
        d = cfg.director
        d.preset = self._preset_name()
        d.override_policy = self.override.currentData()
        d.override_seconds = self.override_s.value()
        d.no_signal_policy = self.nosig.currentData()
        d.latency_offset_ms = self.offset.value()
        d.seed = self.seed.value()
        cfg.shadow_mode = self.shadow.isChecked()
        cfg.log_decisions = self.logd.isChecked()
        d.overrides = {}
        if self.adv.isChecked():
            base = PRESETS[d.preset]
            for k, w in self.param_widgets.items():
                val = w.value()
                if abs(float(val) - float(getattr(base, k))) > 1e-9:
                    d.overrides[k] = val
            if self.prefer_db.isChecked() != base.prefer_downbeat:
                d.overrides["prefer_downbeat"] = self.prefer_db.isChecked()
        self.ctrl.apply_config(cfg)

    def refresh(self, s: AppSnapshot) -> None:
        pass

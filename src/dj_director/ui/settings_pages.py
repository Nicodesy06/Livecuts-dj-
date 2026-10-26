"""Configuration pages: Cameras, Audio, OBS, Director."""

from __future__ import annotations

import copy
import dataclasses
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QSlider, QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLineEdit, QMessageBox, QPushButton, QRadioButton,
                               QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from dj_director.ui.i18n import tr, tr_decision, tr_reason, tr_state
from dj_director.audio.backends import list_input_devices, list_loopback_devices, loopback_available
from dj_director.audio.probe import recommend
from dj_director.ui.source_detect import SourceDetectBox
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
    COLS = ["Use", "OBS scene", "Role", "Master", "Weight", "Min s", "Pref. min s", "Pref. max s", "Hard cap s",
            "Cooldown s", "Status"]

    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        page, v = page_scaffold(tr("Cameras"), tr("Map OBS scenes to editorial roles. The master is the narrative "
                                "anchor: it gets the longest shots and the director returns to it naturally. "
                                "Durations left at 0 use the role defaults (scaled by the preset)."))
        QVBoxLayout(self).addWidget(page)
        self.layout().setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.btn_refresh = QPushButton(tr("Reload scenes from OBS"))
        self.btn_save = QPushButton(tr("Save camera map"))
        self.btn_save.setObjectName("Primary")
        bar.addWidget(self.btn_refresh)
        bar.addStretch()
        self.status = label("", "Muted")
        bar.addWidget(self.status)
        bar.addWidget(self.btn_save)
        v.addLayout(bar)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in self.COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setMinimumHeight(320)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setStyleSheet("QTableWidget QDoubleSpinBox, QTableWidget QComboBox { padding: 2px 6px;"
                                 " margin: 4px; border-radius: 6px; }")
        for col, w in ((0, 50), (2, 130), (3, 70), (10, 130)):
            self.table.setColumnWidth(col, w)
        for col in range(4, 10):
            self.table.setColumnWidth(col, 100)
        v.addWidget(self.table)
        v.addWidget(label(tr("Role defaults (Natural): MASTER preferred 20–50 s, no rigid maximum · DETAIL / OVERHEAD "
                          "preferred 6–10 s, hard cap 12–15 s · SIDE / BEHIND / WIDE preferred 10–18 s, hard cap "
                          "20–25 s. Coverage budget: master 70 %, detail 15 %, side 15 % over 5 minutes. "
                          "Cooldown = minimum time before an insert is reused."), "Faint", wrap=True))
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
            for col, val in zip((5, 6, 7, 8), self._duration_cells(c)):
                self.table.setCellWidget(r, col, self._dspin(val, 0, 1800, 1))
            self.table.setCellWidget(r, 9, self._dspin(c.cooldown if c else -1.0, -1, 600, 1))
            present = name in obs_scenes
            st = QTableWidgetItem(tr("in OBS") if present else (tr("missing in OBS") if obs_scenes else tr("OBS offline")))
            st.setForeground(Qt.green if present else Qt.yellow)
            st.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 10, st)
        self.status.setText(tr("{0} scenes in OBS").format(len(obs_scenes)) if obs_scenes else
                            tr("OBS not connected: showing saved cameras only"))

    @staticmethod
    def _duration_cells(c) -> tuple[float, float, float, float]:
        """Min / preferred min / preferred max / hard cap to show; legacy 0.1.x values converted."""
        if c is None:
            return 0.0, 0.0, 0.0, 0.0
        pmin, pmax = c.preferred_min, c.preferred_max
        if not (pmin or pmax) and c.mean_duration:
            pmin, pmax = round(c.mean_duration * 0.8, 1), round(c.mean_duration * 1.2, 1)
        return c.min_duration, pmin, pmax, c.hard_cap or c.max_duration

    @staticmethod
    def _guess_role(name: str) -> CameraRole:
        n = name.lower()
        for key, role in (("master", CameraRole.MASTER), ("main", CameraRole.MASTER),
                          ("mixer", CameraRole.DETAIL), ("detail", CameraRole.DETAIL),
                          ("hand", CameraRole.DETAIL), ("deck", CameraRole.DETAIL),
                          ("side", CameraRole.SIDE), ("later", CameraRole.SIDE), ("wide", CameraRole.WIDE),
                          ("behind", CameraRole.BEHIND), ("back", CameraRole.BEHIND), ("dietro", CameraRole.BEHIND),
                          ("retro", CameraRole.BEHIND),
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
        s.setSpecialValueText(tr("default"))
        return s

    def save(self) -> None:
        cfg = copy.deepcopy(self.ctrl.config)
        cams = []
        master_row = self._master_group.checkedId()
        for r in range(self.table.rowCount()):
            use = self.table.cellWidget(r, 0).findChild(QCheckBox).isChecked()
            name = self.table.item(r, 1).text()
            role = CameraRole(self.table.cellWidget(r, 2).currentText())
            vals = [self.table.cellWidget(r, c).value() for c in (4, 5, 6, 7, 8, 9)]
            if not use and r != master_row:
                continue
            # start from the existing entry so fields not shown here (priority, fallback,
            # allowed states, scene UUID) survive a save
            old = next((c for c in self.ctrl.config.cameras if c.scene_name == name), None)
            cam = copy.deepcopy(old) if old is not None else CameraConfig(scene_name=name)
            cam.role, cam.enabled, cam.is_master = role, use, (r == master_row)
            cam.weight, cam.min_duration = vals[0], vals[1]
            cam.preferred_min, cam.preferred_max, cam.hard_cap, cam.cooldown = vals[2], vals[3], vals[4], vals[5]
            cam.mean_duration = cam.max_duration = 0.0      # legacy fields now expressed by the new ones
            cams.append(cam)
        if cams and not any(c.is_master for c in cams):
            m = next((c for c in cams if c.role == CameraRole.MASTER), None)
            if m is None:
                QMessageBox.warning(self, tr("Master camera"), tr("Please choose a master camera (the narrative anchor)."))
                return
            m.is_master = True
        cfg.cameras = cams
        self.ctrl.apply_config(cfg)
        self.status.setText(tr("Saved {0} cameras").format(len(cams)))

    def refresh(self, s: AppSnapshot) -> None:
        pass


# =========================================================================== audio
class AudioPage(QWidget):
    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        page, v = page_scaffold(tr("Audio"), tr("Feed the director the master output of your mixer (audio "
                                "interface / USB mixer input) — not a room microphone. Capture is read-only "
                                "and never alters the signal OBS records."))
        QVBoxLayout(self).addWidget(page)
        self.layout().setContentsMargins(0, 0, 0, 0)

        src = QGroupBox(tr("Source"))
        f = QFormLayout(src)
        self.backend = QComboBox()
        self.backend.addItem(tr("Audio input (interface / mixer)"), "input")
        self.backend.addItem(tr("WASAPI loopback (what Windows plays)") +
                             ("" if loopback_available() else tr(" — unavailable")), "loopback")
        self.backend.addItem(tr("Audio file (testing)"), "file")
        f.addRow(tr("Type"), self.backend)
        drow = QHBoxLayout()
        self.device = QComboBox()
        self.device.setMinimumWidth(380)
        self.btn_rescan = QPushButton(tr("Rescan"))
        drow.addWidget(self.device, 1)
        drow.addWidget(self.btn_rescan)
        f.addRow(tr("Device"), drow)
        self.channels = QComboBox()
        for label_, ch in (("1 + 2 (stereo → mono)", [0, 1]), ("1 only", [0]), ("2 only", [1]),
                           ("3 + 4", [2, 3]), ("5 + 6", [4, 5]), ("7 + 8", [6, 7])):
            self.channels.addItem(label_, ch)
        f.addRow(tr("Channels"), self.channels)
        frow = QHBoxLayout()
        self.file = QLineEdit()
        self.btn_browse = QPushButton(tr("Browse…"))
        frow.addWidget(self.file, 1)
        frow.addWidget(self.btn_browse)
        f.addRow(tr("File"), frow)
        self.loop = QCheckBox(tr("Loop file"))
        f.addRow("", self.loop)
        self.hint = label("", "Faint", wrap=True)
        f.addRow("", self.hint)
        v.addWidget(src)

        self.detect = SourceDetectBox()
        self.detect.chosen.connect(self._use_detected)
        v.addWidget(self.detect)

        ana = QGroupBox(tr("Analysis (advanced)"))
        g = QFormLayout(ana)
        self.silence = QDoubleSpinBox(); self.silence.setRange(-90, -20); self.silence.setSuffix(" dBFS")
        self.sens = QDoubleSpinBox(); self.sens.setRange(0.25, 3.0); self.sens.setSingleStep(0.05)
        self.min_bpm = QDoubleSpinBox(); self.min_bpm.setRange(40, 150)
        self.max_bpm = QDoubleSpinBox(); self.max_bpm.setRange(90, 260)
        self.prior = QDoubleSpinBox(); self.prior.setRange(60, 200)
        self.bpb = QSpinBox(); self.bpb.setRange(2, 12)
        g.addRow(tr("Silence threshold"), self.silence)
        g.addRow(tr("Musical sensitivity"), self.sens)
        g.addRow(tr("BPM range min"), self.min_bpm)
        g.addRow(tr("BPM range max"), self.max_bpm)
        g.addRow(tr("Typical tempo (prior)"), self.prior)
        g.addRow(tr("Beats per bar (heuristic)"), self.bpb)
        g.addRow("", label(tr("Set the typical tempo near your genre (e.g. 95 for hip-hop, 174 for drum & bass) "
                           "to reduce half/double-time errors."), "Faint", wrap=True))
        v.addWidget(ana)

        mon = Card(tr("Live input"))
        self.meter = LevelMeter(16)
        mon.lay.addWidget(self.meter)
        self.mon_text = label("", "Muted", wrap=True)
        mon.lay.addWidget(self.mon_text)
        v.addWidget(mon)

        brow = QHBoxLayout()
        brow.addStretch()
        self.btn_apply = QPushButton(tr("Apply audio settings"))
        self.btn_apply.setObjectName("Primary")
        brow.addWidget(self.btn_apply)
        v.addLayout(brow)
        v.addStretch()

        self.backend.currentIndexChanged.connect(self._backend_changed)
        self.btn_rescan.clicked.connect(self._fill_devices)
        self.device.currentIndexChanged.connect(lambda _: self._update_hint())
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
            self.device.addItem(tr("(no devices found)"), None)
        selected = False
        for i in range(self.device.count()):
            data = self.device.itemData(i)
            if data and data[0] == cfg.device_name and (not cfg.host_api or data[1] == cfg.host_api):
                self.device.setCurrentIndex(i)
                selected = True
        if not selected and devs:
            # nothing configured yet: preselect the most plausible line input (e.g. UCA222)
            best = recommend(devs)
            for i in range(self.device.count()):
                if self.device.itemData(i) == (best.name, best.host_api):
                    self.device.setCurrentIndex(i)
        self._update_hint()

    def _update_hint(self) -> None:
        from dj_director.audio.devices import classify
        data = self.device.currentData()
        self.hint.setText(tr(classify(data[0]).hint) if data else "")

    def _use_detected(self, dev) -> None:
        self.backend.setCurrentIndex(max(0, self.backend.findData("loopback" if dev.kind == "loopback" else "input")))
        self._fill_devices()
        for i in range(self.device.count()):
            if self.device.itemData(i) == (dev.name, dev.host_api):
                self.device.setCurrentIndex(i)
        self.apply()

    def _backend_changed(self) -> None:
        b = self.backend.currentData()
        is_file = b == "file"
        for w in (self.file, self.btn_browse, self.loop):
            w.setEnabled(is_file)
        for w in (self.device, self.btn_rescan):
            w.setEnabled(not is_file)
        self._fill_devices()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Audio file"), "", "Audio (*.wav *.flac *.ogg *.aiff *.aif)")
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
            QMessageBox.warning(self, tr("BPM range"), tr("The BPM range must span at least a factor of 1.5."))
            return
        self.ctrl.apply_config(cfg, restart_audio=True)

    def refresh(self, s: AppSnapshot) -> None:
        a = s.audio
        self.meter.set_values(a.level_db, a.peak_db, a.clipping)
        if a.running:
            txt = tr("{0} · {1} Hz · {2} ch · level {3:.1f} dBFS · peak {4:.1f} dBFS · {5}signal {6} · "
                     "dropouts {7} · stream errors {8} · input latency {9:.0f} ms").format(
                a.device_name, a.sample_rate, a.channels, a.level_db, a.peak_db,
                "CLIPPING · " if a.clipping else "", tr("present") if a.signal_present else tr("absent"),
                a.overruns, a.stream_errors, a.capture_latency_ms)
            if s.music:
                txt += tr(" · analysis latency {0:.0f} ms").format(s.music.analysis_latency_ms)
        else:
            txt = a.error or tr("Not running")
        if a.digital_silence:
            txt = tr("⚠ The input delivers exact digital zeros. On Windows check Settings › Privacy & security › "
                     "Microphone › 'Let desktop apps access your microphone', and that the device is not muted. "
                     "(USB interfaces like the UCA222 appear to Windows as a microphone.)") + "  " + txt
        elif a.running and not a.signal_present:
            txt = tr("No signal: check the mixer output → interface input cable and the channel selection. "
                     "If the level is very low, raise the recording level in Windows Sound settings.") + "  " + txt
        self.mon_text.setText(txt)


# =========================================================================== OBS
class ObsPage(QWidget):
    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        page, v = page_scaffold(tr("OBS connection"), tr("Enable the server in OBS: Tools › WebSocket Server Settings "
                                "(OBS 28 or newer includes obs-websocket 5). DJ Director only reads the scene "
                                "list and changes the Program scene; it never creates, deletes or edits scenes, "
                                "sources, filters, audio, profiles, recording or streaming."))
        QVBoxLayout(self).addWidget(page)
        self.layout().setContentsMargins(0, 0, 0, 0)
        box = QGroupBox(tr("WebSocket server"))
        f = QFormLayout(box)
        self.host = QLineEdit()
        self.port = QSpinBox(); self.port.setRange(1, 65535)
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText(tr("stored encrypted (Windows DPAPI)"))
        self.auto = QCheckBox(tr("Connect automatically and reconnect if OBS restarts"))
        f.addRow(tr("Host"), self.host)
        f.addRow(tr("Port"), self.port)
        f.addRow(tr("Password"), self.password)
        f.addRow("", self.auto)
        v.addWidget(box)
        row = QHBoxLayout()
        self.btn_test = QPushButton(tr("Test connection"))
        self.btn_save = QPushButton(tr("Save && connect"))
        self.btn_save.setObjectName("Primary")
        row.addWidget(self.btn_test)
        row.addStretch()
        row.addWidget(self.btn_save)
        v.addLayout(row)
        self.result = label("", "Muted", wrap=True)
        v.addWidget(self.result)
        info = Card(tr("Studio Mode"))
        info.lay.addWidget(label(tr("When Studio Mode is enabled, DJ Director still switches the PROGRAM scene "
                                 "(what is recorded/streamed). The Preview scene is never changed. A scene change "
                                 "you make yourself in OBS is detected as a manual override and respected."),
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
        self.result.setText(tr("Testing…"))
        self.btn_test.setEnabled(False)
        from dj_director.obs.controller import ObsController
        self._worker.run(ObsController.test_connection, self.host.text().strip(), self.port.value(),
                         self.password.text())

    def _tested(self, res, exc) -> None:
        self.btn_test.setEnabled(True)
        if exc:
            self.result.setText(tr("Failed: {0}").format(exc))
            return
        ok, msg, scenes = res
        color = T.OK if ok else T.LIVE
        self.result.setStyleSheet(f"color: {color};")
        self.result.setText((tr("Connected: {0}") if ok else tr("Failed: {0}")).format(msg) +
                            (tr(" — {0} scenes: ").format(len(scenes)) + ", ".join(scenes[:12]) if ok else ""))

    def save(self) -> None:
        cfg = copy.deepcopy(self.ctrl.config)
        cfg.obs.host = self.host.text().strip() or "127.0.0.1"
        cfg.obs.port = self.port.value()
        cfg.obs.auto_connect = self.auto.isChecked()
        self.ctrl.set_password(self.password.text())
        self.ctrl.apply_config(cfg, reconnect_obs=True)
        self.result.setText(tr("Saved. Connecting…"))

    def refresh(self, s: AppSnapshot) -> None:
        o = s.obs
        if o.connected:
            extra = tr(" · OBS {0} · websocket {1} · Studio Mode {2} · Program: {3}").format(
                o.obs_version, o.ws_version, tr("on") if o.studio_mode else tr("off"), o.program_scene)
        else:
            extra = f" · {o.error}" if o.error else ""
        self.status.setText(tr("State: {0}").format(tr(o.state)) + extra)


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
    "hysteresis": ("Resistance to chaining two inserts", 0.0, 2.0, 0.05),
    "max_phrase_wait": ("Max wait for a phrase boundary (s)", 0.0, 30.0, 0.5),
    "coverage_master": ("Coverage target: master", 0.0, 1.0, 0.01),
    "coverage_detail": ("Coverage target: detail", 0.0, 1.0, 0.01),
    "coverage_side": ("Coverage target: side / behind", 0.0, 1.0, 0.01),
    "coverage_window": ("Coverage window (s)", 60.0, 1800.0, 30.0),
    "coverage_gain": ("Coverage budget strength", 0.0, 3.0, 0.05),
    "detail_hard_cap": ("Detail hard cap (s)", 12.0, 15.0, 0.5),
    "side_hard_cap": ("Side hard cap (s)", 20.0, 25.0, 0.5),
    "visual_novelty_threshold": ("Visual novelty threshold", 0.05, 1.0, 0.05),
    "mix_shot_cap": ("Mixer shot cap during a DJ gesture (s)", 15.0, 25.0, 0.5),
}


class DirectorPage(QWidget):  # noqa: D101
    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        page, v = page_scaffold(tr("Director"), tr("Choose a style. Presets are parameter sets of the same engine; "
                                "Natural is recommended. Advanced settings are optional."))
        QVBoxLayout(self).addWidget(page)
        self.layout().setContentsMargins(0, 0, 0, 0)
        prow = QHBoxLayout()
        self.preset_group = QButtonGroup(self)
        self.preset_cards = {}
        for i, name in enumerate(("minimal", "natural", "dynamic")):
            card = Card()
            rb = QRadioButton(tr(name.capitalize()) + (tr("  (recommended)") if name == "natural" else ""))
            f = rb.font(); f.setPointSizeF(12); f.setBold(True); rb.setFont(f)
            card.lay.addWidget(rb)
            card.lay.addWidget(label(tr(PRESET_DESCRIPTIONS[name]), "Muted", wrap=True))
            p = PRESETS[name]
            card.lay.addWidget(label(tr("Master ×{0:g} · inserts ×{1:g} · ≤{2:g} cuts/min").format(
                p.master_duration_scale, p.insert_duration_scale, p.max_cuts_per_minute), "Faint", wrap=True))
            self.preset_group.addButton(rb, i)
            self.preset_cards[name] = rb
            prow.addWidget(card)
        v.addLayout(prow)

        beh = QGroupBox(tr("Behaviour"))
        f = QFormLayout(beh)
        self.override = QComboBox()
        self.override.addItem(tr("Respect for a while, then resume"), "timed")
        self.override.addItem(tr("Respect until I press Resume"), "until_resume")
        self.override.addItem(tr("Adopt the new shot and continue"), "adopt")
        self.override_s = QDoubleSpinBox(); self.override_s.setRange(1, 3600); self.override_s.setSuffix(" s")
        self.nosig = QComboBox()
        self.nosig.addItem(tr("Return to master"), "master")
        self.nosig.addItem(tr("Hold current shot"), "hold")
        self.offset = QDoubleSpinBox(); self.offset.setRange(-500, 1000); self.offset.setSuffix(" ms")
        self.seed = QSpinBox(); self.seed.setRange(0, 2_000_000_000); self.seed.setSpecialValueText(tr("random"))
        self.shadow = QCheckBox(tr("Shadow mode by default (log decisions, never command OBS)"))
        self.logd = QCheckBox(tr("Write decision log (JSONL) for every session"))
        f.addRow(tr("Manual change in OBS"), self.override)
        f.addRow(tr("Manual override duration"), self.override_s)
        f.addRow(tr("When the audio signal is lost"), self.nosig)
        f.addRow(tr("Sync offset (send cut earlier by)"), self.offset)
        self.drop = QComboBox()
        f.addRow(tr("Shot opened on the drop"), self.drop)
        self.mixer = QComboBox()
        f.addRow(tr("Mixer shot for DJ gestures (EQ, filters)"), self.mixer)
        f.addRow(tr("Random seed (fixed = reproducible)"), self.seed)
        self.lang = QComboBox()
        self.lang.addItem(tr("Automatic"), "auto")
        self.lang.addItem("Italiano", "it")
        self.lang.addItem("English", "en")
        f.addRow("", self.shadow)
        f.addRow("", self.logd)
        f.addRow(tr("Interface language (restart required)"), self.lang)
        v.addWidget(beh)

        cal = QGroupBox(tr("Sync calibration"))
        cg = QGridLayout(cal)
        cg.addWidget(label(tr("Play a track with a clear kick and watch the OBS program output (or record a short "
                           "test): the two cameras alternate exactly on the beat grid. Move the slider until each "
                           "cut lands on the kick. Later cut → increase the offset. The value is saved when you "
                           "stop."), "Muted", wrap=True), 0, 0, 1, 4)
        self.cal_a, self.cal_b = QComboBox(), QComboBox()
        self.cal_every = QComboBox()
        for n in (4, 8, 16):
            self.cal_every.addItem(tr("every {0} beats").format(n), n)
        self.cal_every.setCurrentIndex(1)
        cg.addWidget(label(tr("Camera A"), "Muted"), 1, 0); cg.addWidget(self.cal_a, 1, 1)
        cg.addWidget(label(tr("Camera B"), "Muted"), 1, 2); cg.addWidget(self.cal_b, 1, 3)
        cg.addWidget(label(tr("Cut"), "Muted"), 2, 0); cg.addWidget(self.cal_every, 2, 1)
        self.cal_slider = QSlider(Qt.Horizontal)
        self.cal_slider.setRange(-200, 400)
        self.cal_value = label("", "Big")
        cg.addWidget(label(tr("Offset"), "Muted"), 3, 0)
        cg.addWidget(self.cal_slider, 3, 1, 1, 2)
        cg.addWidget(self.cal_value, 3, 3)
        self.btn_cal = QPushButton(tr("Start calibration"))
        self.btn_cal_stop = QPushButton(tr("Stop calibration"))
        cg.addWidget(self.btn_cal, 4, 1)
        cg.addWidget(self.btn_cal_stop, 4, 2)
        self.cal_msg = label("", "Faint", wrap=True)
        cg.addWidget(self.cal_msg, 5, 0, 1, 4)
        v.addWidget(cal)
        self.cal_slider.valueChanged.connect(self._offset_moved)
        self.btn_cal.clicked.connect(self._start_cal)
        self.btn_cal_stop.clicked.connect(self.ctrl.stop_director)

        self.adv = QGroupBox(tr("Advanced (overrides the preset)"))
        self.adv.setCheckable(True)
        g = QGridLayout(self.adv)
        self.param_widgets = {}
        for i, (k, (txt, lo, hi, step)) in enumerate(PARAM_LABELS.items()):
            w = QSpinBox() if isinstance(getattr(DirectorParams(), k), int) else QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setSingleStep(step)
            self.param_widgets[k] = w
            g.addWidget(label(tr(txt), "Muted"), i // 2, (i % 2) * 2)
            g.addWidget(w, i // 2, (i % 2) * 2 + 1)
        self.prefer_db = QCheckBox(tr("Prefer downbeats when confident"))
        g.addWidget(self.prefer_db, len(PARAM_LABELS) // 2 + 1, 0, 1, 2)
        self.phrase = QCheckBox(tr("Cut on 4/8-bar phrase boundaries after a drop"))
        g.addWidget(self.phrase, len(PARAM_LABELS) // 2 + 1, 2, 1, 2)
        self.editorial = QCheckBox(tr("Editorial inserts also in stable music (on musical boundaries)"))
        g.addWidget(self.editorial, len(PARAM_LABELS) // 2 + 2, 0, 1, 4)
        v.addWidget(self.adv)

        row = QHBoxLayout()
        self.btn_reset = QPushButton(tr("Reset advanced to preset"))
        self.btn_save = QPushButton(tr("Save director settings"))
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
        self.phrase.setChecked(params.phrase_sync)
        self.editorial.setChecked(params.editorial_cuts)
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
        self.lang.setCurrentIndex(max(0, self.lang.findData(self.ctrl.config.ui_language)))
        self.adv.setChecked(bool(d.overrides))
        self._show_params(keep_overrides=True)
        self.drop.clear()
        self.drop.addItem(tr("Automatic (wide / behind / side, or the AI's pick)"), "")
        self.drop.addItem(tr("Off (a drop returns to the master)"), "__off__")
        for c in self.ctrl.config.cameras:
            if c.enabled and not c.is_master:
                self.drop.addItem(c.scene_name, c.scene_name)
        reveal_on = dataclasses.replace(PRESETS.get(d.preset, PRESETS["natural"]), **d.overrides).drop_reveal
        key = d.drop_scene if reveal_on else "__off__"
        self.drop.setCurrentIndex(max(0, self.drop.findData(key)))
        self.mixer.clear()
        self.mixer.addItem(tr("Automatic (the DETAIL / OVERHEAD camera)"), "")
        self.mixer.addItem(tr("Off (gestures are ignored)"), "__off__")
        for c in self.ctrl.config.cameras:
            if c.enabled and not c.is_master:
                self.mixer.addItem(c.scene_name, c.scene_name)
        gestures_on = dataclasses.replace(PRESETS.get(d.preset, PRESETS["natural"]), **d.overrides).mix_gestures
        self.mixer.setCurrentIndex(max(0, self.mixer.findData(d.mixer_scene if gestures_on else "__off__")))
        names = [c.scene_name for c in self.ctrl.config.cameras if c.enabled]
        for i, combo in enumerate((self.cal_a, self.cal_b)):
            combo.clear()
            combo.addItems(names)
            if len(names) > i:
                combo.setCurrentIndex(i)
        self.cal_slider.blockSignals(True)
        self.cal_slider.setValue(int(d.latency_offset_ms))
        self.cal_slider.blockSignals(False)
        self.cal_value.setText(f"{d.latency_offset_ms:.0f} ms")

    def _offset_moved(self, v: int) -> None:
        self.cal_value.setText(f"{v} ms")
        self.offset.setValue(v)
        self.ctrl.set_latency_offset(v)

    def _start_cal(self) -> None:
        a, b = self.cal_a.currentText(), self.cal_b.currentText()
        if not a or not b or a == b:
            QMessageBox.warning(self, tr("Calibration"), tr("Choose two different cameras."))
            return
        self.ctrl.start_calibration(a, b, self.cal_every.currentData())

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
        cfg.ui_language = self.lang.currentData()
        d.overrides = {}
        if self.adv.isChecked():
            base = PRESETS[d.preset]
            for k, w in self.param_widgets.items():
                val = w.value()
                if abs(float(val) - float(getattr(base, k))) > 1e-9:
                    d.overrides[k] = val
            if self.prefer_db.isChecked() != base.prefer_downbeat:
                d.overrides["prefer_downbeat"] = self.prefer_db.isChecked()
            if self.phrase.isChecked() != base.phrase_sync:
                d.overrides["phrase_sync"] = self.phrase.isChecked()
            if self.editorial.isChecked() != base.editorial_cuts:
                d.overrides["editorial_cuts"] = self.editorial.isChecked()
        drop = self.drop.currentData()
        if drop == "__off__":
            d.overrides["drop_reveal"] = False
            d.drop_scene = ""
        else:
            d.drop_scene = drop or ""
        mix = self.mixer.currentData()
        if mix == "__off__":
            d.overrides["mix_gestures"] = False
            d.mixer_scene = ""
        else:
            d.mixer_scene = mix or ""
        self.ctrl.apply_config(cfg)

    def refresh(self, s: AppSnapshot) -> None:
        calibrating = s.director.value == "CALIBRATING"
        self.btn_cal.setEnabled(not calibrating and s.obs.connected)
        self.btn_cal_stop.setEnabled(calibrating)
        if calibrating:
            self.cal_msg.setText(s.director_message)
        elif not s.obs.connected:
            self.cal_msg.setText(tr("Calibration needs a connection to OBS."))
        else:
            self.cal_msg.setText("")

"""First-run setup wizard: OBS -> cameras -> audio -> preset -> finish."""

from __future__ import annotations

import copy

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFormLayout, QGridLayout, QHBoxLayout,
                               QLineEdit, QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
                               QWizard, QWizardPage)

from dj_director.audio.backends import list_input_devices, list_loopback_devices
from dj_director.config.presets import PRESET_DESCRIPTIONS
from dj_director.config.schema import CameraConfig, CameraRole
from dj_director.core.controller import AppController
from dj_director.obs.controller import ObsController
from dj_director.ui import theme as T
from dj_director.ui.settings_pages import CamerasPage
from dj_director.ui.widgets import LevelMeter, Worker, label


class _Page(QWizardPage):
    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        self.setTitle(title)
        self.setSubTitle(subtitle)
        self.v = QVBoxLayout(self)
        self.v.setSpacing(10)


class SetupWizard(QWizard):
    def __init__(self, ctrl: AppController, parent=None) -> None:
        super().__init__(parent)
        self.ctrl = ctrl
        self.cfg = copy.deepcopy(ctrl.config)
        self.scenes: list[str] = []
        self.setWindowTitle("DJ Director AI — Setup")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(760, 560)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self._worker = Worker()
        self._worker.done.connect(self._tested)

        # 1. welcome
        p = _Page("Welcome", "DJ Director follows your set and switches OBS cameras for you.")
        p.v.addWidget(label("You will: connect OBS, map your camera scenes to roles, choose the audio input "
                            "carrying your mixer's master output, and pick a directing style. Nothing in OBS is "
                            "created, deleted or modified — only the Program scene is switched while Auto "
                            "Director is running.", wrap=True))
        p.v.addStretch()
        self.addPage(p)

        # 2. OBS
        p = _Page("Connect OBS", "OBS › Tools › WebSocket Server Settings: enable the server, copy port and password.")
        f = QFormLayout()
        self.host = QLineEdit(self.cfg.obs.host)
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(self.cfg.obs.port)
        self.pw = QLineEdit(ctrl.get_password()); self.pw.setEchoMode(QLineEdit.Password)
        f.addRow("Host", self.host); f.addRow("Port", self.port); f.addRow("Password", self.pw)
        p.v.addLayout(f)
        b = QPushButton("Test connection")
        b.clicked.connect(self._test)
        p.v.addWidget(b, 0, Qt.AlignLeft)
        self.obs_result = label("Not tested yet. You can also skip and configure OBS later.", "Muted", wrap=True)
        p.v.addWidget(self.obs_result)
        p.v.addStretch()
        self.addPage(p)

        # 3. cameras
        self.cam_page = _Page("Cameras", "Tick the scenes that are camera shots and give each a role. "
                              "Choose one master (the main, wide-ish shot of the DJ).")
        self.cam_grid = QGridLayout()
        self.cam_page.v.addLayout(self.cam_grid)
        self.cam_page.v.addStretch()
        self.cam_page.initializePage = self._fill_cameras
        self.addPage(self.cam_page)

        # 4. audio
        p = _Page("Audio input", "Select the input that carries the mixer master (not a room microphone).")
        f = QFormLayout()
        self.backend = QComboBox()
        self.backend.addItem("Audio input (interface / mixer)", "input")
        self.backend.addItem("WASAPI loopback", "loopback")
        self.device = QComboBox(); self.device.setMinimumWidth(400)
        self.channels = QComboBox()
        for txt, ch in (("1 + 2", [0, 1]), ("1", [0]), ("2", [1]), ("3 + 4", [2, 3])):
            self.channels.addItem(txt, ch)
        f.addRow("Type", self.backend); f.addRow("Device", self.device); f.addRow("Channels", self.channels)
        p.v.addLayout(f)
        b2 = QPushButton("Use this input and check the signal")
        b2.clicked.connect(self._apply_audio)
        p.v.addWidget(b2, 0, Qt.AlignLeft)
        self.meter = LevelMeter(16)
        p.v.addWidget(self.meter)
        self.audio_result = label("Play some music on the mixer: the meter should move.", "Muted", wrap=True)
        p.v.addWidget(self.audio_result)
        p.v.addStretch()
        self.backend.currentIndexChanged.connect(self._fill_devices)
        self._fill_devices()
        self.addPage(p)

        # 5. preset
        p = _Page("Directing style", "You can change this any time.")
        self.preset_group = QButtonGroup(self)
        for i, name in enumerate(("minimal", "natural", "dynamic")):
            rb = QRadioButton(f"{name.capitalize()} — {PRESET_DESCRIPTIONS[name]}")
            rb.setChecked(name == self.cfg.director.preset)
            self.preset_group.addButton(rb, i)
            p.v.addWidget(rb)
        p.v.addStretch()
        self.addPage(p)

        # 6. finish
        p = _Page("Ready", "Recommended: start with a Shadow run to see the decisions without touching OBS.")
        self.shadow = QCheckBox("Use Shadow mode by default (decisions logged, OBS not commanded)")
        self.shadow.setChecked(True)
        p.v.addWidget(self.shadow)
        p.v.addWidget(label("From the Dashboard: Start Auto Director to go live, Pause/Resume, Stop, or Force "
                            "Master at any time. A scene change you make in OBS is always respected.",
                            "Muted", wrap=True))
        p.v.addStretch()
        self.addPage(p)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(100)

    # ------------------------------------------------------------------ OBS
    def _test(self) -> None:
        self.obs_result.setText("Testing…")
        self._worker.run(ObsController.test_connection, self.host.text().strip(), self.port.value(), self.pw.text())

    def _tested(self, res, exc) -> None:
        if exc or not res[0]:
            self.obs_result.setStyleSheet(f"color: {T.LIVE};")
            self.obs_result.setText(f"Failed: {exc or res[1]}")
            return
        ok, msg, scenes = res
        self.scenes = scenes
        self.obs_result.setStyleSheet(f"color: {T.OK};")
        self.obs_result.setText(f"Connected — {msg}. {len(scenes)} scenes found.")

    # ------------------------------------------------------------------ cameras
    def _fill_cameras(self) -> None:
        while self.cam_grid.count():
            w = self.cam_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        known = {c.scene_name: c for c in self.cfg.cameras}
        names = self.scenes or list(known)
        self.cam_rows = []
        self.master_group = QButtonGroup(self)
        for h, txt in enumerate(("Use", "Scene", "Role", "Master")):
            self.cam_grid.addWidget(label(txt, "CardTitle"), 0, h)
        if not names:
            self.cam_grid.addWidget(label("No scenes available (OBS not connected). You can map cameras later "
                                          "on the Cameras page.", "Muted", wrap=True), 1, 0, 1, 4)
        for i, n in enumerate(names, start=1):
            c = known.get(n)
            role = c.role if c else CamerasPage._guess_role(n)
            use = QCheckBox(); use.setChecked(bool(c) or role != CameraRole.CUSTOM)
            combo = QComboBox(); combo.addItems([r.value for r in CameraRole]); combo.setCurrentText(role.value)
            m = QRadioButton(); self.master_group.addButton(m, i)
            m.setChecked(bool(c and c.is_master) or (not known and role == CameraRole.MASTER
                                                     and self.master_group.checkedId() < 0))
            self.cam_grid.addWidget(use, i, 0)
            self.cam_grid.addWidget(label(n), i, 1)
            self.cam_grid.addWidget(combo, i, 2)
            self.cam_grid.addWidget(m, i, 3)
            self.cam_rows.append((n, use, combo, m))

    # ------------------------------------------------------------------ audio
    def _fill_devices(self) -> None:
        self.device.clear()
        devs = list_loopback_devices() if self.backend.currentData() == "loopback" else list_input_devices()
        for d in devs:
            self.device.addItem(d.label, (d.name, d.host_api))
            if d.name == self.cfg.audio.device_name:
                self.device.setCurrentIndex(self.device.count() - 1)
        if not devs:
            self.device.addItem("(no devices found)", None)

    def _audio_cfg(self, cfg) -> None:
        cfg.audio.backend = self.backend.currentData()
        data = self.device.currentData()
        if data:
            cfg.audio.device_name, cfg.audio.host_api = data
        cfg.audio.channels = self.channels.currentData()

    def _apply_audio(self) -> None:
        cfg = copy.deepcopy(self.ctrl.config)
        self._audio_cfg(cfg)
        self.ctrl.apply_config(cfg, restart_audio=True)

    def _poll(self) -> None:
        st = self.ctrl.audio.status()
        self.meter.set_values(st.level_db, st.peak_db, st.clipping)
        if st.running and self.currentId() == 3:
            self.audio_result.setText("Signal detected ✓" if st.signal_present else
                                      f"No signal yet ({st.level_db:.0f} dBFS). Check channels and mixer output.")
        elif st.error and self.currentId() == 3:
            self.audio_result.setText(st.error)

    # ------------------------------------------------------------------ finish
    def accept(self) -> None:
        cfg = copy.deepcopy(self.ctrl.config)
        cfg.obs.host, cfg.obs.port = self.host.text().strip() or "127.0.0.1", self.port.value()
        self.ctrl.set_password(self.pw.text())
        if getattr(self, "cam_rows", None):
            master = self.master_group.checkedId()
            cams = []
            for i, (n, use, combo, m) in enumerate(self.cam_rows, start=1):
                if use.isChecked() or i == master:
                    cams.append(CameraConfig(scene_name=n, role=CameraRole(combo.currentText()),
                                             enabled=True, is_master=(i == master)))
            if cams and not any(c.is_master for c in cams):
                cams[0].is_master = True
            cfg.cameras = cams
        self._audio_cfg(cfg)
        cfg.director.preset = ("minimal", "natural", "dynamic")[max(0, self.preset_group.checkedId())]
        cfg.shadow_mode = self.shadow.isChecked()
        cfg.first_run_done = True
        self.ctrl.apply_config(cfg, restart_audio=True, reconnect_obs=True)
        self._timer.stop()
        super().accept()

    def reject(self) -> None:
        self._timer.stop()
        super().reject()

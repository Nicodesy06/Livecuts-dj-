"""Dashboard: readiness, OBS / audio / director status, main actions, camera map, decisions."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget, QFrame)

from dj_director.core.controller import AppController, AppSnapshot, DirectorStatus
from dj_director.core.events import DirectorAction, MusicState
from dj_director.ui import theme as T
from dj_director.ui.widgets import Bar, Card, CameraTile, Dot, LevelMeter, StatusPill, label

STATE_COLORS = {
    MusicState.NO_SIGNAL: T.FAINT, MusicState.UNCERTAIN: T.MUTED, MusicState.STABLE_GROOVE: T.OK,
    MusicState.INCREASING: T.INFO, MusicState.DECREASING: T.INFO, MusicState.SPARSE: T.WARN,
    MusicState.TRANSITION: T.ACCENT, MusicState.REENTRY: T.LIVE,
}


def _fmt_dur(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


class DashboardPage(QWidget):
    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.setObjectName("Page")
        self.ctrl = ctrl
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        # --- header: title + director pill + actions
        head = QHBoxLayout()
        tcol = QVBoxLayout()
        tcol.addWidget(label("Dashboard", "PageTitle"))
        self.ready = label("", "PageSubtitle")
        tcol.addWidget(self.ready)
        head.addLayout(tcol)
        head.addStretch()
        self.pill = StatusPill("OFF")
        head.addWidget(self.pill, 0, Qt.AlignVCenter)
        root.addLayout(head)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.btn_start = QPushButton("Start Auto Director")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setProperty("class", "Big")
        self.btn_shadow = QPushButton("Start Shadow (no OBS changes)")
        self.btn_pause = QPushButton("Pause")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("Danger")
        self.btn_master = QPushButton("Force Master")
        for b in (self.btn_start, self.btn_shadow, self.btn_pause, self.btn_stop, self.btn_master):
            b.setMinimumHeight(42)
            b.setCursor(Qt.PointingHandCursor)
            actions.addWidget(b)
        actions.addStretch()
        root.addLayout(actions)
        self.btn_start.clicked.connect(lambda: ctrl.start_director(shadow=False))
        self.btn_shadow.clicked.connect(lambda: ctrl.start_director(shadow=True))
        self.btn_pause.clicked.connect(self._pause_resume)
        self.btn_stop.clicked.connect(ctrl.stop_director)
        self.btn_master.clicked.connect(ctrl.force_master)

        # --- status cards
        grid = QGridLayout()
        grid.setSpacing(14)
        # OBS
        c = Card("OBS Studio")
        row = QHBoxLayout()
        self.obs_dot = Dot()
        row.addWidget(self.obs_dot)
        self.obs_state = label("Disconnected", "Big")
        row.addWidget(self.obs_state)
        row.addStretch()
        c.lay.addLayout(row)
        self.obs_detail = label("", "Muted", wrap=True)
        c.lay.addWidget(self.obs_detail)
        self.obs_program = label("", "Muted", wrap=True)
        c.lay.addWidget(self.obs_program)
        c.lay.addStretch()
        grid.addWidget(c, 0, 0)
        # AUDIO
        c = Card("Audio & Music")
        row = QHBoxLayout()
        self.audio_dot = Dot()
        row.addWidget(self.audio_dot)
        self.audio_dev = label("No device", wrap=True)
        row.addWidget(self.audio_dev, 1)
        c.lay.addLayout(row)
        self.meter = LevelMeter()
        c.lay.addWidget(self.meter)
        bpm_row = QHBoxLayout()
        self.bpm = label("— BPM", "Big")
        bpm_row.addWidget(self.bpm)
        bpm_row.addStretch()
        self.state_pill = StatusPill("NO SIGNAL")
        bpm_row.addWidget(self.state_pill)
        c.lay.addLayout(bpm_row)
        self.beat_conf = Bar(T.OK)
        c.lay.addWidget(self.beat_conf)
        self.audio_detail = label("", "Faint", wrap=True)
        c.lay.addWidget(self.audio_detail)
        grid.addWidget(c, 0, 1)
        # DIRECTOR
        c = Card("Director")
        self.cur_cam = label("—", "Big")
        c.lay.addWidget(self.cur_cam)
        self.cur_detail = label("", "Muted")
        c.lay.addWidget(self.cur_detail)
        self.last_reason = label("", "Faint", wrap=True)
        c.lay.addWidget(self.last_reason)
        self.dir_msg = label("", "Faint", wrap=True)
        c.lay.addWidget(self.dir_msg)
        c.lay.addStretch()
        grid.addWidget(c, 0, 2)
        for i in range(3):
            grid.setColumnStretch(i, 1)
        root.addLayout(grid)

        # --- camera map
        cm = Card("Camera map")
        self.tiles_row = QHBoxLayout()
        self.tiles_row.setSpacing(10)
        cm.lay.addLayout(self.tiles_row)
        root.addWidget(cm)
        self.tiles: dict[str, CameraTile] = {}
        self._tiles_key = None

        # --- decisions
        dc = Card("Recent decisions")
        self.feed = QListWidget()
        self.feed.setMinimumHeight(140)
        self.feed.setFocusPolicy(Qt.NoFocus)
        dc.lay.addWidget(self.feed)
        root.addWidget(dc, 1)
        self._feed_count = 0

    def _pause_resume(self) -> None:
        if self.ctrl.status == DirectorStatus.PAUSED or self.ctrl.snapshot().override_active:
            self.ctrl.resume()
        else:
            self.ctrl.pause()

    def _rebuild_tiles(self) -> None:
        cams = [c for c in self.ctrl.config.cameras]
        key = tuple((c.scene_name, c.role.value, c.is_master) for c in cams)
        if key == self._tiles_key:
            return
        self._tiles_key = key
        while self.tiles_row.count():
            w = self.tiles_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.tiles = {}
        if not cams:
            self.tiles_row.addWidget(label("No cameras configured yet — open Cameras to map OBS scenes.",
                                           "Muted"))
            return
        for i, c in enumerate(cams):
            t = CameraTile(c.scene_name, c.role.value, c.is_master, T.CAMERA_COLORS[i % len(T.CAMERA_COLORS)])
            self.tiles[c.scene_name] = t
            self.tiles_row.addWidget(t)
        self.tiles_row.addStretch()

    # ------------------------------------------------------------------ polling
    def refresh(self, s: AppSnapshot) -> None:
        cfg = self.ctrl.config
        # readiness
        problems = []
        if not s.obs.connected and not s.shadow:
            problems.append("OBS not connected")
        n_cams = len([c for c in cfg.cameras if c.enabled])
        if n_cams < 2:
            problems.append("configure at least 2 cameras")
        if not cfg.master_camera():
            problems.append("no master camera")
        if not s.audio.signal_present:
            problems.append("no audio signal")
        if problems:
            self.ready.setText("Not ready: " + " · ".join(problems))
            self.ready.setStyleSheet(f"color: {T.WARN};")
        else:
            self.ready.setText("Ready to direct")
            self.ready.setStyleSheet(f"color: {T.OK};")

        st = s.director.value
        label_txt = st if not (s.override_active and st == "LIVE") else "LIVE · MANUAL"
        self.pill.set_status(label_txt, T.STATUS_COLORS.get(st, T.FAINT))
        running = s.director != DirectorStatus.OFF
        self.btn_start.setEnabled(not running or s.shadow)
        self.btn_shadow.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_pause.setEnabled(running)
        self.btn_pause.setText("Resume" if s.director == DirectorStatus.PAUSED or s.override_active else "Pause")
        self.btn_master.setEnabled(bool(cfg.master_camera()) and (s.obs.connected or s.shadow))

        # OBS
        o = s.obs
        col = {"connected": T.OK, "connecting": T.WARN, "auth_failed": T.LIVE}.get(o.state, T.FAINT)
        self.obs_dot.set_color(col)
        self.obs_state.setText({"connected": "Connected", "connecting": "Connecting…",
                                "auth_failed": "Wrong password"}.get(o.state, "Disconnected"))
        if o.connected:
            self.obs_detail.setText(f"OBS {o.obs_version} · {o.scene_collection or 'scene collection'}"
                                    f" · {len(o.scenes)} scenes" + (" · Studio Mode" if o.studio_mode else ""))
            self.obs_program.setText(f"Program: {o.program_scene}")
        else:
            self.obs_detail.setText(o.error[:140] if o.error else "Waiting for OBS (Tools › WebSocket Server Settings)")
            self.obs_program.setText("")

        # audio
        a = s.audio
        self.audio_dot.set_color(T.LIVE if a.clipping else T.OK if a.signal_present else
                                 T.WARN if a.running else T.FAINT)
        if a.running:
            name = a.device_name if len(a.device_name) < 48 else "…" + a.device_name[-46:]
            self.audio_dev.setText(f"{name}")
        else:
            self.audio_dev.setText(a.error or "No audio source selected")
        self.meter.set_values(a.level_db, a.peak_db, a.clipping)
        m = s.music
        if m and m.beat.bpm > 0 and m.beat.confidence >= 0.3:
            self.bpm.setText(f"{m.beat.bpm:.1f} BPM")
        else:
            self.bpm.setText("— BPM")
        if m:
            self.state_pill.set_status(m.state.label.upper(), STATE_COLORS.get(m.state, T.MUTED))
            self.beat_conf.set_value(m.beat.confidence, T.OK if m.beat.confidence >= 0.45 else T.WARN)
        else:
            self.state_pill.set_status("NO SIGNAL", T.FAINT)
            self.beat_conf.set_value(0)
        bits = []
        if a.running:
            bits.append(f"{a.sample_rate / 1000:.1f} kHz · {a.channels} ch")
            bits.append(f"{a.level_db:.0f} dBFS")
            if a.clipping:
                bits.append("CLIPPING")
            if a.overruns:
                bits.append(f"dropouts {a.overruns}")
        if m:
            bits.append(f"beat conf {m.beat.confidence:.2f}")
            bits.append(f"latency ~{s.latency.capture_ms + s.latency.analysis_ms:.0f} ms")
        self.audio_detail.setText(" · ".join(bits))

        # director
        self.cur_cam.setText(s.current_camera or "—")
        self.cur_detail.setText(f"on air {_fmt_dur(s.time_on_shot)} · preset {s.preset.capitalize()}"
                                f" · {s.cuts_last_minute} cuts/min" if s.current_camera else
                                f"preset {s.preset.capitalize()}")
        if s.last_switch:
            self.last_reason.setText(f"Last cut → {s.last_switch.candidate_camera}: {s.last_switch.reason}")
        else:
            self.last_reason.setText("")
        msg = s.director_message
        if s.last_decision and s.director in (DirectorStatus.LIVE, DirectorStatus.SHADOW):
            msg = s.last_decision.short()
        self.dir_msg.setText(msg)

        # camera map
        self._rebuild_tiles()
        avail = set(o.scenes) if o.connected else None
        for name, tile in self.tiles.items():
            camcfg = next((c for c in cfg.cameras if c.scene_name == name), None)
            tile.set_state(on_air=(name == s.current_camera and running),
                           pending=(name == s.pending_target),
                           enabled=bool(camcfg and camcfg.enabled),
                           available=(avail is None or name in avail))

        # decision feed (newest first)
        decs = list(self.ctrl.decisions)
        if len(decs) != self._feed_count or (decs and self.feed.count() and
                                              self.feed.item(0).data(Qt.UserRole) != id(decs[-1])):
            self._feed_count = len(decs)
            self.feed.clear()
            for d in reversed(decs[-40:]):
                color = {DirectorAction.SWITCH: T.LIVE if not s.shadow else T.INFO,
                         DirectorAction.SCHEDULE: T.WARN, DirectorAction.CANCEL: T.MUTED,
                         DirectorAction.OVERRIDE: T.ACCENT}.get(d.action, T.FAINT)
                it = QListWidgetItem(f"{d.action.value:<9}  {d.candidate_camera or d.current_camera or '':<18}  {d.reason}")
                it.setForeground(Qt.white if d.action == DirectorAction.SWITCH else Qt.gray)
                it.setData(Qt.UserRole, id(d))
                from PySide6.QtGui import QColor
                it.setForeground(QColor(color if d.action != DirectorAction.HOLD else T.MUTED))
                self.feed.addItem(it)

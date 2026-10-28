"""Dashboard: readiness, OBS / audio / director status, main actions, camera map, decisions."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget, QFrame)

from dj_director.ui.i18n import tr, tr_action, tr_decision, tr_reason, tr_state
from dj_director.core.controller import AppController, AppSnapshot, DirectorStatus
from dj_director.core.events import DirectorAction, MusicState
from dj_director.ui import theme as T

GESTURE_LABEL = {"eq_cut:low": "DJ: lows cut on the mixer", "eq_cut:high": "DJ: highs cut on the mixer",
                 "filter:low": "DJ: high-pass filter", "filter:high": "DJ: low-pass filter"}
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
        tcol.addWidget(label(tr("Dashboard"), "PageTitle"))
        self.ready = label("", "PageSubtitle")
        tcol.addWidget(self.ready)
        head.addLayout(tcol)
        head.addStretch()
        self.pill = StatusPill("OFF")
        head.addWidget(self.pill, 0, Qt.AlignVCenter)
        root.addLayout(head)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.btn_start = QPushButton(tr("Start Auto Director"))
        self.btn_start.setObjectName("Primary")
        self.btn_start.setProperty("class", "Big")
        self.btn_shadow = QPushButton(tr("Start Shadow (no OBS changes)"))
        self.btn_pause = QPushButton(tr("Pause"))
        self.btn_stop = QPushButton(tr("Stop"))
        self.btn_stop.setObjectName("Danger")
        self.btn_master = QPushButton(tr("Force Master"))
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
        c = Card(tr("OBS Studio"))
        row = QHBoxLayout()
        self.obs_dot = Dot()
        row.addWidget(self.obs_dot)
        self.obs_state = label(tr("Disconnected"), "Big")
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
        c = Card(tr("Audio & Music"))
        row = QHBoxLayout()
        self.audio_dot = Dot()
        row.addWidget(self.audio_dot)
        self.audio_dev = label(tr("No device"), wrap=True)
        row.addWidget(self.audio_dev, 1)
        c.lay.addLayout(row)
        self.meter = LevelMeter()
        c.lay.addWidget(self.meter)
        bpm_row = QHBoxLayout()
        self.bpm = label(tr("— BPM"), "Big")
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
        c = Card(tr("Director"))
        self.cur_cam = label("—", "Big")
        c.lay.addWidget(self.cur_cam)
        self.cur_detail = label("", "Muted")
        c.lay.addWidget(self.cur_detail)
        self.coverage = label("", "Faint", wrap=True)
        c.lay.addWidget(self.coverage)
        self.dir_msg = label("", "Faint", wrap=True)
        c.lay.addWidget(self.dir_msg)
        c.lay.addStretch()
        grid.addWidget(c, 0, 2)
        for i in range(3):
            grid.setColumnStretch(i, 1)
        root.addLayout(grid)

        # --- camera map
        cm = Card(tr("Camera map"))
        self.tiles_row = QHBoxLayout()
        self.tiles_row.setSpacing(10)
        cm.lay.addLayout(self.tiles_row)
        root.addWidget(cm)
        self.tiles: dict[str, CameraTile] = {}
        self._tiles_key = None

        # --- diagnostics (what is really on air, what comes next, what was refused)
        dg = Card(tr("Direction diagnostics"))
        g = QGridLayout()
        g.setHorizontalSpacing(18)
        self.diag = {}
        for i, (key, title) in enumerate((("program", "In Program"), ("quality", "Source"),
                                          ("why", "Last cut: why"), ("next", "Next cut"),
                                          ("airtime", "Real airtime (5 min)"), ("refused", "Refused / cancelled"))):
            g.addWidget(label(tr(title), "Faint"), i // 2, (i % 2) * 2)
            val = label("—", "Muted", wrap=True)
            g.addWidget(val, i // 2, (i % 2) * 2 + 1)
            self.diag[key] = val
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        dg.lay.addLayout(g)
        root.addWidget(dg)

        # --- decisions
        dc = Card(tr("Recent decisions"))
        self.feed = QListWidget()
        self.feed.setMinimumHeight(140)
        self.feed.setFocusPolicy(Qt.NoFocus)
        dc.lay.addWidget(self.feed)
        root.addWidget(dc, 1)
        self._feed_count = 0

    def _refresh_diagnostics(self, s: AppSnapshot) -> None:
        d = self.diag
        prog = s.program or "—"
        d["program"].setText(prog + (f" · {s.program_shot}" if s.program_shot else ""))
        issue = ""
        if s.vision is not None:
            issue = next((c.issue for c in s.vision.cameras if c.name == s.program and c.issue), "")
        q = {"HEALTHY": tr("healthy"), "UNKNOWN": tr("not verified"), "FAILED": tr("failed")}.get(
            s.program_quality, s.program_quality)
        d["quality"].setText(q + (f" · {tr(issue)}" if issue else ""))
        d["quality"].setStyleSheet(f"color: {T.OK if s.program_quality == 'HEALTHY' else T.LIVE if s.program_quality == 'FAILED' else T.MUTED};")
        why = ""
        if s.last_switch is not None:
            plan = dict(s.last_switch.plan)
            why = tr_reason(plan.get("why", s.last_switch.reason.split("; ")[0]))
            if plan.get("how_long"):
                why += f" · {plan['how_long']}"
        d["why"].setText(why or "—")
        if s.pending_target:
            plan = dict(s.pending_plan)
            nxt = f"{s.pending_target} · {tr('in {0:.1f} s').format(s.pending_in)}"
            if plan.get("why"):
                nxt += f" · {tr_reason(plan['why'])}"
            d["next"].setText(nxt)
        else:
            d["next"].setText("—")
        tot = sum(sec for _, sec in s.airtime) or 1.0
        d["airtime"].setText(" · ".join(f"{n} {sec / tot * 100:.0f}%" for n, sec in s.airtime[:5]) or "—")
        d["refused"].setText(tr_reason(s.last_cancel) if s.last_cancel else "—")

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
            self.tiles_row.addWidget(label(tr("No cameras configured yet — open Cameras to map OBS scenes."),
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
            problems.append(tr("OBS not connected"))
        n_cams = len([c for c in cfg.cameras if c.enabled])
        if n_cams < 2:
            problems.append(tr("configure at least 2 cameras"))
        if not cfg.master_camera():
            problems.append(tr("no master camera"))
        if not s.audio.signal_present:
            problems.append(tr("no audio signal"))
        if problems:
            self.ready.setText(tr("Not ready: ") + " · ".join(problems))
            self.ready.setStyleSheet(f"color: {T.WARN};")
        else:
            self.ready.setText(tr("Ready to direct"))
            self.ready.setStyleSheet(f"color: {T.OK};")

        st = s.director.value
        label_txt = st if not (s.override_active and st == "LIVE") else "LIVE · MANUAL"
        self.pill.set_status(label_txt, T.STATUS_COLORS.get(st, T.FAINT))
        running = s.director != DirectorStatus.OFF
        self.btn_start.setEnabled(not running or s.shadow)
        self.btn_shadow.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_pause.setEnabled(running)
        self.btn_pause.setText(tr("Resume") if s.director == DirectorStatus.PAUSED or s.override_active else tr("Pause"))
        self.btn_master.setEnabled(bool(cfg.master_camera()) and (s.obs.connected or s.shadow))

        # OBS
        o = s.obs
        col = {"connected": T.OK, "connecting": T.WARN, "auth_failed": T.LIVE}.get(o.state, T.FAINT)
        self.obs_dot.set_color(col)
        self.obs_state.setText(tr({"connected": "Connected", "connecting": "Connecting…",
                                   "auth_failed": "Wrong password"}.get(o.state, "Disconnected")))
        if o.connected:
            self.obs_detail.setText(f"OBS {o.obs_version} · {o.scene_collection or tr('scene collection')}"
                                    + tr(" · {0} scenes").format(len(o.scenes))
                                    + (" · Studio Mode" if o.studio_mode else ""))
            self.obs_program.setText(tr("Program: {0}").format(o.program_scene))
        else:
            self.obs_detail.setText(o.error[:140] if o.error else tr("Waiting for OBS (Tools › WebSocket Server Settings)"))
            self.obs_program.setText("")

        # audio
        a = s.audio
        self.audio_dot.set_color(T.LIVE if a.clipping else T.OK if a.signal_present else
                                 T.WARN if a.running else T.FAINT)
        if a.running:
            name = a.device_name
            if a.backend == "file":
                from pathlib import Path
                name = tr("File: {0}").format(Path(name).name)
            elif len(name) >= 48:
                name = "…" + name[-46:]
            self.audio_dev.setText(f"{name}")
        else:
            self.audio_dev.setText(a.error or tr("No audio source selected"))
        if a.digital_silence:
            self.audio_dev.setText(tr("Input is digitally silent: check Windows microphone privacy"))
            self.audio_dot.set_color(T.LIVE)
        self.meter.set_values(a.level_db, a.peak_db, a.clipping)
        m = s.music
        if m and m.beat.bpm > 0 and m.beat.confidence >= 0.3:
            self.bpm.setText(f"{m.beat.bpm:.1f} BPM")
        else:
            self.bpm.setText(tr("— BPM"))
        if m:
            self.state_pill.set_status(tr_state(m.state).upper(), STATE_COLORS.get(m.state, T.MUTED))
            self.beat_conf.set_value(m.beat.confidence, T.OK if m.beat.confidence >= 0.45 else T.WARN)
        else:
            self.state_pill.set_status(tr("NO SIGNAL"), T.FAINT)
            self.beat_conf.set_value(0)
        bits = []
        if a.running:
            bits.append(f"{a.sample_rate / 1000:.1f} kHz · {a.channels} ch")
            bits.append(f"{a.level_db:.0f} dBFS")
            if a.clipping:
                bits.append("CLIPPING")
            if a.overruns:
                bits.append(tr("dropouts {0}").format(a.overruns))
        if m:
            bits.append(tr("beat conf {0:.2f}").format(m.beat.confidence))
            bits.append(tr("latency ~{0:.0f} ms").format(s.latency.capture_ms + s.latency.analysis_ms))
        self.audio_detail.setText(" · ".join(bits))

        # director
        self.cur_cam.setText(s.current_camera or "—")
        self.cur_detail.setText(tr("on air {0} · preset {1} · {2} cuts/min").format(
            _fmt_dur(s.time_on_shot), tr(s.preset.capitalize()), s.cuts_last_minute) if s.current_camera else
            tr("preset {0}").format(tr(s.preset.capitalize())))
        if s.coverage:
            names = {"master": "Master", "detail": "Detail", "side": "Side"}
            self.coverage.setText(tr("Coverage (sliding window): ") + " · ".join(
                f"{names[k]} {share * 100:.0f}%/{target * 100:.0f}%" for k, share, target in s.coverage))
        else:
            self.coverage.setText("")
        vis = []
        if m is not None and m.mix_gesture:
            vis.append(tr(GESTURE_LABEL.get(m.mix_gesture, "DJ at work on the mixer")))
        if s.reveal_camera:
            vis.append(tr("drop → {0}").format(s.reveal_camera))
        if s.vision is not None and s.vision.enabled:
            bad = [c.name for c in s.vision.cameras if c.issue]
            seen = sum(1 for c in s.vision.cameras if c.has_frame and c.age <= 3)
            vis.append(tr("vision: {0}/{1} cameras seen").format(seen, len(s.vision.cameras)))
            if bad:
                vis.append(tr("avoided: {0}").format(", ".join(bad)))
            j = s.vision.judge
            if j is not None and j.hold:
                vis.append(tr("AI: stay on {0}").format(j.hold) + (f" ({j.reason})" if j.reason else ""))
            elif j is not None and j.next_pick:
                vis.append(tr("AI: next {0}").format(j.next_pick) + (f" ({j.reason})" if j.reason else ""))
            elif j is not None and j.note:
                vis.append(tr("AI: {0}").format(j.note))
        if vis:
            self.coverage.setText(self.coverage.text() + ("\n" if self.coverage.text() else "") + " · ".join(vis))
        self._refresh_diagnostics(s)
        msg = tr_reason(s.director_message)
        if s.last_decision and s.director in (DirectorStatus.LIVE, DirectorStatus.SHADOW):
            msg = tr_decision(s.last_decision)
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
                it = QListWidgetItem(f"{tr_action(d.action.value):<11}  {d.candidate_camera or d.current_camera or '':<18}  {tr_reason(d.reason)}")
                it.setForeground(Qt.white if d.action == DirectorAction.SWITCH else Qt.gray)
                it.setData(Qt.UserRole, id(d))
                from PySide6.QtGui import QColor
                it.setForeground(QColor(color if d.action != DirectorAction.HOLD else T.MUTED))
                self.feed.addItem(it)

"""Desktop smoke tests (offscreen Qt): first run, corrupt config, pages, wizard, simulation."""

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from dj_director.config.manager import ConfigManager
from dj_director.config.paths import config_dir
from dj_director.core.controller import AppController
from dj_director.ui import theme as T


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(T.STYLESHEET)
    return app


@pytest.fixture(autouse=True)
def _destroy_widgets(qapp):
    """Destroy every widget through Qt before Python's GC gets to them.

    Letting interpreter shutdown collect Qt objects in arbitrary order can
    segfault (seen on CI after all tests had passed).
    """
    yield
    import gc
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    for _ in range(3):
        qapp.processEvents()
    gc.collect()
    qapp.processEvents()


def _window(qapp):
    from dj_director.ui.main_window import MainWindow
    ctrl = AppController()
    ctrl.start_services()
    win = MainWindow(ctrl)
    win.show()
    return ctrl, win


def test_first_run_without_config_and_all_pages_refresh(qapp):
    ctrl, win = _window(qapp)
    try:
        assert not ctrl.config.first_run_done
        for i in range(win.nav.count()):
            win.nav.setCurrentRow(i)
            qapp.processEvents()
            win.poll()                         # must not raise with nothing connected
        assert "Not ready" in win.pages[0][1].ready.text()
    finally:
        ctrl.shutdown()


def test_corrupt_config_recovers(qapp):
    (config_dir() / "config.json").write_text("{broken", "utf-8")
    ctrl, win = _window(qapp)
    try:
        assert ctrl.config_manager.recovered_from_corruption
        win.poll()
    finally:
        ctrl.shutdown()


def test_wizard_constructs_and_finishes(qapp):
    from dj_director.ui.wizard import SetupWizard
    ctrl = AppController()
    try:
        w = SetupWizard(ctrl)
        w.show()
        qapp.processEvents()
        w.accept()
        assert ctrl.config.first_run_done
        assert ConfigManager().load().first_run_done        # persisted
    finally:
        ctrl.shutdown()


def test_director_page_saves_overrides(qapp):
    from dj_director.ui.settings_pages import DirectorPage
    ctrl = AppController()
    try:
        p = DirectorPage(ctrl)
        p.show()
        p.preset_cards["minimal"].setChecked(True)
        p.adv.setChecked(True)
        p.param_widgets["min_cut_gap"].setValue(12.0)
        p.save()
        cfg = ConfigManager().load()
        assert cfg.director.preset == "minimal" and cfg.director.overrides == {"min_cut_gap": 12.0}
    finally:
        ctrl.shutdown()


def test_simulation_page_runs(qapp):
    from dj_director.ui.tools_pages import SimulationPage
    ctrl = AppController()
    try:
        p = SimulationPage(ctrl)
        p.show()
        p.run()
        t0 = time.time()
        while p.result is None and time.time() - t0 < 60:
            qapp.processEvents()
            time.sleep(0.05)
        assert p.result is not None and p.result.shots
        assert p.table.item(0, 0) is not None
    finally:
        ctrl.shutdown()


def test_vision_page_live_view_roi_and_provider(qapp, tmp_path):
    pytest.importorskip("websockets")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from dj_director.config.schema import AppConfig, CameraConfig, CameraRole
    from dj_director.obs.fake_server import FakeObsServer
    from dj_director.ui.vision_page import VisionPage
    srv = FakeObsServer(scenes=["Cam M", "Cam D"]).start()
    srv.scene_look["Cam D"] = {"black": True}
    cfg = AppConfig(first_run_done=True)
    cfg.obs.port = srv.port
    cfg.cameras = [CameraConfig("Cam M", role=CameraRole.MASTER, is_master=True),
                   CameraConfig("Cam D", role=CameraRole.DETAIL)]
    ConfigManager().save(cfg)
    ctrl = AppController()
    ctrl.start_services()
    page = VisionPage(ctrl)
    page.resize(1100, 1800)
    page.show()
    try:
        assert page.roi_cam.currentText() == "Cam D"           # the detail camera is proposed first
        deadline = time.time() + 15
        while time.time() < deadline:
            qapp.processEvents()
            page.refresh(ctrl.snapshot())
            if (page.table.rowCount() == 2 and page.table.item(1, 6) and "black" in page.table.item(1, 6).text()
                    and page.table.item(0, 6).text() == "OK"):
                break
            time.sleep(0.1)
        assert "black" in page.table.item(1, 6).text() and page.table.item(0, 6).text() == "OK"
        assert page.roi.pix is not None                        # live picture of the selected camera
        r = page.roi._area()
        QTest.mousePress(page.roi, Qt.LeftButton, pos=QPoint(int(r.x() + 10), int(r.y() + 10)))
        QTest.mouseMove(page.roi, QPoint(int(r.x() + r.width() / 2), int(r.y() + r.height() / 2)))
        QTest.mouseRelease(page.roi, Qt.LeftButton, pos=QPoint(int(r.x() + r.width() / 2),
                                                               int(r.y() + r.height() / 2)))
        page.prov_buttons["local"].setChecked(True)
        assert page.local_box.isVisible() and not page.cloud_box.isVisible()
        page.prov_buttons["off"].setChecked(True)
        assert page.influence.currentData() == 1.0 and page.review_frames.isChecked()
        page.influence.setCurrentIndex(3)
        page.review_frames.setChecked(False)
        page.save()
        assert ctrl.config.vision.ai_influence == 1.6 and not ctrl.config.vision.review_frames
        roi = ctrl.config.vision.rois["Cam D"]
        assert 0.0 <= roi[0] < 0.1 and 0.4 < roi[2] <= 0.5
        assert ConfigManager().load().vision.rois["Cam D"] == roi        # persisted
    finally:
        page.close()
        ctrl.shutdown()
        srv.stop()


def test_cameras_page_framing_editor_saves_metadata_and_warns(qapp):
    from dj_director.config.schema import AppConfig, CameraConfig, CameraRole, ShotSize, Subject, ViewAngle
    from dj_director.ui.settings_pages import CamerasPage
    cfg = AppConfig(first_run_done=True)
    cfg.cameras = [CameraConfig("CAM1 Wide", role=CameraRole.MASTER, is_master=True),
                   CameraConfig("CAM1 Medium", role=CameraRole.SIDE)]
    ConfigManager().save(cfg)
    ctrl = AppController()
    page = CamerasPage(ctrl)
    try:
        assert page.framing.rowCount() == 2
        w = page.framing.cellWidget
        for r, size in ((0, "WIDE"), (1, "WIDE")):         # same camera, same scale: a jump cut setup
            w(r, 1).setCurrentIndex(w(r, 1).findData(size))
            w(r, 5).setText("cam1")
        w(1, 2).setCurrentIndex(w(1, 2).findData("DJ"))
        w(1, 3).setCurrentIndex(w(1, 3).findData("FRONT"))
        w(1, 7).setValue(1.6)
        page.save()
        saved = {c.scene_name: c for c in ConfigManager().load().cameras}
        m = saved["CAM1 Medium"]
        assert (m.shot_size, m.subject, m.view_angle, m.source_group, m.crop_zoom) == (
            ShotSize.WIDE, Subject.DJ, ViewAngle.FRONT, "cam1", 1.6)
        assert "jump cut" in page.framing_warn.text()
        w(1, 1).setCurrentIndex(w(1, 1).findData("MEDIUM"))
        page.save()
        assert page.framing_warn.text() == "No framing problems found."
    finally:
        page.close()
        ctrl.shutdown()


def test_log_page_reviews_a_set(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    from dj_director.ui.tools_pages import LogPage
    from tests.unit.test_slow_eye_director import _session
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    ctrl = AppController()
    page = LogPage(ctrl)
    page.show()
    try:
        page.start_review(str(_session(tmp_path)))           # slow eye off: numbers and rules only
        deadline = time.time() + 20
        while time.time() < deadline and not opened:
            qapp.processEvents()
            time.sleep(0.05)
        assert opened and opened[0].endswith("-review.html") and page.btn_review.isEnabled()
        assert "9" in page.review_msg.text()
    finally:
        page.close()
        ctrl.shutdown()


def test_director_page_cut_look_reads_obs_transitions_and_saves(qapp):
    pytest.importorskip("websockets")
    from dj_director.config.schema import AppConfig
    from dj_director.obs.fake_server import FakeObsServer
    from dj_director.ui.settings_pages import DirectorPage
    srv = FakeObsServer(scenes=["Cam M", "Cam D"]).start()
    cfg = AppConfig(first_run_done=True)
    cfg.obs.port = srv.port
    ConfigManager().save(cfg)
    ctrl = AppController()
    ctrl.start_services()
    page = DirectorPage(ctrl)
    page.show()
    try:
        assert not page.zoom.isChecked() and page.zoom_amount.value() == 4 and page.special_every.value() == 25
        deadline = time.time() + 10
        while time.time() < deadline and not ctrl.obs.connected:
            qapp.processEvents()
            time.sleep(0.05)
        page.btn_transitions.click()
        deadline = time.time() + 10
        while time.time() < deadline and page.special_name.count() < 3:
            qapp.processEvents()
            time.sleep(0.05)
        assert page.special_name.currentText() == "Stinger DJ"           # a real transition, not Cut / Fade
        page.zoom.setChecked(True)
        page.zoom_amount.setValue(6)
        page.special.setChecked(True)
        page.special_every.setValue(20)
        page.save()
        t = ctrl.config.transitions
        assert (t.zoom, t.zoom_amount, t.special, t.special_name, t.special_every) == (
            True, 0.06, True, "Stinger DJ", 20)
        assert ConfigManager().load().transitions == t
        deadline = time.time() + 5
        while time.time() < deadline and (ctrl.motion is None or ctrl.motion.state != "running"):
            time.sleep(0.05)
        assert ctrl.motion is not None and ctrl.motion.state == "running"      # zoom connection started
    finally:
        page.close()
        ctrl.shutdown()
        srv.stop()

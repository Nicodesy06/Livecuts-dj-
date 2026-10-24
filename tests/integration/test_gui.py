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
        p.run()
        t0 = time.time()
        while p.result is None and time.time() - t0 < 60:
            qapp.processEvents()
            time.sleep(0.05)
        assert p.result is not None and p.result.shots
        assert p.table.item(0, 0) is not None
    finally:
        ctrl.shutdown()

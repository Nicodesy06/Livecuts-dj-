"""Main window: sidebar navigation + pages, 10 Hz status polling, safe shutdown."""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QListWidget, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from dj_director import APP_NAME, __version__
from dj_director.core.controller import AppController, DirectorStatus
from dj_director.ui import theme as T
from dj_director.ui.dashboard import DashboardPage
from dj_director.ui.settings_pages import AudioPage, CamerasPage, DirectorPage, ObsPage
from dj_director.ui.tools_pages import LogPage, SimulationPage
from dj_director.ui.widgets import label
from dj_director.ui.wizard import SetupWizard

log = logging.getLogger(__name__)
ASSETS = Path(__file__).parent / "assets"


class MainWindow(QMainWindow):
    def __init__(self, ctrl: AppController) -> None:
        super().__init__()
        self.ctrl = ctrl
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 900)
        self.setMinimumSize(1080, 720)
        icon = ASSETS / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(220)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 12)
        sv.addWidget(label("DJ Director", "AppTitle"))
        sv.addWidget(label(f"AI multicam director · v{__version__}", "AppSubtitle"))
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        sv.addWidget(self.nav, 1)
        wiz = QPushButton("Setup wizard")
        wiz.clicked.connect(self.open_wizard)
        sv.addWidget(wiz)
        sv.setContentsMargins(10, 0, 10, 12)
        h.addWidget(side)
        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.pages = [
            ("Dashboard", DashboardPage(ctrl)),
            ("Cameras", CamerasPage(ctrl)),
            ("Audio", AudioPage(ctrl)),
            ("OBS", ObsPage(ctrl)),
            ("Director", DirectorPage(ctrl)),
            ("Simulation", SimulationPage(ctrl)),
            ("Decisions & log", LogPage(ctrl)),
        ]
        for name, page in self.pages:
            self.nav.addItem(name)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self._page_changed)
        self.nav.setCurrentRow(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    def _page_changed(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        page = self.pages[i][1]
        if hasattr(page, "reload") and i in (1,):
            page.reload()          # camera list follows OBS scenes

    def poll(self) -> None:
        try:
            snap = self.ctrl.snapshot()
            page = self.stack.currentWidget()
            page.refresh(snap)
            st = snap.director.value
            self.setWindowTitle(f"{APP_NAME} — {st}" + (f" — {snap.current_camera}" if snap.current_camera else ""))
        except Exception:
            log.exception("UI refresh failed")

    def open_wizard(self) -> None:
        w = SetupWizard(self.ctrl, self)
        if w.exec():
            for _, page in self.pages:
                if hasattr(page, "reload"):
                    page.reload()

    def closeEvent(self, e) -> None:
        if self.ctrl.status in (DirectorStatus.LIVE, DirectorStatus.ARMED):
            r = QMessageBox.question(self, "Quit", "Auto Director is running. Stop it and quit?\n"
                                     "(OBS recording/streaming is NOT affected.)")
            if r != QMessageBox.Yes:
                e.ignore()
                return
        self.timer.stop()
        self.ctrl.shutdown()
        e.accept()


def _install_excepthook() -> None:
    def hook(exc_type, exc, tb):
        log.critical("unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            QMessageBox.critical(None, APP_NAME, f"Unexpected error: {exc}\n\nDetails were written to the log. "
                                 "OBS is not affected.")
        except Exception:
            pass
    sys.excepthook = hook


def run(argv: list[str] | None = None, show_wizard: bool = True) -> int:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(T.STYLESHEET)
    _install_excepthook()
    ctrl = AppController()
    for issue in ctrl.config_manager.issues:
        log.warning("config: %s", issue)
    ctrl.start_services()
    win = MainWindow(ctrl)
    win.show()
    if ctrl.config_manager.recovered_from_corruption:
        QMessageBox.warning(win, APP_NAME, "The configuration file was damaged and has been reset.\n"
                            "A backup was kept next to it.")
    if show_wizard and not ctrl.config.first_run_done:
        QTimer.singleShot(300, win.open_wizard)
    return app.exec()

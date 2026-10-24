# PyInstaller spec: portable one-folder build with two executables sharing one runtime:
#   DJDirectorAI.exe      windowed desktop application
#   dj-director-cli.exe   console tools (simulate, devices, obs-test, selftest)
# Build:  pyinstaller --noconfirm packaging/dj_director.spec
import os
import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
ICON = os.path.join(SRC, "dj_director", "ui", "assets", "icon.ico")

datas = collect_data_files("dj_director", includes=["ui/assets/*"])
# PortAudio / libsndfile binaries shipped inside the sounddevice / soundfile wheels
for pkg in ("_sounddevice_data", "_soundfile_data"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

hiddenimports = ["dj_director.ui.main_window"]
if sys.platform == "win32":
    hiddenimports.append("pyaudiowpatch")      # optional WASAPI loopback

# Qt modules we do not use: keeps the folder small and avoids shipping WebEngine.
excludes = [
    "tkinter", "pytest", "websockets", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL",
    "PySide6.QtSvgWidgets", "PySide6.QtNetworkAuth", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSerialPort", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtHttpServer",
]

common = dict(pathex=[SRC], binaries=[], datas=datas, hiddenimports=hiddenimports, hookspath=[],
              runtime_hooks=[], excludes=excludes, noarchive=False)

a_gui = Analysis([os.path.join(SPECPATH, "launch_gui.py")], **common)
a_cli = Analysis([os.path.join(SPECPATH, "launch_cli.py")], **common)

pyz_gui = PYZ(a_gui.pure)
pyz_cli = PYZ(a_cli.pure)

exe_gui = EXE(pyz_gui, a_gui.scripts, [], exclude_binaries=True, name="DJDirectorAI",
              console=False, icon=ICON, upx=False)
exe_cli = EXE(pyz_cli, a_cli.scripts, [], exclude_binaries=True, name="dj-director-cli",
              console=True, icon=ICON, upx=False)

coll = COLLECT(exe_gui, exe_cli, a_gui.binaries, a_gui.datas, a_cli.binaries, a_cli.datas,
               strip=False, upx=False, name="DJDirectorAI")

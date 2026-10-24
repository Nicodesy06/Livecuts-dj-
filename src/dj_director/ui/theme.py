"""Dark theme: palette tokens + Qt stylesheet."""

from __future__ import annotations

BG = "#0e1014"
SURFACE = "#161920"
SURFACE_2 = "#1d2129"
SURFACE_3 = "#252a34"
BORDER = "#2a303b"
TEXT = "#e7e9ef"
MUTED = "#8a92a6"
FAINT = "#5b6273"
ACCENT = "#7c6cff"
ACCENT_HOVER = "#8f81ff"
LIVE = "#ff4d5e"
OK = "#2fd17c"
WARN = "#f5a524"
INFO = "#45a8ff"

def rgba(hex_color: str, alpha: float) -> str:
    """CSS rgba() from #rrggbb (Qt reads 8-digit hex as #AARRGGBB, so never append alpha)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {int(alpha * 255)})"


# camera colours for timelines / tiles (stable by index)
CAMERA_COLORS = ["#7c6cff", "#2fd17c", "#45a8ff", "#f5a524", "#ff7ab6", "#3fd6d0", "#c9a2ff", "#ffb86b"]

STATUS_COLORS = {
    "OFF": FAINT, "ARMED": WARN, "LIVE": LIVE, "SHADOW": INFO, "PAUSED": WARN, "ERROR": LIVE, "CALIBRATING": ACCENT,
}

STYLESHEET = f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 10pt;
    color: {TEXT};
}}
QMainWindow, QDialog, QWizard, QWidget#Page, QStackedWidget {{ background: {BG}; }}
QWidget#Sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QListWidget#Nav {{ background: transparent; border: none; outline: none; padding: 6px; }}
QListWidget#Nav::item {{ padding: 10px 14px; border-radius: 8px; color: {MUTED}; margin: 2px 0; }}
QListWidget#Nav::item:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget#Nav::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
QLabel#AppTitle {{ font-size: 13pt; font-weight: 600; padding: 18px 16px 4px 16px; }}
QLabel#AppSubtitle {{ color: {FAINT}; padding: 0 16px 14px 16px; font-size: 9pt; }}
QLabel#PageTitle {{ font-size: 18pt; font-weight: 600; }}
QLabel#PageSubtitle {{ color: {MUTED}; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Faint {{ color: {FAINT}; font-size: 9pt; }}
QLabel#CardTitle {{ color: {MUTED}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1px; }}
QLabel#Big {{ font-size: 20pt; font-weight: 600; }}
QLabel#Huge {{ font-size: 28pt; font-weight: 650; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#Banner {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QPushButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 9px;
    padding: 8px 16px; font-weight: 500;
}}
QPushButton:hover {{ background: {SURFACE_3}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; }}
QPushButton#Primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: {SURFACE_3}; border-color: {BORDER}; color: {FAINT}; }}
QPushButton#Danger {{ color: {LIVE}; }}
QPushButton#Big {{ padding: 12px 22px; font-size: 11pt; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {SURFACE_3}; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 2px solid {FAINT}; background: {SURFACE_2}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {MUTED}; }}
QGroupBox::indicator {{ width: 14px; height: 14px; border: 2px solid {FAINT}; border-radius: 4px; background: {SURFACE_2}; }}
QGroupBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 12px; margin-top: 14px; padding: 14px 12px 10px 12px; background: {SURFACE}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {MUTED}; font-weight: 600; }}
QTableWidget, QTableView, QListWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; gridline-color: {BORDER};
    alternate-background-color: {SURFACE_2};
}}
QHeaderView::section {{ background: {SURFACE_2}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 6px; font-weight: 600; }}
QTableWidget::item {{ padding: 4px; }}
QTableWidget::item:selected {{ background: {SURFACE_3}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 4px; min-width: 30px; }}
QProgressBar {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
QPlainTextEdit {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
"""

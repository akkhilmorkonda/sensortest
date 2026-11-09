"""The look of the station screen (GUI spec §6): status colours, symbols, number formats, stylesheet.

Status colours are reserved: green = PASS, red = FAIL, amber = ERROR and actuator ON, blue =
running, grey = not tested or pending. They are never used for anything else, and every status
also carries a symbol and a word, so nothing is told by colour alone.
"""

import pyqtgraph as pg
from PyQt6.QtWidgets import QApplication

# status kind -> (text colour, background)
STATUS: dict[str, tuple[str, str]] = {
    "pass": ("#1b6e34", "#e3f3e7"),
    "fail": ("#b42318", "#fde8e6"),
    "nt": ("#5f6670", "#eceef1"),
    "pend": ("#8a929c", "#ffffff"),
    "err": ("#8a5300", "#fff2d6"),
    "run": ("#1d5fbf", "#e6effc"),
}
SYMBOL = {"pass": "✓", "fail": "✗", "nt": "–", "pend": "", "err": "⚠", "run": ""}
WORD = {"pass": "PASS", "fail": "FAIL", "nt": "N/T", "pend": "…", "err": "ERROR", "run": "…"}
MUTED = "#555d68"
INK = "#1d2127"
PANEL = "#f4f6f8"
SIGNAL_PEN = "#3d5a80"  # the live signal: a neutral ink-blue, not a status colour
TONE_SHADE = (255, 214, 102, 45)  # tone windows in the enlarged plot (actuator ON = amber)
BASE_SHADE = (120, 130, 145, 28)  # baseline windows

STRIP_SECONDS = 2.0  # each live strip shows the last 2 s (spec §4.5)
TERMINAL_LINES = 1000  # the terminal keeps this many lines (spec §4.6)

# Decimals by unit (spec §4.4); values of 1000 or more in a.u. are shown as integers.
_DECIMALS = {"Hz": 3, "deg": 2, "a.u.": 1, "%": 1, "dB": 1, "samples": 0, "count": 0}
_UNIT_LABEL = {"deg": "°", "samples": "smpl"}


def unit_label(unit: str) -> str:
    return _UNIT_LABEL.get(unit, unit)


def fmt(value: float | None, unit: str) -> str:
    """A measured value or limit for the screen. None (unbounded, or not measured) is "—"."""
    if value is None:
        return "—"
    d = _DECIMALS.get(unit, 2)
    if abs(value) >= 1000 and d <= 1:
        return f"{value:.0f}"
    return f"{value:.{d}f}"


def chip(kind: str) -> str:
    """The status text for a result cell or chip, e.g. "✓ PASS"."""
    return f"{SYMBOL[kind]} {WORD[kind]}".strip()


QSS = f"""
QWidget {{ font-family: 'Segoe UI'; font-size: 10pt; color: {INK}; }}
QMainWindow, #central {{ background: #ffffff; }}
#titlebar {{ background: {PANEL}; border-bottom: 1px solid #d9dde3; }}
#app {{ font-weight: 600; font-size: 11pt; }}
#muted {{ color: {MUTED}; }}
QPushButton {{ padding: 5px 14px; border: 1px solid #c9ced6; border-radius: 5px; background: #ffffff;
  font-weight: 600; }}
QPushButton:disabled {{ color: #a3a9b1; }}
QPushButton[primary="true"]:enabled {{ background: #1d5fbf; color: white; border-color: #1d5fbf; }}
QPushButton[seg="true"] {{ border-radius: 0; padding: 3px 14px; font-weight: 400; }}
QPushButton[seg="true"]:checked {{ background: {INK}; color: white; border-color: {INK}; }}
QLineEdit {{ font-family: Consolas; font-size: 11pt; padding: 4px 6px; border: 1px solid #c9ced6;
  border-radius: 5px; background: #fafbfc; }}
QLineEdit[bad="true"] {{ border: 2px solid #b42318; }}
QComboBox {{ padding: 3px 8px; border: 1px solid #c9ced6; border-radius: 5px; background: #ffffff; }}
QFrame[seq="pend"] {{ border: 1px solid #d9dde3; border-radius: 6px; background: #ffffff; }}
QFrame[seq="done"] {{ border: 1px solid #b9dcc4; border-radius: 6px; background: #e3f3e7; }}
QFrame[seq="now"]  {{ border: 2px solid #1d5fbf; border-radius: 6px; background: #e6effc; }}
QFrame[seq="err"]  {{ border: 2px solid #c27c00; border-radius: 6px; background: #fff2d6; }}
QFrame#actuator[on="true"]  {{ border: 2px solid #e0a100; border-radius: 8px; background: #fff7dc; }}
QFrame#actuator[on="false"] {{ border: 2px solid #d9dde3; border-radius: 8px; background: #ffffff; }}
QLabel#lamp[on="true"]  {{ background: #f2b600; border: 3px solid #ffe08a; border-radius: 15px; }}
QLabel#lamp[on="false"] {{ background: #c9ced6; border: 3px solid #e7eaee; border-radius: 15px; }}
QFrame#banner[kind="idle"]  {{ background: {PANEL}; border: 2px solid #d9dde3; border-radius: 8px; }}
QFrame#banner[kind="run"]   {{ background: #e6effc; border: 2px solid #1d5fbf; border-radius: 8px; }}
QFrame#banner[kind="pass"]  {{ background: #e3f3e7; border: 2px solid #1b6e34; border-radius: 8px; }}
QFrame#banner[kind="fail"]  {{ background: #fde8e6; border: 2px solid #b42318; border-radius: 8px; }}
QFrame#banner[kind="err"]   {{ background: #fff2d6; border: 2px solid #c27c00; border-radius: 8px; }}
QFrame#banner[kind="abort"] {{ background: #eceef1; border: 2px solid #5f6670; border-radius: 8px; }}
QFrame#card {{ border: 1px solid #d9dde3; border-radius: 8px; background: #ffffff; }}
QLabel#cardtitle {{ font-weight: 600; }}
QTableView {{ border: none; gridline-color: #eef0f3; selection-background-color: transparent; }}
QHeaderView::section {{ background: {PANEL}; border: none; border-bottom: 1px solid #d9dde3;
  padding: 4px 6px; color: {MUTED}; font-weight: 600; }}
QPlainTextEdit#term {{ background: #1f242b; color: #cfd6df; font-family: Consolas; font-size: 9pt;
  border: none; }}
QToolButton {{ border: 1px solid #c9ced6; border-radius: 4px; padding: 2px 8px; background: #ffffff; }}
QToolButton:checked {{ background: {INK}; color: white; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ padding: 4px 12px; border: 1px solid #d9dde3; border-bottom: none;
  border-top-left-radius: 5px; border-top-right-radius: 5px; background: {PANEL}; color: {MUTED}; }}
QTabBar::tab:selected {{ background: #ffffff; color: {INK}; font-weight: 600; }}
QTreeWidget, QTableWidget {{ border: 1px solid #d9dde3; border-radius: 6px; }}
"""


def apply(app: QApplication) -> None:
    """Fusion style + the station stylesheet + pyqtgraph colours. Call once, before any window.

    Antialiasing stays off: drawing with it in the GUI thread starved the serial reader thread of
    the GIL and biased the host-timed line rate low by ~0.15% on the Teensy (149.72-149.92 Hz vs
    150.01-150.02 headless); with it off the GUI reads 149.97-150.02 (D-054,
    benchtop_testing/07_gui_line_rate). The 1% line-rate rule (D-008) needs that margin.
    """
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    pg.setConfigOptions(antialias=False, background="w", foreground=MUTED)

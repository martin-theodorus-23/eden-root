"""Eden Root: one window for chat, files and apps (PyQt6).

This file holds the main window and "the look" (colours, fonts, icons, buttons, the popup menu
and the animation helper). The apps in apps/ and the chat in chat/ read those names with
`from theme import ...`: "theme" is this file (see the line below the look), so there is no
separate theme file.
"""
import calendar
import ctypes
import importlib
import json
import math
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PyQt6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QSizePolicy, QStackedLayout, QVBoxLayout, QWidget)

# ======================================================================================
#  THE LOOK
# ======================================================================================
# ---- colours ----
BG = "#0e0f13"              # the window
SURFACE = "#16181e"         # cards: app windows
EDITOR = "#12141a"          # text areas inside a card
RAISED = "#1e2128"          # chips, inputs, menus
BORDER = "#2a2e38"
HOVER = "#2a2f3a"
ACCENT = "#7c9cff"
ACCENT_HOVER = "#94adff"
ACCENT_DIM = "#27304f"      # soft accent fill (selected, primary-soft buttons)
ACCENT_DIM_HOVER = "#34406a"
TAB_ACTIVE = "#232a3e"
FG = "#e9ebf1"
MUTED = "#8b91a3"
DANGER = "#ff7b8a"
DANGER_BG = "#3a2027"
ON_ACCENT = "#0e1120"       # text on a solid accent button
SELECTION = "#34406a"
CODE_BG = "#0b0c10"
# colours for code in Notepad (keys are what the highlighter calls each kind of word)
SYNTAX = {"keyword": "#c792ea", "string": "#a5d6a7", "number": "#f7a072", "comment": "#6b7385",
          "function": "#82aaff", "tag": "#ff8fa3", "attr": "#ffcb6b", "key": "#7dd3fc",
          "heading": "#7c9cff", "special": "#89ddff"}

FAMILY = "Segoe UI Variable Text"
TITLE_FAMILY = "Segoe UI Semibold"
MONO_FAMILY = "Consolas"

# Segoe Fluent Icons code points (Windows 11; Segoe MDL2 Assets has the same ones on Windows 10)
_CODES = {
    "add": 0xE710, "close": 0xE711, "minus": 0xE738, "chevron_down": 0xE70D,
    "left": 0xE76B, "right": 0xE76C, "layout": 0xE8A9, "apps": 0xECAA,
    "wifi": 0xE701, "volume": 0xE767, "mute": 0xE74F, "clock": 0xE823,
    "battery0": 0xE850, "battery3": 0xE853, "battery6": 0xE856, "battery9": 0xE859,
    "battery_full": 0xE83F, "charging": 0xE83E,
    "folder": 0xE838, "save": 0xE74E, "save_as": 0xE792, "page": 0xE7C3,
    "trash": 0xE74D, "stop": 0xE71A, "terminal": 0xE756, "note": 0xE70B,
    "slides": 0xE8B9, "thumbs": 0xE8FD, "check": 0xE73E, "check_circle": 0xE930,
    "chat": 0xE8BD, "send": 0xE724, "compose": 0xE932, "robot": 0xE99A,
    "refresh": 0xE72C, "home": 0xE80F, "globe": 0xE774, "external": 0xE8A7,
    "up": 0xE74A, "server": 0xE968, "copy": 0xE8C8, "download": 0xE896, "eye": 0xE7B3,
    "rename": 0xE8AC, "newfolder": 0xE8F4,
    "play": 0xE768, "pause": 0xE769, "image": 0xEB9F, "document": 0xE8A5, "zoom_in": 0xE8A3,
    "zoom_out": 0xE71F,
}
ICONS = {name: chr(code) for name, code in _CODES.items()}
APP_ICONS = {"notepad": "page", "terminal": "terminal", "ppt_viewer": "slides", "browser": "globe",
             "files": "folder", "servers": "server", "media_viewer": "image", "pdf_viewer": "document",
             "edge": "play"}

# ---- motion: tests and anyone who dislikes animation can set MOTION["on"] = False ----
MOTION = {"on": True, "ms": 220}


def mix(a, b, t):
    """The colour a fraction t of the way from hex colour a to hex colour b."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def lerp(a, b, t):
    return a + (b - a) * t


def ease_out(t):
    """Fast start, gentle stop."""
    return 1 - (1 - t) ** 3


def ease_in(t):
    return t ** 3


def ease_in_out(t):
    return 3 * t * t - 2 * t * t * t


# Qt does the timing; this only turns "progress 0 -> 1" into calls of step(progress).
_live = set()                                   # running animations (Qt would drop ones nobody holds)


def tween(step, ms=None, done=None, ease=ease_out):
    """Call step(progress) over `ms` milliseconds; progress goes 0 -> 1 (shaped by `ease`).

    Returns {"finish": jump to the end now, "cancel": stop where it is, "running": function}.
    With MOTION["on"] False (or ms 0) it jumps to the end at once. If what the step moves has been
    deleted meanwhile, the animation just ends."""
    ms = MOTION["ms"] if ms is None else ms
    state = {"over": False}
    anim = QVariantAnimation()

    def safe(progress):
        try:
            step(progress)
            return True
        except RuntimeError:                    # the widget was deleted mid-animation
            state["over"] = True
            return False

    def finish():
        if state["over"]:
            return
        state["over"] = True
        anim.stop()
        _live.discard(anim)
        if safe(1.0) and done:
            done()

    def cancel():
        state["over"] = True
        anim.stop()
        _live.discard(anim)

    handle = {"finish": finish, "cancel": cancel, "running": lambda: not state["over"]}
    if not MOTION["on"] or ms <= 0:
        finish()
        return handle
    anim.setDuration(max(1, ms))
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.valueChanged.connect(lambda value: None if state["over"] else (safe(ease(float(value))) or cancel()))
    anim.finished.connect(finish)
    _live.add(anim)
    if safe(0.0):
        anim.start()
    else:
        cancel()
    return handle


# ---- the application, fonts, dark palette, style sheet ----
try:                                           # the Browser app needs this before the application exists
    from PyQt6 import QtWebEngineWidgets       # noqa: F401
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
except ImportError:
    pass
qt_app = QApplication.instance() or QApplication(sys.argv)
qt_app.setApplicationName("Eden Root")         # where profiles keep their data on disk
qt_app.setOrganizationName("Eden")
qt_app.setStyle("Fusion")


def font(size=13, weight="normal", family=FAMILY):
    """A font `size` pixels high. weight: normal, medium or bold."""
    f = QFont()
    f.setFamilies([family, "Segoe UI"])
    f.setPixelSize(size)
    f.setWeight({"normal": QFont.Weight.Normal, "medium": QFont.Weight.Medium,
                 "bold": QFont.Weight.Bold}[weight])
    return f


def title_font(size=13):
    return font(size, "normal", TITLE_FAMILY)


def mono_font(size=14):
    return font(size, "normal", MONO_FAMILY)


_icon_family = {}


def icon_font(size=16):
    if not _icon_family:
        families = set(QFontDatabase.families())
        _icon_family["name"] = ("Segoe Fluent Icons" if "Segoe Fluent Icons" in families
                                else "Segoe MDL2 Assets")
    f = QFont(_icon_family["name"])
    f.setPixelSize(size)
    return f


qt_app.setFont(font(13))
_palette = QPalette()
for _role, _color in ((QPalette.ColorRole.Window, BG), (QPalette.ColorRole.WindowText, FG),
                      (QPalette.ColorRole.Base, EDITOR), (QPalette.ColorRole.AlternateBase, SURFACE),
                      (QPalette.ColorRole.Text, FG), (QPalette.ColorRole.Button, RAISED),
                      (QPalette.ColorRole.ButtonText, FG), (QPalette.ColorRole.Highlight, SELECTION),
                      (QPalette.ColorRole.HighlightedText, FG), (QPalette.ColorRole.ToolTipBase, RAISED),
                      (QPalette.ColorRole.ToolTipText, FG), (QPalette.ColorRole.PlaceholderText, MUTED),
                      (QPalette.ColorRole.Link, ACCENT)):
    _palette.setColor(_role, QColor(_color))
qt_app.setPalette(_palette)

QSS = f"""
QToolTip {{ background: {RAISED}; color: {FG}; border: 1px solid {BORDER}; padding: 4px 6px; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px 2px 2px 2px; }}
QScrollBar::handle:vertical {{ background: {HOVER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {HOVER}; border-radius: 4px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QPushButton {{ background: {RAISED}; color: {FG}; border: none; border-radius: 9px; padding: 0 12px; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton[kind="ghost"] {{ background: transparent; }}
QPushButton[kind="ghost"]:hover {{ background: {HOVER}; }}
QPushButton[kind="soft"] {{ background: {RAISED}; }}
QPushButton[kind="accent"] {{ background: {ACCENT_DIM}; color: {ACCENT}; }}
QPushButton[kind="accent"]:hover {{ background: {ACCENT_DIM_HOVER}; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[kind="danger"] {{ background: {DANGER_BG}; color: {DANGER}; }}
QPushButton[kind="icon"] {{ background: transparent; color: {MUTED}; border-radius: 8px; padding: 0; }}
QPushButton[kind="icon"]:hover {{ background: {HOVER}; }}
QPushButton[kind="icon"]:disabled, QPushButton[kind="icon"][strong="true"]:disabled {{ color: {BORDER}; }}
QPushButton[kind="icon"][danger="true"]:hover {{ background: {DANGER_BG}; color: {DANGER}; }}
QPushButton[kind="icon"][strong="true"] {{ color: {FG}; }}
QPushButton[kind="icon"][on="true"] {{ background: {ACCENT_DIM}; color: {ACCENT}; }}
QPushButton[kind="toggle"] {{ background: transparent; color: {FG}; }}
QPushButton[kind="toggle"]:hover {{ background: {HOVER}; }}
QPushButton[kind="toggle"][on="true"] {{ background: {ACCENT_DIM}; color: {ACCENT}; }}
QPushButton[kind="toggle"][on="true"]:hover {{ background: {ACCENT_DIM_HOVER}; }}
QFrame#chip {{ background: {RAISED}; border-radius: 11px; }}
QFrame#chip[tiny="true"] {{ background: transparent; }}
QFrame#pane {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#volumecard, QFrame#popcard {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#popline {{ background: {BORDER}; border: none; }}
QFrame#volumetrack {{ background: {HOVER}; border: none; border-radius: 3px; }}
QFrame#volumefill {{ background: {ACCENT}; border: none; border-radius: 3px; }}
QFrame#dropmark {{ background: rgba(124, 156, 255, 46); border: 2px solid {ACCENT}; border-radius: 14px; }}
QFrame#menubox {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#menurow {{ background: transparent; border: none; border-radius: 8px; }}
QFrame#menurow:hover {{ background: {HOVER}; }}
QFrame#menutile {{ background: transparent; border: none; border-radius: 10px; }}
QFrame#menutile:hover {{ background: {HOVER}; }}
QPlainTextEdit, QTextEdit, QTextBrowser {{ background: {EDITOR}; color: {FG}; border: none;
    border-radius: 10px; padding: 6px; selection-background-color: {SELECTION}; selection-color: {FG}; }}
QLineEdit {{ background: transparent; color: {FG}; border: none; selection-background-color: {SELECTION}; }}
QTreeView {{ background: {EDITOR}; color: {FG}; border: none; border-radius: 10px; padding: 4px; outline: 0;
    selection-background-color: {ACCENT_DIM}; selection-color: {FG}; }}
QTreeView::item {{ height: 24px; border-radius: 6px; }}
QTreeView::item:hover {{ background: {HOVER}; }}
QTreeView::item:selected {{ background: {ACCENT_DIM}; color: {FG}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: transparent; color: {MUTED}; border: none; padding: 2px 6px; }}
QLabel {{ background: transparent; }}
QMenu, QDialog, QMessageBox {{ background: {SURFACE}; color: {FG}; }}
"""
qt_app.setStyleSheet(QSS)


def label(text="", size=13, color=FG, weight="normal", family=FAMILY, parent=None):
    w = QLabel(text, parent)
    w.setFont(font(size, weight, family))
    w.setStyleSheet(f"color: {color};")
    return w


def set_color(widget, color):
    widget.setStyleSheet(f"color: {color};")


def icon_label(name, size=16, color=MUTED, parent=None):
    w = QLabel(ICONS[name], parent)
    w.setFont(icon_font(size))
    w.setStyleSheet(f"color: {color};")
    w.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return w


def button(text, command, kind="ghost", width=None, height=28, size=13):
    """A rounded text button. kind: ghost (no fill), soft, accent (tinted) or primary (solid).
    A toggle-looking button uses kind "toggle" and set_on(button, True/False)."""
    b = QPushButton(text)
    b.setProperty("kind", kind)
    b.setFont(font(size))
    b.setFixedHeight(height)
    b.setMinimumWidth(width or 26 + 8 * len(text))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.clicked.connect(lambda checked=False: command())
    return b


def icon_button(name, command, size=28, danger=False, strong=False, glyph_size=14):
    """A square button showing an icon. danger=True turns it red on hover (for close)."""
    b = QPushButton(ICONS[name])
    b.setProperty("kind", "icon")
    b.setProperty("danger", "true" if danger else "false")
    b.setProperty("strong", "true" if strong else "false")
    b.setFont(icon_font(glyph_size))
    b.setFixedSize(size, size)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.clicked.connect(lambda checked=False: command())
    return b


def set_on(btn, on):
    """A toggle button looks tinted while its panel is shown."""
    btn.setProperty("on", "true" if on else "false")
    btn.style().unpolish(btn)
    btn.style().polish(btn)


def dark_titlebar(widget):
    """Windows: a dark title bar in the colours of the window."""
    try:
        if qt_app.platformName() != "windows":
            return
        hwnd = ctypes.c_void_p(int(widget.winId()))
        dwm = ctypes.windll.dwmapi
        for attribute, value in ((20, 1), (35, 0x130F0E), (36, 0xF1EBE9)):   # dark mode, caption, text
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(ctypes.c_int(value)), 4)
    except Exception:
        pass


# ---- popup menu: rounded, with icons and a check mark, fades in under a button ----
POPUP = {"top": None, "rows": []}          # the menu that is open now (tests look in here)


def close_popup():
    top = POPUP["top"]
    POPUP["top"], POPUP["rows"] = None, []
    if top is not None:
        try:
            top.close()
            top.deleteLater()
        except RuntimeError:
            pass


class MenuRow(QFrame):
    def __init__(self, text, command, icon, checked):
        super().__init__()
        self.setObjectName("menurow")
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label_text, self.checked, self.command = text, checked, command
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(6)
        glyph = icon_label(icon, 15, ACCENT if checked else MUTED) if icon else QLabel()
        glyph.setFixedWidth(26)
        name = label(text, 13, FG)
        tick = icon_label("check", 13, ACCENT) if checked else QLabel()
        tick.setFixedWidth(26)
        for child in (glyph, name, tick):                # the row itself must see the mouse
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(glyph)
        row.addWidget(name, 1)
        row.addWidget(tick)

    def run(self):
        command = self.command
        close_popup()
        command()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.run()


class Popup(QWidget):
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            close_popup()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        if POPUP["top"] is self:
            POPUP["top"], POPUP["rows"] = None, []
        super().closeEvent(event)


def popup_menu(anchor, items, width=210, align="left", at=None):
    """Open a menu just under `anchor` (or at the screen point `at`, e.g. where a right-click was).
    items: list of (label, command, icon_name_or_None, checked).
    A click runs the command and closes the menu; Esc or clicking elsewhere closes it too."""
    close_popup()
    top = Popup(anchor.window())
    top.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                       | Qt.WindowType.NoDropShadowWindowHint)
    top.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    outer = QVBoxLayout(top)
    outer.setContentsMargins(0, 0, 0, 0)
    box = QFrame()
    box.setObjectName("menubox")
    outer.addWidget(box)
    column = QVBoxLayout(box)
    column.setContentsMargins(6, 6, 6, 6)
    column.setSpacing(0)
    POPUP["top"], POPUP["rows"] = top, []
    for text, command, icon, checked in items:
        row = MenuRow(text, command, icon, checked)
        column.addWidget(row)
        POPUP["rows"].append(row)
    return place_popup(top, anchor, width, 34 * len(items) + 14, align, at)


class MenuTile(QFrame):
    """One entry of a grid menu: a big icon with its name under it."""

    def __init__(self, text, command, icon):
        super().__init__()
        self.setObjectName("menutile")
        self.setFixedSize(TILE_W, TILE_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label_text, self.checked, self.command = text, False, command
        column = QVBoxLayout(self)
        column.setContentsMargins(4, 10, 4, 6)
        column.setSpacing(6)
        glyph = icon_label(icon or "apps", 26, ACCENT)
        glyph.setFixedHeight(32)
        name = label(text, 12, FG)
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        name.setWordWrap(True)
        for child in (glyph, name):                      # the tile itself must see the mouse
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            column.addWidget(child)

    def run(self):
        command = self.command
        close_popup()
        command()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.run()


TILE_W, TILE_H, TILE_GAP = 100, 86, 4


def popup_grid(anchor, items, columns=3, align="left"):
    """Like popup_menu, but the entries are tiles in a grid (icon above name), `columns` to a row.
    items: list of (label, command, icon_name_or_None, checked), the same as popup_menu's."""
    close_popup()
    top = Popup(anchor.window())
    top.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                       | Qt.WindowType.NoDropShadowWindowHint)
    top.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    outer = QVBoxLayout(top)
    outer.setContentsMargins(0, 0, 0, 0)
    box = QFrame()
    box.setObjectName("menubox")
    outer.addWidget(box)
    grid = QGridLayout(box)
    grid.setContentsMargins(8, 8, 8, 8)
    grid.setSpacing(TILE_GAP)
    POPUP["top"], POPUP["rows"] = top, []
    for i, (text, command, icon, checked) in enumerate(items):
        tile = MenuTile(text, command, icon)
        grid.addWidget(tile, i // columns, i % columns)
        POPUP["rows"].append(tile)
    used = min(columns, max(1, len(items)))
    lines = max(1, -(-len(items) // columns))
    width = used * TILE_W + (used - 1) * TILE_GAP + 16 + 2
    height = lines * TILE_H + (lines - 1) * TILE_GAP + 16 + 2
    return place_popup(top, anchor, width, height, align, None)


def place_popup(top, anchor, width, height, align, at):
    """Size a popup, put it under its anchor (or at a screen point) and fade it in."""
    top.resize(width, height)
    if at is not None:
        screen = QGuiApplication.screenAt(at) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left(), min(at.x(), area.right() - width))          # keep it on the screen
        y = max(area.top(), min(at.y(), area.bottom() - height))
    else:
        corner = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        x = corner.x() if align == "left" else corner.x() + anchor.width() - width
        y = corner.y()
    top.move(x, y - 6)
    top.setWindowOpacity(0.0)
    top.show()

    def step(t):
        top.setWindowOpacity(t)
        top.move(x, round(y - 6 * (1 - t)))

    tween(step, ms=130)
    return top


# ======================================================================================
#  THE WINDOW
# ======================================================================================
# From here on "theme" means this file: apps and chat import their colours, fonts and helpers
# from it. (This must come before anything below imports them.)
sys.modules["theme"] = sys.modules[__name__]

from chat import tools as chat_tools                                    # noqa: E402
from chat import ui as chat_ui                                          # noqa: E402

GAP = 10                          # space between app windows


class Root(QWidget):
    """The main window. Closing it asks every app first (e.g. to save)."""

    def closeEvent(self, event):
        if on_close():
            event.accept()
        else:
            event.ignore()


root = Root()
root.setWindowTitle("Eden Root")
root.resize(1280, 820)
root.setMinimumSize(760, 500)
root.setAutoFillBackground(True)
root.setStyleSheet(f"Root {{ background: {BG}; }}")
page = QVBoxLayout(root)
page.setContentsMargins(10, 6, 10, 10)
page.setSpacing(0)


# ---- system status (netsh and pycaw are slow, so they run off the window thread) ----

def get_wifi():
    """Connected network name, 'offline', or 'n/a' on error."""
    try:
        out = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        if "disconnected" in out.lower() or "SSID" not in out:
            return "offline"
        for line in out.splitlines():
            if line.strip().startswith("SSID") and "BSSID" not in line:
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "n/a"


def get_battery():
    """'85%' or '85% charging'; 'none' if there is no battery, 'n/a' on error."""
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is None:
            return "none"
        return f"{int(b.percent)}%" + (" charging" if b.power_plugged else "")
    except Exception:
        return "n/a"


def get_volume():
    """'40%' or 'muted'; 'n/a' on error."""
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
        comtypes.CoInitialize()
        speakers = AudioUtilities.GetSpeakers()
        if speakers.EndpointVolume.GetMute():
            return "muted"
        return f"{round(speakers.EndpointVolume.GetMasterVolumeLevelScalar() * 100)}%"
    except Exception:
        return "n/a"


VOLUME_STEP = 2      # percent per wheel notch, like the Windows taskbar


def change_volume(steps):
    """Move the speaker volume up (steps > 0) or down by VOLUME_STEP percent a step, between 0 and 100.
    Going up also un-mutes. Returns the new reading, like get_volume()."""
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
        comtypes.CoInitialize()
        endpoint = AudioUtilities.GetSpeakers().EndpointVolume
        percent = round(endpoint.GetMasterVolumeLevelScalar() * 100) + steps * VOLUME_STEP
        endpoint.SetMasterVolumeLevelScalar(min(100, max(0, percent)) / 100, None)
        if steps > 0 and endpoint.GetMute():
            endpoint.SetMute(0, None)
    except Exception:
        pass
    return get_volume()


status = {"battery": "...", "wifi": "...", "volume": "..."}


def refresh_status():
    while True:
        status["battery"] = get_battery()
        status["wifi"] = get_wifi()
        status["volume"] = get_volume()
        time.sleep(5)


threading.Thread(target=refresh_status, daemon=True).start()


# ---- the one top row: Apps button, the tabs (added below), messages, and the system status chips ----

topbar = QHBoxLayout()
topbar.setContentsMargins(0, 0, 0, 0)
topbar.setSpacing(6)
page.addLayout(topbar)

apps_button = button("Apps  ▾", lambda: open_apps_menu(), kind="accent", width=88, height=30)
topbar.addWidget(apps_button)
message = label("Ready", 12, MUTED)
message.setContentsMargins(10, 0, 6, 0)
message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)   # long messages are cut, not widening the window
topbar.addWidget(message, 1)


class Chip(QFrame):
    """A small rounded box at the right of the top row: an icon and a text."""

    def __init__(self, glyph):
        super().__init__()
        self.setObjectName("chip")
        self.setFixedHeight(28)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(5)
        self.icon = icon_label("clock", 14, MUTED)
        self.icon.setText(glyph)
        self.icon.setFixedWidth(18)
        self.text = label("", 12, FG)
        self.plain = ""             # the words without any colour markup (the clock chip keeps its text here)
        row.addWidget(self.icon)
        row.addWidget(self.text)
        self.on_wheel = None        # set to a function(steps) to make the chip react to the mouse wheel
        self.on_hover = None        # set to a function(inside) to hear the pointer enter (True) and leave (False)
        self._wheel_rest = 0

    def enterEvent(self, event):
        super().enterEvent(event)
        if self.on_hover:
            self.on_hover(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self.on_hover:
            self.on_hover(False)

    def wheelEvent(self, event):
        if self.on_wheel is None:
            super().wheelEvent(event)
            return
        self._wheel_rest += event.angleDelta().y()      # a notch is 120; a touchpad sends small amounts
        steps = int(self._wheel_rest / 120)
        if steps:
            self._wheel_rest -= steps * 120
            self.on_wheel(steps)
        event.accept()


class NameChip(QFrame):
    """The file-name chip in an app's toolbar. It gives way first when the window is narrow, and
    once it is too small to show a name it draws nothing (no leftover sliver)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("chip")
        self.setFixedHeight(28)
        self.setMinimumWidth(1)
        self.setProperty("tiny", False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        tiny = self.width() < 44
        if self.property("tiny") != tiny:
            self.setProperty("tiny", tiny)
            self.style().unpolish(self)
            self.style().polish(self)
            for text in self.findChildren(QLabel):      # see-through, not hidden: hiding would change the chip's wanted width
                if tiny:
                    effect = QGraphicsOpacityEffect(text)
                    effect.setOpacity(0.0)
                    text.setGraphicsEffect(effect)
                else:
                    text.setGraphicsEffect(None)


# on screen, left to right:  battery  wifi  volume  date : time
battery_chip, wifi_chip = Chip(ICONS["battery9"]), Chip(ICONS["wifi"])
volume_chip, clock_chip = Chip(ICONS["volume"]), Chip(ICONS["clock"])
for _chip in (battery_chip, wifi_chip, volume_chip, clock_chip):
    topbar.addWidget(_chip)
battery_icon, battery = battery_chip.icon, battery_chip.text
wifi, volume, clock = wifi_chip.text, volume_chip.text, clock_chip.text
volume_icon = volume_chip.icon


BATTERY_RED, BATTERY_AMBER, BATTERY_GREEN = "#ff5d6c", "#ffc857", "#5fd38d"


def battery_color(text):
    """The battery icon's colour for a reading ('55%', '90% charging'): green when full, through amber, to
    red when nearly empty. No reading (none, n/a, ...) is the quiet grey."""
    try:
        percent = max(0, min(100, int(text.split("%")[0])))
    except ValueError:
        return MUTED
    if percent <= 50:
        return mix(BATTERY_RED, BATTERY_AMBER, percent / 50)
    return mix(BATTERY_AMBER, BATTERY_GREEN, (percent - 50) / 50)


def battery_glyph(text):
    """The battery icon that matches the reading ('55%', '90% charging', 'none')."""
    if "charging" in text:
        return ICONS["charging"]
    try:
        percent = int(text.split("%")[0])
    except ValueError:
        return ICONS["battery9"]
    for limit, name in ((10, "battery0"), (40, "battery3"), (75, "battery6")):
        if percent < limit:
            return ICONS[name]
    return ICONS["battery9"]


def tick():
    clock_chip.plain = time.strftime(CLOCK_FORMAT)
    clock.setText(clock_markup(clock_chip.plain))
    if CLOCK_UI["card"] is not None and CLOCK_UI["card"].isVisible():
        update_clock_card()
    battery.setText(status["battery"].replace(" charging", ""))
    battery_icon.setText(battery_glyph(status["battery"]))
    set_color(battery_icon, battery_color(status["battery"]))
    wifi.setText(status["wifi"][:22])
    volume.setText(status["volume"])
    volume_icon.setText(ICONS["mute"] if status["volume"] == "muted" else ICONS["volume"])


# ---- the volume pop-up: slides in under the volume icon while the wheel turns, then fades away ----
VOLUME_UI = {"card": None, "icon": None, "text": None, "fill": None, "timer": None,
             "fade": None, "bar": None, "pulse": None, "pos": (0, 0)}
POP_W, POP_H, TRACK_W, HIDE_AFTER = 240, 44, 120, 1200       # size of the card, bar length, ms before it fades
ICON_SIZE, ICON_PEAK = 18, 32                                # the icon swells from 18 to 32 px and back on every notch


def floating(card):
    """Make a pop-up card a small window of its own above the main one. A card that is only a child
    widget would be hidden by a real window embedded in an app (the Edge app). It takes no focus and no mouse.
    It is placed with global screen positions and faded with setWindowOpacity."""
    card.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
                        | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput)
    card.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    card.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    card.setWindowOpacity(0.0)


def volume_card():
    """The pop-up, made the first time it is needed."""
    ui = VOLUME_UI
    if ui["card"] is not None:
        return ui["card"]
    card = QFrame(root)
    card.setObjectName("volumecard")
    card.resize(POP_W, POP_H)
    floating(card)
    icon = icon_label("volume", ICON_SIZE, FG, card)
    icon.setGeometry(6, 0, 40, POP_H)
    track = QFrame(card)
    track.setObjectName("volumetrack")
    track.setGeometry(52, (POP_H - 6) // 2, TRACK_W, 6)
    fill = QFrame(track)
    fill.setObjectName("volumefill")
    fill.setGeometry(0, 0, 0, 6)
    text = label("", 13, FG, "medium", parent=card)
    text.setGeometry(52 + TRACK_W + 8, 0, POP_W - (52 + TRACK_W + 8) - 8, POP_H)
    text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    timer = QTimer(card)
    timer.setSingleShot(True)
    timer.timeout.connect(hide_volume_card)
    ui.update(card=card, icon=icon, text=text, fill=fill, timer=timer)
    card.hide()
    return card


def fade_volume_card(to, ms, done=None):
    volume_card()
    fade_pop(VOLUME_UI, to, ms, done)


def fade_pop(ui, to, ms, done=None):
    """Fade a pop-up card (ui["card"], placed at the screen position ui["pos"]) to opacity `to`. It also slides:
    8 px lower when see-through, in place when solid."""
    card = ui["card"]
    if ui["fade"]:
        ui["fade"]["cancel"]()
    start = card.windowOpacity()

    def step(t):
        opacity = lerp(start, to, t)
        card.setWindowOpacity(opacity)
        card.move(ui["pos"][0], ui["pos"][1] + round(8 * (1 - opacity)))

    ui["fade"] = tween(step, ms, done)


def hide_volume_card():
    card = volume_card()
    fade_volume_card(0.0, 260, card.hide)


def show_volume_card(reading):
    """Show the pop-up with the volume reading ('40%', 'muted'): fade in, bar slides, icon zooms."""
    ui = VOLUME_UI
    card = volume_card()
    corner = volume_chip.mapToGlobal(QPoint(0, 0))
    ui["pos"] = (max(8, corner.x() + volume_chip.width() - POP_W), corner.y() + volume_chip.height() + 8)
    muted = reading == "muted" or not reading.endswith("%")
    percent = 0 if muted else int(reading[:-1])
    ui["text"].setText(reading)
    ui["icon"].setText(ICONS["mute"] if reading == "muted" else ICONS["volume"])
    ui["fill"].setStyleSheet(f"background: {MUTED}; border-radius: 3px;" if muted else "")
    if not card.isVisible():
        card.move(ui["pos"][0], ui["pos"][1] + 8)
        card.show()
    card.raise_()
    fade_volume_card(1.0, 140)
    # the bar slides from where it was to the new level
    if ui["bar"]:
        ui["bar"]["cancel"]()
    fill = ui["fill"]
    first, last = fill.width(), round(TRACK_W * percent / 100)
    ui["bar"] = tween(lambda t: fill.setGeometry(0, 0, round(lerp(first, last, t)), 6), 180)
    # the icon swells and settles back, flashing the accent colour at the top
    if ui["pulse"]:
        ui["pulse"]["cancel"]()
    icon = ui["icon"]

    def swell(t):
        s = math.sin(math.pi * t)
        icon.setFont(icon_font(round(ICON_SIZE + (ICON_PEAK - ICON_SIZE) * s)))
        set_color(icon, mix(FG, ACCENT, s))

    ui["pulse"] = tween(swell, 300, ease=lambda t: t)
    ui["timer"].start(HIDE_AFTER)


def scroll_volume(steps):
    status["volume"] = change_volume(steps)
    tick()
    say(f"Volume {status['volume']}")
    show_volume_card(status["volume"])


# ---- the clock: year:month:date:hour:min:sec:day; the pointer over it shows a month calendar and a world clock ----
CLOCK_FORMAT = "%Y:%m:%d:%H:%M:%S:%a"
CLOCK_DATE, CLOCK_TIME, CLOCK_DAY, CLOCK_COLON = FG, ACCENT_HOVER, "#ffc857", "#5d6372"   # date white, time blue, day amber, colons dim


def clock_markup(stamp):
    """The clock text with the parts in their own colours: year:month:date | hour:min:sec | day."""
    parts = stamp.split(":")
    colon = f"<span style='color:{CLOCK_COLON}'>:</span>"
    if len(parts) != 7:
        return stamp
    date, clock_time, day = parts[:3], parts[3:6], parts[6]
    return (colon.join(f"<span style='color:{CLOCK_DATE}'>{p}</span>" for p in date) + colon
            + colon.join(f"<span style='color:{CLOCK_TIME}'>{p}</span>" for p in clock_time) + colon
            + f"<span style='color:{CLOCK_DAY}'>{day}</span>")
WORLD_CLOCK = [("India", "Asia/Kolkata"), ("Dubai", "Asia/Dubai"), ("London", "Europe/London"),
               ("New York", "America/New_York"), ("Singapore", "Asia/Singapore"), ("Tokyo", "Asia/Tokyo"),
               ("Sydney", "Australia/Sydney"), ("UTC", "UTC")]          # (name shown, time zone); edit to taste
CLOCK_UI = {"card": None, "key": None, "title": None, "cells": [], "zones": [], "fade": None, "pos": (0, 0)}
CELL_W, CELL_H = 36, 26


def clock_card():
    """The calendar and world-clock card, made the first time it is needed."""
    ui = CLOCK_UI
    if ui["card"] is not None:
        return ui["card"]
    card = QFrame(root)
    card.setObjectName("popcard")
    floating(card)
    box = QVBoxLayout(card)
    box.setContentsMargins(14, 12, 14, 14)
    box.setSpacing(6)
    middle = Qt.AlignmentFlag.AlignCenter
    title = label("", 14, FG, "medium")
    title.setAlignment(middle)
    box.addWidget(title)
    grid = QGridLayout()
    grid.setSpacing(2)
    for column, name in enumerate(("Su", "Mo", "Tu", "We", "Th", "Fr", "Sa")):
        head = label(name, 11, MUTED)
        head.setFixedSize(CELL_W, 20)
        head.setAlignment(middle)
        grid.addWidget(head, 0, column)
    cells = []
    for i in range(42):                                   # six weeks always, so the card keeps its height
        cell = label("", 12, FG)
        cell.setFixedSize(CELL_W, CELL_H)
        cell.setAlignment(middle)
        grid.addWidget(cell, 1 + i // 7, i % 7)
        cells.append(cell)
    box.addLayout(grid)
    line = QFrame()
    line.setObjectName("popline")
    line.setFixedHeight(1)
    box.addWidget(line)
    box.addWidget(label("World clock", 11, MUTED))
    zones = QGridLayout()
    zones.setHorizontalSpacing(10)
    zones.setVerticalSpacing(3)
    zones.setColumnStretch(0, 1)
    rows = []
    for r, (name, _zone) in enumerate(WORLD_CLOCK):
        clock_text = label("", 12, FG, "medium")
        day_text, offset_text = label("", 11, MUTED), label("", 11, MUTED)
        day_text.setMinimumWidth(30)
        offset_text.setMinimumWidth(38)
        for widget in (clock_text, offset_text):
            widget.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        zones.addWidget(label(name, 12, FG), r, 0)
        zones.addWidget(clock_text, r, 1)
        zones.addWidget(day_text, r, 2)
        zones.addWidget(offset_text, r, 3)
        rows.append((clock_text, day_text, offset_text))
    box.addLayout(zones)
    card.setFixedWidth(7 * CELL_W + 6 * 2 + 28)
    card.adjustSize()
    ui.update(card=card, title=title, cells=cells, zones=rows)
    card.hide()
    return card


def update_clock_card():
    """Fill the calendar (when the day changes) and the world clock (every call)."""
    ui = CLOCK_UI
    clock_card()
    now = datetime.now()
    key = (now.year, now.month, now.day)
    if key != ui["key"]:
        ui["key"] = key
        ui["title"].setText(f"{calendar.month_name[now.month]} {now.year}")
        days = list(calendar.Calendar(firstweekday=6).itermonthdates(now.year, now.month))      # weeks start on Sunday
        for i, cell in enumerate(ui["cells"]):
            if i >= len(days):
                cell.setText("")
                cell.setStyleSheet("")
                continue
            day = days[i]
            cell.setText(str(day.day))
            if day == now.date():
                cell.setStyleSheet(f"background: {ACCENT}; color: {ON_ACCENT}; border-radius: 8px; font-weight: 600;")
            elif day.month == now.month:
                set_color(cell, FG)
            else:
                set_color(cell, mix(RAISED, MUTED, 0.45))       # the neighbouring months, dimmed
    here = datetime.now().astimezone()
    for (name, zone), (clock_text, day_text, offset_text) in zip(WORLD_CLOCK, ui["zones"]):
        try:
            there = datetime.now(ZoneInfo(zone))
        except Exception:                                       # no data for that zone
            clock_text.setText("n/a")
            day_text.setText("")
            offset_text.setText("")
            continue
        clock_text.setText(there.strftime("%H:%M"))
        day_text.setText(there.strftime("%a"))
        set_color(day_text, ACCENT if there.date() != here.date() else MUTED)       # another day over there
        minutes = round((there.utcoffset() - here.utcoffset()).total_seconds() / 60)
        sign = "+" if minutes > 0 else "−"
        offset_text.setText("" if minutes == 0 else f"{sign}{abs(minutes) // 60}:{abs(minutes) % 60:02d}")


def clock_hover(inside):
    ui = CLOCK_UI
    if inside:
        card = clock_card()
        update_clock_card()
        corner = clock_chip.mapToGlobal(QPoint(0, 0))
        ui["pos"] = (max(8, corner.x() + clock_chip.width() - card.width()), corner.y() + clock_chip.height() + 8)
        if not card.isVisible():
            card.move(ui["pos"][0], ui["pos"][1] + 8)
            card.show()
        card.raise_()
        fade_pop(ui, 1.0, 140)
    elif ui["card"] is not None:
        fade_pop(ui, 0.0, 200, ui["card"].hide)


clock_chip.on_hover = clock_hover
volume_chip.on_wheel = scroll_volume
volume_chip.setToolTip("Scroll up or down to change the volume")

clock_timer = QTimer(root)
clock_timer.timeout.connect(tick)
clock_timer.start(1000)
tick()


def say(text):
    message.setText(text)


# ---- main.json: what the window remembers (restored the next time it starts) ----
HERE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent     # the .exe's folder when packaged
MAIN_JSON = HERE / "main.json"
AUTOSAVE = {"on": False}          # only the real program saves by itself (tests switch it off)
autosave_timer = QTimer(root)
autosave_timer.setSingleShot(True)
autosave_timer.timeout.connect(lambda: save_state())


def save_soon():
    """Save a moment after the last change (many changes in a row make one save)."""
    if AUTOSAVE["on"]:
        autosave_timer.start(600)


LAST_SAVED = {"text": None}


def save_if_changed():
    """Every few seconds: save if anything differs from what is in the file. This catches what
    has no event of its own: the window being moved, the pages open in the Browser."""
    if AUTOSAVE["on"] and json.dumps(snapshot(), indent=2, ensure_ascii=False) != LAST_SAVED["text"]:
        save_state()


check_timer = QTimer(root)
check_timer.timeout.connect(save_if_changed)
check_timer.start(4000)


# ---- tab bar (under the top row) and the content below it ----

tabbar = QWidget()
tabbar.setFixedHeight(34)
tabbar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)   # many tabs never widen the window
tabs_layout = QHBoxLayout(tabbar)
tabs_layout.setContentsMargins(0, 0, 0, 0)
tabs_layout.setSpacing(4)
topbar.insertWidget(1, tabbar, 3)                  # right after the Apps button, in the same row
page.addSpacing(6)

content = QWidget()                                # the tabs' workspaces, one shown at a time
stack = QStackedLayout(content)
stack.setContentsMargins(0, 0, 0, 0)
page.addWidget(content, 1)

tabs = []                  # every tab, left to right
active = {"tab": None}     # the tab shown now
made = {"tabs": 0, "windows": 0}   # how many tabs / app windows were ever made (titles, window ids)
tab_motion = {"colors": None, "indicator": None}


class TitleEdit(QLineEdit):
    """A text box laid over a title. Enter or clicking away keeps the new name; Esc cancels."""

    def __init__(self, over, current, on_done):
        top = over.window()
        super().__init__(top)
        self.on_done, self.finished = on_done, False
        self.setFont(font(13))
        self.setStyleSheet(f"QLineEdit {{ background: {RAISED}; border: 1px solid {ACCENT}; "
                           f"border-radius: 8px; padding: 0 8px; color: {FG}; }}")
        self.setText(current)
        where = over.mapTo(top, QPoint(0, 0))
        self.setGeometry(where.x(), where.y(), max(over.width(), 130), max(over.height(), 24))
        self.show()
        self.raise_()
        self.setFocus()
        self.selectAll()

    def finish(self, keep):
        if self.finished:                      # Enter and then losing focus must not run twice
            return
        self.finished = True
        name = self.text().strip()[:40]
        self.hide()
        self.deleteLater()
        if keep and name:
            self.on_done(name)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish(True)
        elif event.key() == Qt.Key.Key_Escape:
            self.finish(False)
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.finish(True)


def edit_title(over, current, on_done):
    """Put a text box over a title. An empty name is ignored (the old name stays)."""
    return TitleEdit(over, current, on_done)


class ClickLabel(QLabel):
    """A title. Double-click renames it. If `on_drag` is set, pressing and moving the mouse
    calls it as on_drag("start" | "move" | "end", global_position): that is how a window is moved."""

    def __init__(self, text, size=13):
        super().__init__(text)
        self.setFont(title_font(size))
        self.setStyleSheet(f"color: {FG};")
        self.double_clicked = None
        self.on_drag = None
        self._press = None
        self._moving = False

    def mouseDoubleClickEvent(self, event):
        if self.double_clicked:
            self.double_clicked()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.on_drag:
            self._press, self._moving = event.globalPosition().toPoint(), False

    def mouseMoveEvent(self, event):
        if self._press is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        here = event.globalPosition().toPoint()
        if not self._moving and (here - self._press).manhattanLength() >= 6:     # a click is not a drag
            self._moving = True
            self.on_drag("start", self._press)
        if self._moving:
            self.on_drag("move", here)

    def mouseReleaseEvent(self, event):
        if self._moving:
            self.on_drag("end", event.globalPosition().toPoint())
        self._press, self._moving = None, False


class TabButton(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("tab")
        self.setFixedHeight(30)
        self.on_click = self.on_rename = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.on_click:
            self.on_click()

    def mouseDoubleClickEvent(self, event):
        if self.on_rename:
            self.on_rename()


def set_tab_title(tab, name):
    tab["title"] = name
    tab["label"].setText(name)
    QTimer.singleShot(0, lambda: move_indicator(animate=False))     # the tab got wider or narrower
    say("Renamed to " + name)
    save_soon()


def rename_tab(tab):
    edit_title(tab["label"], tab["title"], lambda name: set_tab_title(tab, name))


def paint_tab(tab):
    tab["button"].setStyleSheet(f"QFrame#tab {{ background: {tab['fill']}; border-radius: 11px; }}")
    set_color(tab["label"], tab["ink"])


def draw_tabs():
    """The shown tab has a soft fill and bright text; the others fade back to the background."""
    if tab_motion["colors"]:
        tab_motion["colors"]["cancel"]()
    plan = []
    for tab in tabs:
        shown = tab is active["tab"]
        plan.append((tab, tab["fill"], TAB_ACTIVE if shown else BG, tab["ink"], FG if shown else MUTED))

    def step(t):
        for tab, fill_a, fill_b, ink_a, ink_b in plan:
            tab["fill"], tab["ink"] = mix(fill_a, fill_b, t), mix(ink_a, ink_b, t)
            paint_tab(tab)

    tab_motion["colors"] = tween(step, ms=160)


indicator = QFrame(tabbar)
indicator.setStyleSheet(f"background: {ACCENT}; border-radius: 2px;")
indicator.setGeometry(0, 0, 20, 3)
indicator.hide()
indicator_at = {"rect": None}      # where the underline is now: (x, y, width, height)


def move_indicator(animate=True):
    """Glide the thin accent underline to sit under the shown tab."""
    tab = active["tab"]
    if tab is None or tab not in tabs:
        return
    tabs_layout.activate()
    b = tab["button"]
    target = (b.x() + 12, b.y() + b.height() - 3, max(10, b.width() - 24), 3)
    if tab_motion["indicator"]:
        tab_motion["indicator"]["cancel"]()
    start = indicator_at["rect"] or target

    def step(t):
        rect = tuple(round(lerp(p, q, t)) for p, q in zip(start, target))
        indicator.setGeometry(*rect)
        indicator_at["rect"] = rect

    indicator.show()
    indicator.raise_()
    tab_motion["indicator"] = tween(step, ms=240 if animate else 0, ease=ease_in_out)


def show_tab(tab, animate=True):
    """Show a tab's workspace. The underline under the tab names glides to the new tab."""
    old = active["tab"]
    if old is tab:
        draw_tabs()
        move_indicator(animate)
        return
    active["tab"] = tab
    stack.setCurrentWidget(tab["workspace"])
    draw_tabs()
    move_indicator(animate)
    save_soon()


def tab_can_close(tab):
    return all(inst["app"].get("can_close", lambda: True)() for inst in tab["instances"])


def close_tab(tab):
    """Close a tab and every app in it (apps may ask first, e.g. to save)."""
    if not tab_can_close(tab):
        return
    index = tabs.index(tab)
    if active["tab"] is tab:
        active["tab"] = None
    if tab["motion"]:
        tab["motion"]["cancel"]()
    for inst in tab["instances"]:
        stop_app(inst)
    stack.removeWidget(tab["workspace"])
    tab["workspace"].deleteLater()
    tab["button"].deleteLater()
    tabs.remove(tab)
    if not tabs:
        new_tab()                          # there is always one tab
    elif active["tab"] is None:
        show_tab(tabs[min(index, len(tabs) - 1)], animate=False)
    else:
        QTimer.singleShot(0, lambda: move_indicator(animate=False))   # the tabs after it moved left
    draw_tabs()
    say("Closed " + tab["title"])
    save_soon()


class Workspace(QWidget):
    """Where a tab's app windows are placed (by hand: see relayout)."""

    def __init__(self):
        super().__init__()
        self.on_resize = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.on_resize:
            self.on_resize(event.size().width(), event.size().height())


def make_empty_state(workspace):
    """What an empty tab shows: a hint about how to open an app."""
    box = QWidget(workspace)
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)
    column.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    column.addWidget(icon_label("apps", 46, BORDER))
    heading = label("Nothing open yet", 19, FG, family=TITLE_FAMILY)
    heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
    heading.setContentsMargins(0, 12, 0, 2)
    column.addWidget(heading)
    hint = label("Open an app from the Apps menu, top left", 13, MUTED)
    hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    column.addWidget(hint)
    box.adjustSize()
    return box


def show_empty_state(tab):
    box, workspace = tab["empty"], tab["workspace"]
    if tab["instances"]:
        box.hide()
    else:
        box.adjustSize()
        box.move((workspace.width() - box.width()) // 2, int(workspace.height() * 0.46 - box.height() / 2))
        box.show()


class Node(dict):
    """A tab or an app window. Two of them are the same only if they are the very same object
    (they point at each other, so comparing their contents would never end)."""
    __eq__ = lambda self, other: self is other
    __ne__ = lambda self, other: self is not other
    __hash__ = lambda self: id(self)


def new_tab():
    """Add an empty tab on the right and show it. Each tab has its own apps and pattern."""
    made["tabs"] += 1
    tab = Node(title=f"Tab {made['tabs']}", instances=[], counts={}, pattern="grid",
               motion=None, size=(0, 0), fill=BG, ink=MUTED)
    workspace = Workspace()
    tab["workspace"] = workspace
    tab["empty"] = make_empty_state(workspace)
    workspace.on_resize = lambda w, h: on_workspace_size(tab, w, h)
    stack.addWidget(workspace)
    button_ = TabButton()
    tab["button"] = button_
    row = QHBoxLayout(button_)
    row.setContentsMargins(14, 0, 6, 0)
    row.setSpacing(4)
    tab["label"] = label(tab["title"], 13, MUTED)
    row.addWidget(tab["label"])
    tab["close"] = icon_button("close", lambda: close_tab(tab), size=22, danger=True, glyph_size=10)
    row.addWidget(tab["close"])
    button_.on_click = lambda: show_tab(tab)
    button_.on_rename = lambda: rename_tab(tab)           # double-click renames
    tabs_layout.insertWidget(len(tabs), button_)
    button_.show()                                        # so the layout places it right now
    tabs.append(tab)
    paint_tab(tab)
    show_tab(tab)
    show_empty_state(tab)
    return tab


plus_button = icon_button("add", lambda: say("Opened " + new_tab()["title"]), size=28, strong=True,
                          glyph_size=15)
tabs_layout.addWidget(plus_button)
tabs_layout.addStretch(1)


# ---- apps: every apps/<name>.py with a build(parent, say) function ----

APPS_DIR = Path(__file__).with_name("apps")
LAYOUTS = {"grid": "Grid", "columns": "Side by side", "rows": "Stacked", "main": "Main + stack"}
MAX_SIZE = 6


def app_names():
    return sorted(p.stem for p in APPS_DIR.glob("*.py") if not p.name.startswith("_"))


def app_title(name):
    return name.replace("_", " ").title()


def cells(count, kind):
    """(row, column, rowspan, columnspan) for each of `count` windows in the given pattern."""
    if kind == "columns":
        return [(0, i, 1, 1) for i in range(count)]
    if kind == "rows":
        return [(i, 0, 1, 1) for i in range(count)]
    if kind == "main":        # the first window big on the left, the rest stacked on the right
        if count < 2:
            return [(0, 0, 1, 1)] * count
        return [(0, 0, count - 1, 1)] + [(i, 1, 1, 1) for i in range(count - 1)]
    if count == 0:            # grid: as square as possible; the last window fills its row
        return []
    cols = math.ceil(math.sqrt(count))
    out = [(i // cols, i % cols, 1, 1) for i in range(count)]
    row, col, _, _ = out[-1]
    out[-1] = (row, col, 1, cols - col)
    return out


def edges(total, weights, count):
    """Start and end pixel of each of `count` rows/columns that share `total` pixels in
    proportion to their weights, with GAP pixels between them."""
    free = max(0, total - GAP * (count - 1))
    whole = sum(weights.get(i, 1) for i in range(count)) or 1
    starts, ends, used = [], [], 0
    for i in range(count):
        starts.append(round(free * used / whole) + GAP * i)
        used += weights.get(i, 1)
        ends.append(round(free * used / whole) + GAP * i)
    return starts, ends


def rects(tab, width, height):
    """Where each app window of a tab goes: (x, y, w, h) in pixels, in the same order."""
    placed = cells(len(tab["instances"]), tab["pattern"])
    row_weight, col_weight = {}, {}
    for inst, (r, c, rowspan, colspan) in zip(tab["instances"], placed):
        if rowspan == 1:      # a window spanning several rows/columns only sets the other direction
            row_weight[r] = max(row_weight.get(r, 1), inst["size"])
        if colspan == 1:
            col_weight[c] = max(col_weight.get(c, 1), inst["size"])
    rows = max((r + rs for r, _, rs, _ in placed), default=0)
    cols = max((c + cs for _, c, _, cs in placed), default=0)
    row_start, row_end = edges(height, row_weight, rows)
    col_start, col_end = edges(width, col_weight, cols)
    out = []
    for r, c, rowspan, colspan in placed:
        x, y = col_start[c], row_start[r]
        out.append((x, y, max(1, col_end[c + colspan - 1] - x), max(1, row_end[r + rowspan - 1] - y)))
    return out


def put(inst, rect):
    """Put an app window at a rectangle."""
    x, y, w, h = (round(v) for v in rect)
    fit_toolbar(inst, w)
    inst["pane"].setGeometry(x, y, max(1, w), max(1, h))
    inst["rect"] = (x, y, max(1, w), max(1, h))


def shrunk(rect, scale):
    x, y, w, h = rect
    return (x + w * (1 - scale) / 2, y + h * (1 - scale) / 2, w * scale, h * scale)


def relayout(tab, animate=True):
    """Move every app window of a tab to its place, gliding there when animate is on."""
    workspace = tab["workspace"]
    width, height = workspace.width(), workspace.height()
    if width < 40 or height < 40:             # not shown yet; the resize event comes later
        return
    if tab["motion"]:
        tab["motion"]["cancel"]()             # start from wherever the windows are right now
    plan = []
    for inst, target in zip(tab["instances"], rects(tab, width, height)):
        if inst["rect"] is None:              # a new window grows in from a bit smaller
            put(inst, shrunk(target, 0.9))
            inst["pane"].show()
        plan.append((inst, inst["rect"], target))

    def step(t):
        for inst, a, b in plan:
            put(inst, tuple(lerp(p, q, t) for p, q in zip(a, b)))

    tab["motion"] = tween(step, ms=240 if animate else 0, ease=ease_out)


DRAG = {"inst": None, "origin": None, "grab": None, "target": None}      # the window being moved by hand


def drop_mark(tab):
    """The glowing frame that shows which window the dragged one will swap places with."""
    if tab.get("mark") is None:
        tab["mark"] = QFrame(tab["workspace"])
        tab["mark"].setObjectName("dropmark")
        tab["mark"].hide()
    return tab["mark"]


def swap_windows(first, second):
    """Two windows of the same tab trade places (the pattern gives each place its shape)."""
    tab = first["tab"]
    if first is second or second["tab"] is not tab:
        return False
    items = tab["instances"]
    i, j = items.index(first), items.index(second)
    items[i], items[j] = second, first
    relayout(tab)
    say(f"{first['title']} and {second['title']} swapped places")
    save_soon()
    return True


def move_window(inst, position):
    """Put a window in place number `position` (1 = first, in the order list_windows shows);
    the window that was there takes its old place. Returns the window it swapped with."""
    items = inst["tab"]["instances"]
    if not 1 <= position <= len(items):
        raise ValueError(f"There is no place {position}: this tab has places 1 to {len(items)}.")
    other = items[position - 1]
    if other is inst:
        return None
    swap_windows(inst, other)
    return other


def drag_window(inst, phase, global_pos):
    """Move a window by its title: it follows the pointer; the window under it lights up; letting
    go swaps them. Letting go over nothing sends it back to its own place."""
    tab, workspace = inst["tab"], inst["tab"]["workspace"]
    here = workspace.mapFromGlobal(global_pos)
    if phase == "start":
        if tab["motion"]:
            tab["motion"]["cancel"]()
        DRAG.update(inst=inst, origin=inst["rect"], grab=here, target=None)
        inst["label"].setCursor(Qt.CursorShape.ClosedHandCursor)
        return
    if DRAG["inst"] is not inst or DRAG["origin"] is None:
        return
    mark = drop_mark(tab)
    if phase == "move":
        ox, oy, w, h = DRAG["origin"]
        x = min(max(0, ox + here.x() - DRAG["grab"].x()), max(0, workspace.width() - w))
        y = min(max(0, oy + here.y() - DRAG["grab"].y()), max(0, workspace.height() - h))
        put(inst, (x, y, w, h))
        hit = None
        for other, (rx, ry, rw, rh) in zip(tab["instances"], rects(tab, workspace.width(), workspace.height())):
            if other is not inst and rx <= here.x() < rx + rw and ry <= here.y() < ry + rh:
                hit = other
                mark.setGeometry(rx, ry, rw, rh)
        DRAG["target"] = hit
        mark.setVisible(hit is not None)
        mark.raise_()
        inst["pane"].raise_()
        return
    target = DRAG["target"]                      # phase == "end"
    mark.hide()
    inst["label"].setCursor(Qt.CursorShape.OpenHandCursor)
    DRAG.update(inst=None, origin=None, grab=None, target=None)
    if target is None or not swap_windows(inst, target):
        relayout(tab)


def on_workspace_size(tab, width, height):
    """The window was resized: put everything in its new place at once (no gliding)."""
    if (width, height) != tab["size"]:
        tab["size"] = (width, height)
        relayout(tab, animate=False)
        show_empty_state(tab)


def set_pattern(tab, kind):
    tab["pattern"] = kind
    relayout(tab)
    say("Pattern: " + LAYOUTS[kind])
    save_soon()


def resize(inst, step):
    inst["size"] = min(MAX_SIZE, max(1, inst["size"] + step))
    relayout(inst["tab"])
    say(f"{inst['title']}: space {inst['size']}")
    save_soon()


def set_window_title(inst, name):
    inst["title"] = name
    inst["label"].setText(name)
    say("Renamed to " + name)
    save_soon()


def rename_app(inst):
    edit_title(inst["label"], inst["title"], lambda name: set_window_title(inst, name))


def stop_app(inst):
    """Let an app end what it started (a terminal's PowerShell)."""
    shutdown = inst["app"].get("shutdown")
    if shutdown:
        shutdown()


def close_app(inst):
    """Close one app window (the app may ask first, e.g. to save). It shrinks away while the
    others glide into the space it leaves."""
    if not inst["app"].get("can_close", lambda: True)():
        return
    tab = inst["tab"]
    tab["instances"].remove(inst)
    stop_app(inst)
    pane = inst["pane"]
    start = inst["rect"] or (0, 0, 1, 1)
    show_empty_state(tab)
    relayout(tab)

    def step(t):
        pane.setGeometry(*(round(v) for v in shrunk(start, 1 - 0.1 * t)))

    def gone():
        pane.hide()
        pane.deleteLater()

    tween(step, ms=210, done=gone, ease=ease_in_out)
    say(f"Closed {inst['title']}")
    save_soon()


def open_pattern_menu(tab, anchor):
    popup_menu(anchor, [(text, lambda k=kind: set_pattern(tab, k), "layout", tab["pattern"] == kind)
                        for kind, text in LAYOUTS.items()], width=190, align="right")


def build_pane(inst):
    """A card for one app: its title and small buttons along the top, the app below."""
    tab = inst["tab"]
    pane = QFrame(tab["workspace"])
    pane.setObjectName("pane")
    column = QVBoxLayout(pane)
    column.setContentsMargins(8, 4, 8, 6)
    column.setSpacing(4)
    header = QHBoxLayout()
    header.setContentsMargins(8, 0, 0, 0)
    header.setSpacing(0)
    title = ClickLabel(inst["title"])
    title.setFixedHeight(28)
    title.double_clicked = lambda: rename_app(inst)                     # double-click renames
    title.on_drag = lambda phase, pos: drag_window(inst, phase, pos)    # drag moves
    title.setCursor(Qt.CursorShape.OpenHandCursor)
    title.setToolTip("Drag to move this window onto another one (they swap places). Double-click to rename.")
    inst["label"] = title
    header.addWidget(title, 1)
    layout_button = icon_button("layout", lambda: open_pattern_menu(tab, layout_button), size=24)
    header.addWidget(layout_button)
    header.addSpacing(4)
    header.addWidget(icon_button("minus", lambda: resize(inst, -1), size=24))
    header.addWidget(icon_button("add", lambda: resize(inst, 1), size=24))
    header.addWidget(icon_button("close", lambda: close_app(inst), size=24, danger=True, glyph_size=12))
    column.addLayout(header)
    body = QWidget()
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(0, 0, 0, 0)
    column.addWidget(body, 1)
    inst["body"], inst["header"] = body, header
    return pane, body


WINDOW_BUTTONS = 4 * 24 + 4 + 16 + 16       # the four small buttons, their gap, and the header's margins


def set_toolbar_inline(inst, inline):
    """Put an app's toolbar in its window's title row (wide windows: no row of its own, so the
    app gets more room) or on a row below it (narrow windows)."""
    bar = inst.get("toolbar")
    if bar is None or inst.get("inline") == inline:
        return
    inst["inline"] = inline
    header, frame_layout = inst["header"], inst["app"]["frame"].layout()
    if inline:
        frame_layout.removeWidget(bar)
        header.insertWidget(1, bar, 1)
        header.setStretchFactor(inst["label"], 0)
        bar.layout().setContentsMargins(14, 0, 8, 0)
    else:
        header.removeWidget(bar)
        frame_layout.insertWidget(0, bar)
        header.setStretchFactor(inst["label"], 1)
        bar.layout().setContentsMargins(0, 0, 0, 0)
    bar.show()


def fit_toolbar(inst, width):
    """Title row or own row, whichever the window's width allows."""
    bar = inst.get("toolbar")
    if bar is not None:
        needed = inst["label"].sizeHint().width() + bar.minimumSizeHint().width() + 22 + WINDOW_BUTTONS
        set_toolbar_inline(inst, width >= needed)


def open_app(name):
    """Open a new window of an app in the shown tab. Opening the same app again opens another.
    Returns the window (a dict), or None if the app could not be opened."""
    tab = active["tab"]
    try:
        module = importlib.import_module("apps." + name)
    except Exception as err:
        say(f"Could not open {app_title(name)}: {err}")
        return None
    tab["counts"][name] = tab["counts"].get(name, 0) + 1
    made["windows"] += 1
    inst = Node(name=name, tab=tab, size=1, rect=None, id=made["windows"],
                title=f"{app_title(name)} {tab['counts'][name]}")
    inst["pane"], body = build_pane(inst)
    try:
        inst["app"] = module.build(body, say)
    except Exception as err:
        inst["pane"].deleteLater()
        say(f"Could not open {app_title(name)}: {err}")
        return None
    body.layout().addWidget(inst["app"]["frame"])
    inst["toolbar"], inst["inline"] = inst["app"].get("toolbar"), False
    tab["instances"].append(inst)
    show_empty_state(tab)
    relayout(tab)
    inst["pane"].show()
    say("Opened " + inst["title"])
    save_soon()
    return inst


def open_apps_menu():
    """The Apps menu: one row per file in the apps folder (read again each time it opens)."""
    items = [(app_title(name), lambda n=name: open_app(n), APP_ICONS.get(name, "apps"), False)
             for name in app_names()]
    if items:
        popup_grid(apps_button, items, columns=3)
    else:
        popup_menu(apps_button, [("No apps found", lambda: None, None, False)], width=220)


# ---- what the chatbot's tools use: find windows and tabs by id or name, describe them ----

def describe_window(inst):
    return {"id": inst["id"], "title": inst["title"], "app": inst["name"], "tab": inst["tab"]["title"],
            "size": inst["size"], "place": inst["tab"]["instances"].index(inst) + 1}


def describe_windows():
    """Everything that is open: the tabs, each tab's pattern and its windows (with their ids)."""
    return {"shown_tab": active["tab"]["title"] if active["tab"] else None,
            "tabs": [{"title": tab["title"], "pattern": tab["pattern"],
                      "windows": [describe_window(inst) for inst in tab["instances"]]}
                     for tab in tabs]}


def find_tab(ref=None):
    """A tab from its name, its number (1 = leftmost) or nothing/"current" for the shown one.
    Raises ValueError with a message that says what is available."""
    names = ", ".join(f"{n}: {t['title']}" for n, t in enumerate(tabs, 1))
    text = "" if ref is None else str(ref).strip()
    if text.lower() in ("", "current", "shown", "active"):
        return active["tab"]
    if text.isdigit():
        if 1 <= int(text) <= len(tabs):
            return tabs[int(text) - 1]
        raise ValueError(f"There is no tab number {text}. Tabs: {names}")
    matches = [t for t in tabs if t["title"].lower() == text.lower()]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"There is no tab called '{text}'. Tabs: {names}" if not matches
                     else f"Several tabs are called '{text}'; use the tab number. Tabs: {names}")


def find_window(ref=None, app=None):
    """A window from its id (7), or its title ('Notepad 1'). With no ref, the only window of
    that app (in the shown tab first). `app` restricts the kind (e.g. 'notepad').
    Raises ValueError with a message that says what is available."""
    everything = [inst for tab in tabs for inst in tab["instances"]]
    listing = ", ".join(f"{i['id']}: {i['title']} (in {i['tab']['title']})" for i in everything) or "none"
    text = "" if ref is None else str(ref).strip()
    pool = [i for i in everything if app is None or i["name"] == app]
    if text == "":
        here = [i for i in pool if i["tab"] is active["tab"]]
        if len(here) == 1:
            return here[0]
        if not here and len(pool) == 1:
            return pool[0]
        kind = app_title(app) if app else "window"
        if not pool:
            raise ValueError(f"No {kind} is open. Open one with open_app. Open windows: {listing}")
        raise ValueError(f"Several {kind} windows are open; say which one (window id). Open windows: {listing}")
    if text.isdigit():
        found = [i for i in everything if i["id"] == int(text)]
        if not found:
            raise ValueError(f"There is no window with id {text}. Open windows: {listing}")
        inst = found[0]
    else:
        found = [i for i in everything if i["title"].lower() == text.lower()]
        shown = [i for i in found if i["tab"] is active["tab"]]
        found = shown if len(shown) == 1 else found
        if not found:
            raise ValueError(f"There is no window called '{text}'. Open windows: {listing}")
        if len(found) > 1:
            raise ValueError(f"Several windows are called '{text}'; use the window id. Open windows: {listing}")
        inst = found[0]
    if app is not None and inst["name"] != app:
        raise ValueError(f"Window {inst['id']} ({inst['title']}) is a {app_title(inst['name'])}, not a {app_title(app)}.")
    return inst


tools_api = {            # handed to chat/tools.py and to each app's tools(api)
    "root": root, "tabs": tabs, "active": active, "status": status, "LAYOUTS": LAYOUTS,
    "MAX_SIZE": MAX_SIZE, "say": say, "app_names": app_names, "app_title": app_title,
    "describe_windows": describe_windows, "describe_window": describe_window,
    "find_window": find_window, "find_tab": find_tab, "open_app": open_app, "close_app": close_app,
    "set_pattern": set_pattern, "resize": resize, "move_window": move_window, "set_window_title": set_window_title,
    "set_tab_title": set_tab_title, "new_tab": new_tab, "show_tab": show_tab,
    "close_tab": close_tab, "tab_can_close": tab_can_close, "get_wifi": get_wifi,
    "get_battery": get_battery, "get_volume": get_volume,
}


def snapshot():
    """What main.json keeps: the window's place and size, and for each tab its name, pattern
    and windows (which app, its title, its share of the space). Not what is inside the apps."""
    g = root.normalGeometry() if root.isMaximized() else root.geometry()
    return {"window": {"x": g.x(), "y": g.y(), "width": g.width(), "height": g.height(),
                       "maximized": root.isMaximized()},
            "active_tab": tabs.index(active["tab"]) if active["tab"] in tabs else 0,
            "tabs": [{"title": tab["title"], "pattern": tab["pattern"],
                      "windows": [window_entry(inst) for inst in tab["instances"]]} for tab in tabs]}


def window_entry(inst):
    """One window for main.json. An app that has get_state() (the Browser: its profile and
    pages) adds what it wants kept; restoring hands that back to its set_state()."""
    entry = {"app": inst["name"], "title": inst["title"], "size": inst["size"]}
    getter = inst["app"].get("get_state")
    if getter:
        try:
            kept = getter()
            json.dumps(kept)                   # only if it can really be written
            entry["state"] = kept
        except Exception:
            pass
    return entry


def save_state(path=None):
    """Write main.json (a half-written file is never left behind). True if it worked."""
    path = Path(path or MAIN_JSON)
    temp = path.with_name(path.name + ".tmp")
    try:
        text = json.dumps(snapshot(), indent=2, ensure_ascii=False)
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
        LAST_SAVED["text"] = text
        return True
    except OSError as err:
        say("Could not save main.json: " + str(err))
        return False


def restore_state(path=None):
    """Bring back what main.json kept. False (and nothing changed) if there is no usable file."""
    try:
        data = json.loads(Path(path or MAIN_JSON).read_text(encoding="utf-8"))
        entries = [e for e in data["tabs"] if isinstance(e, dict)][:20]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if not entries:
        return False
    motion = MOTION["on"]
    MOTION["on"] = False                       # everything is simply there, nothing glides in
    try:
        place = data.get("window") if isinstance(data.get("window"), dict) else {}
        try:
            rect = QRect(int(place["x"]), int(place["y"]), max(760, int(place["width"])),
                         max(500, int(place["height"])))
            if any(s.availableGeometry().intersects(rect) for s in QGuiApplication.screens()):
                root.setGeometry(rect)         # (only if it is on a screen that still exists)
            else:
                root.resize(rect.width(), rect.height())
            if place.get("maximized"):
                root.setWindowState(Qt.WindowState.WindowMaximized)
        except (KeyError, TypeError, ValueError):
            pass
        for entry in entries:
            tab = new_tab()
            title = entry.get("title")
            if isinstance(title, str) and title.strip():
                tab["title"] = title.strip()[:40]
                tab["label"].setText(tab["title"])
            if entry.get("pattern") in LAYOUTS:
                tab["pattern"] = entry["pattern"]
            for win in [w for w in entry.get("windows", []) if isinstance(w, dict)][:12]:
                if win.get("app") not in app_names():
                    continue                   # an app that is gone is skipped
                inst = open_app(win["app"])
                if inst is None:
                    continue
                if isinstance(win.get("title"), str) and win["title"].strip():
                    inst["title"] = win["title"].strip()[:40]
                    inst["label"].setText(inst["title"])
                if isinstance(win.get("size"), int):
                    inst["size"] = min(MAX_SIZE, max(1, win["size"]))
                setter = inst["app"].get("set_state")
                if setter and isinstance(win.get("state"), dict):
                    try:
                        setter(win["state"])
                    except Exception as err:
                        say(f"Could not restore {inst['title']}: {err}")
            relayout(tab, animate=False)
        index = data.get("active_tab")
        show_tab(tabs[index if isinstance(index, int) and 0 <= index < len(tabs) else 0], animate=False)
    finally:
        MOTION["on"] = motion
    return True


def on_close():
    """True when the window may close (no app said no)."""
    if not all(tab_can_close(tab) for tab in tabs):
        return False
    if AUTOSAVE["on"]:
        autosave_timer.stop()
        save_state()                           # while the windows are still there
    chatbot["shutdown"]()                      # stop a reply that is still being written
    for tab in tabs:
        for inst in tab["instances"]:
            stop_app(inst)                     # ends terminals' PowerShell
    return True


if __name__ == "__main__" and restore_state():
    say("Welcome back")
else:
    new_tab()
root.show()
dark_titlebar(root)
chatbot = chat_ui.build(root, say, tools=lambda: chat_tools.collect(tools_api))   # the floating chat button
AUTOSAVE["on"] = __name__ == "__main__"        # from now on every change is saved
save_soon()

if __name__ == "__main__":
    sys.exit(qt_app.exec())

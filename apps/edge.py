"""Edge: the real Microsoft Edge, shown inside a window of Eden Root.

    build(parent, say) -> dict

This is separate from the Browser app. The Browser is Chromium through Qt, which has no H.264 or AAC, so
many videos and live streams will not play there. Edge uses Windows' own codecs, so they do (checked:
H.264, AAC, VP9 and MSE are all reported as supported). The price is that this is a whole Edge window
placed inside the pane, not a web page widget:

  * It has Edge's own tabs, address bar, menus, extensions and sign-in. Eden Root only gives it a place.
  * It runs with its own profile folder (logins, history, extensions, all apart from your everyday Edge
    and from the Browser app): %APPDATA%/Eden/Eden Root/edge/<profile>. Treat that folder as private. Two Edge
    windows in Eden Root never share one profile (the second one gets "<name>-2").
  * Edge's pop-ups (its menus, the translate bubble) are windows of their own, and a real window inside
    a pane is drawn above Eden Root's own small things that overlap it: a window you drag across it
    passes behind it, and the drop highlight is hidden behind it.
  * Closing the pane closes its Edge. If you close Edge itself, the pane says so and offers Start Edge.

The returned dict holds: frame, state, start, stop, restart, mode, pid, hwnd, profile, open_url,
get_state, set_state, shutdown, can_close.
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QStandardPaths, QTimer, Qt
from PyQt6.QtGui import QWindow
from PyQt6.QtWidgets import QVBoxLayout, QWidget

import theme
from chat.tools import text_arg, tool
from theme import MUTED

EDGE_PATHS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
START_TIMEOUT = 40          # seconds to wait for Edge's window to appear
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ALLOWED_SCHEMES = ("http", "https", "file", "edge", "about")
IN_USE = set()              # profiles that a running Edge pane has (two panes never share one)
CLAIMED = set()             # Edge windows already placed in a pane

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
HANDLE = ctypes.c_void_p
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [HANDLE, ctypes.c_int]
user32.SetWindowLongW.argtypes = [HANDLE, ctypes.c_int, ctypes.c_long]
user32.IsWindow.argtypes = [HANDLE]
user32.IsWindowVisible.argtypes = [HANDLE]
user32.GetWindow.restype = HANDLE
user32.GetWindow.argtypes = [HANDLE, wt.UINT]
user32.GetClassNameW.argtypes = [HANDLE, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [HANDLE, ctypes.POINTER(wt.RECT)]
user32.GetWindowThreadProcessId.argtypes = [HANDLE, ctypes.POINTER(wt.DWORD)]
user32.PostMessageW.argtypes = [HANDLE, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel32.CreateToolhelp32Snapshot.restype = HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.CloseHandle.argtypes = [HANDLE]
GWL_STYLE, WS_CAPTION, WS_THICKFRAME, WM_CLOSE, GW_OWNER = -16, 0x00C00000, 0x00040000, 0x0010, 4
ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, HANDLE, wt.LPARAM)


class ProcessEntry(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD), ("heap", ctypes.c_size_t),
                ("module", wt.DWORD), ("threads", wt.DWORD), ("parent", wt.DWORD), ("priority", ctypes.c_long),
                ("flags", wt.DWORD), ("exe", ctypes.c_wchar * 260)]


# ---- finding things ----

def find_edge():
    """Path of msedge.exe, or None."""
    for path in EDGE_PATHS:
        if os.path.exists(path):
            return path
    return None


def clean_name(name):
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", str(name or "").strip()).strip(" .")
    return name[:40] or "default"


def profile_root():
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / "edge"


def profile_dir(name):
    return profile_root() / clean_name(name)


def claim_profile(name):
    """The profile name to use: `name`, or '<name>-2', '<name>-3'... when another Edge pane has it."""
    base = clean_name(name)
    chosen, n = base, 1
    while chosen in IN_USE:
        n += 1
        chosen = f"{base}-{n}"
    IN_USE.add(chosen)
    return chosen


def process_tree(root_pid):
    """The process id and the ids of everything it started (Edge runs as many processes)."""
    snapshot = kernel32.CreateToolhelp32Snapshot(2, 0)          # TH32CS_SNAPPROCESS
    children = {}
    entry = ProcessEntry()
    entry.size = ctypes.sizeof(ProcessEntry)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            children.setdefault(entry.parent, []).append(entry.pid)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    found, todo = {root_pid}, [root_pid]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in found:
                found.add(child)
                todo.append(child)
    return found


def edge_windows(pids):
    """Edge's own big top-level windows among those processes (pop-up bubbles have an owner, so they are left out)."""
    found = []

    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or hwnd in CLAIMED or user32.GetWindow(hwnd, GW_OWNER):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        name = ctypes.create_unicode_buffer(60)
        user32.GetClassNameW(hwnd, name, 60)
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if name.value == "Chrome_WidgetWin_1" and width > 300 and height > 200:
            found.append((width * height, hwnd))
        return True

    user32.EnumWindows(ENUMPROC(each), 0)
    return [hwnd for _, hwnd in sorted(found, reverse=True)]


def address(text):
    """What to open for whatever was typed: an address with a scheme, or a search."""
    text = str(text or "").strip()
    if not text:
        return ""
    match = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", text)
    if match and not re.match(r"^[A-Za-z]:[\\/]", text) and not re.match(r"^(localhost|\d{1,3}(\.\d{1,3}){3}):\d+", text):
        if match.group(1).lower() not in ALLOWED_SCHEMES:
            raise ValueError(f"'{match.group(1)}:' addresses are not opened here (use http, https, file, edge or about)")
        return text
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return Path(text).as_uri()
    local = re.match(r"^(localhost|\d{1,3}(\.\d{1,3}){3})(:\d+)?([/?#].*)?$", text)
    if " " not in text and not text.startswith("-") and (local or "." in text.split("/")[0]):
        private = local and (text.startswith("localhost") or re.match(r"^(127|10|192\.168)\.", text))
        return ("http://" if private else "https://") + text
    from urllib.parse import quote_plus
    return "https://www.bing.com/search?q=" + quote_plus(text)


def close_edge(proc, hwnd):
    """Ask Edge to close (so it saves its tabs), and make sure it is gone a few seconds later."""
    if hwnd:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def reap():
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            pass
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)

    threading.Thread(target=reap, daemon=False).start()


# ---- the app ----

def build(parent, say=None):
    say = say or (lambda text: None)
    state = {"mode": "idle", "profile": None, "proc": None, "hwnd": None, "since": 0.0, "closed": False,
             "container": None, "window": None, "message": "", "exe": find_edge(), "wanted_url": None}

    frame = QWidget(parent)
    column = QVBoxLayout(frame)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)

    # what shows while Edge is not in the pane: starting, closed, or not installed
    note = QWidget()
    note_column = QVBoxLayout(note)
    note_column.setAlignment(Qt.AlignmentFlag.AlignCenter)
    note_column.setSpacing(10)
    headline = theme.label("", 15, theme.FG, "medium")
    headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
    detail = theme.label("", 12, MUTED)
    detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
    detail.setWordWrap(True)
    start_button = theme.button("Start Edge", lambda: start(), kind="accent", width=120, height=30)
    note_column.addWidget(headline)
    note_column.addWidget(detail)
    note_column.addWidget(start_button, 0, Qt.AlignmentFlag.AlignCenter)
    column.addWidget(note, 1)

    def show_note(title, text, button):
        headline.setText(title)
        detail.setText(text)
        start_button.setVisible(button)
        note.show()
        state["message"] = title

    def profile():
        return state["profile"] or clean_name("default")

    def release():
        IN_USE.discard(state["profile"])

    def remove_container():
        container, state["container"], state["window"] = state["container"], None, None
        if state["hwnd"]:
            CLAIMED.discard(state["hwnd"])
        state["hwnd"] = None
        if container is not None:
            container.hide()
            container.setParent(None)
            container.deleteLater()

    def start(url=None):
        """Start Edge (when it is not already running) and place its window here."""
        if state["closed"] or state["mode"] in ("starting", "running"):
            return False
        if os.environ.get("EDGE_NO_START") == "1":                      # the test suites must never open a real Edge
            state["mode"] = "stopped"
            show_note("Edge is switched off", "EDGE_NO_START=1 is set in the environment.", True)
            return False
        if not state["exe"]:
            state["mode"] = "missing"
            show_note("Microsoft Edge was not found", "Edge is not installed in the usual place on this PC.", False)
            return False
        release()
        state["profile"] = claim_profile(profile())
        folder = profile_dir(state["profile"])
        folder.mkdir(parents=True, exist_ok=True)
        args = [state["exe"], f"--user-data-dir={folder}", "--no-first-run", "--no-default-browser-check", "--new-window"]
        if url:
            args.append(url)
        try:
            state["proc"] = subprocess.Popen(args)
        except OSError as err:
            state["mode"] = "stopped"
            show_note("Could not start Edge", str(err), True)
            return False
        state["mode"], state["since"] = "starting", time.time()
        show_note("Starting Microsoft Edge…", f"Profile: {state['profile']}", False)
        say("Starting Edge…")
        return True

    def stop():
        """Close Edge (the pane stays, and Start Edge brings it back)."""
        proc, hwnd = state["proc"], state["hwnd"]
        if proc is not None and proc.poll() is None:
            close_edge(proc, hwnd)
        remove_container()
        state["proc"] = None
        release()
        if not state["closed"]:
            state["mode"] = "stopped"
            show_note("Edge is closed", "Start it again to carry on with the same profile.", True)

    def restart():
        stop()
        return start()

    def embed(hwnd):
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~WS_CAPTION & ~WS_THICKFRAME)      # no title bar of its own
        window = QWindow.fromWinId(hwnd)
        container = QWidget.createWindowContainer(window, frame)
        container.setMinimumSize(1, 1)
        column.addWidget(container, 1)
        note.hide()
        state["window"], state["container"], state["hwnd"] = window, container, hwnd
        CLAIMED.add(hwnd)
        state["mode"] = "running"
        say("Edge is ready")

    def poll():
        if state["closed"]:
            return
        mode, proc = state["mode"], state["proc"]
        if mode == "starting":
            pids = process_tree(proc.pid)
            windows = edge_windows(pids)
            if windows:
                embed(windows[0])
            elif time.time() - state["since"] > START_TIMEOUT:
                stop()
                show_note("Edge did not open a window", "It took too long. Try Start Edge again.", True)
            elif proc.poll() is not None and time.time() - state["since"] > 3:
                code = proc.returncode
                state["proc"] = None
                state["mode"] = "stopped"
                release()
                show_note("Edge stopped right away", f"(exit code {code}). Another Edge may already be using the profile '{profile()}'.", True)
        elif mode == "running":
            if proc.poll() is not None or not user32.IsWindow(state["hwnd"]):
                remove_container()
                if proc.poll() is None:                                  # the window is gone but Edge lingers in the background
                    close_edge(proc, None)
                state["proc"] = None
                release()
                state["mode"] = "stopped"
                show_note("Edge was closed", "Start it again to carry on with the same profile.", True)
                say("Edge was closed")

    timer = QTimer(frame)
    timer.timeout.connect(poll)
    timer.start(250)

    def shutdown():
        if state["closed"]:
            return
        state["closed"] = True
        try:
            timer.stop()
        except RuntimeError:
            pass
        proc = state["proc"]
        if proc is not None and proc.poll() is None:
            close_edge(proc, state["hwnd"])
        CLAIMED.discard(state["hwnd"])
        release()

    frame.destroyed.connect(lambda *args: shutdown())

    def open_url(text):
        """Open an address in Edge: a new tab of the running window (starts Edge first when it is not running)."""
        target = address(text)
        if not target:
            raise ValueError("'url' is empty")
        if state["mode"] in ("idle", "stopped"):
            start(target)
        elif state["mode"] == "running":
            subprocess.Popen([state["exe"], f"--user-data-dir={profile_dir(state['profile'])}", target], creationflags=NO_WINDOW)
        return target

    def get_state():
        return {"profile": state["profile"] or profile()}

    def set_state(data):
        if isinstance(data, dict) and data.get("profile") and state["mode"] in ("idle", "stopped"):
            state["profile"] = clean_name(data["profile"])

    # Edge starts as soon as the window is open (after set_state had its say about the profile)
    QTimer.singleShot(0, lambda: start() if state["mode"] == "idle" else None)
    if not state["exe"]:
        show_note("Microsoft Edge was not found", "Edge is not installed in the usual place on this PC.", False)
    else:
        show_note("Starting Microsoft Edge…", "", False)
    return {"frame": frame, "state": state, "start": start, "stop": stop, "restart": restart,
            "mode": lambda: state["mode"], "pid": lambda: state["proc"].pid if state["proc"] else None,
            "hwnd": lambda: state["hwnd"], "profile": profile, "open_url": open_url, "poll": poll,
            "get_state": get_state, "set_state": set_state, "shutdown": shutdown, "can_close": lambda: True,
            "note": note, "headline": headline, "detail": detail, "start_button": start_button}


# --------------------------------------------------------------------------
# Tools for the chatbot
# --------------------------------------------------------------------------

def tools(api):
    window_prop = {"window": {"type": "string", "description": "Edge window id or title; may be left out when only one Edge is open"}}

    def pick(args):
        return api["find_window"](args.get("window"), "edge")

    def open_confirm(args):
        inst = pick(args)
        return f"Open {address(text_arg(args, 'url'))} in {inst['title']} (the real Microsoft Edge, profile {inst['app']['profile']()})"

    def open_tool(args):
        inst = pick(args)
        target = inst["app"]["open_url"](text_arg(args, "url"))
        return f"Opening {target} in {inst['title']}. It appears as a new tab; this tool cannot read the page."

    def info_tool(args):
        inst = pick(args)
        app = inst["app"]
        return (f"{inst['title']}: {app['mode']()}, profile {app['profile']()}"
                + (f", Edge process {app['pid']()}" if app["pid"]() else ""))

    def restart_confirm(args):
        inst = pick(args)
        return f"Close and restart Edge in {inst['title']} (its open tabs are saved by Edge, but unsaved page input may be lost)"

    def restart_tool(args):
        inst = pick(args)
        inst["app"]["restart"]()
        return f"Restarting Edge in {inst['title']}."

    return [
        tool("edge_open", "Open a web address (or search for words) as a new tab in the real Microsoft Edge shown in an Edge window of Eden Root (asks Martin first). Use this for video that the Browser app cannot play (H.264, live streams). It cannot read the page.",
             {**window_prop, "url": {"type": "string", "description": "an address like example.com, or words to search for"}}, ["url"], open_tool, open_confirm),
        tool("edge_info", "Say whether an Edge window is starting, running or closed, and which profile it uses.", window_prop, run=info_tool),
        tool("edge_restart", "Close and start again the Edge in an Edge window (asks Martin first).", window_prop, run=restart_tool, confirm=restart_confirm),
    ]

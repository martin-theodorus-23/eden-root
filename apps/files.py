"""Files: the drives, folders and files of this PC as a tree.

    build(parent, say) -> dict

Builds the file tree inside `parent` (an app window's body). Click an arrow to open a folder; type
a path in the address box and press Enter to jump there; right-click for more (open in Notepad, open
a terminal in that folder, show in Explorer, copy the path, new folder, rename). Double-click (or
Enter) opens a file with the program Windows uses for it; programs and scripts ask first.
Nothing here deletes files.

The returned dict holds: frame, toolbar, tree, model, state, reveal, current_path, open_file,
set_hidden, get_state, set_state, can_close.
"""
import os
import subprocess
from pathlib import Path

from PyQt6.QtCore import QDir, QPoint, Qt, QTimer
from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QHeaderView, QInputDialog, QLineEdit, QMessageBox,
                             QTreeView, QVBoxLayout, QWidget)

import theme
from chat.tools import bool_arg, clip, int_arg, text_arg, tool
from theme import ACCENT, BORDER, FG, MUTED, RAISED, button, icon_button, popup_menu, set_on

START = r"D:\raid-disk" if os.path.isdir(r"D:\raid-disk") else os.path.expanduser("~")      # where a new Files window opens (your home folder on other PCs)
RISKY = {".exe", ".bat", ".cmd", ".ps1", ".msi", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".scr",
         ".com", ".lnk", ".reg", ".jar", ".hta", ".dll", ".cpl"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def quote(path):
    """A path as a PowerShell single-quoted string."""
    return "'" + path.replace("'", "''") + "'"


def build(parent, say=None):
    say = say or (lambda text: None)
    state = {"hidden": False, "error": None, "pending": None}

    frame = QWidget(parent)
    column = QVBoxLayout(frame)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(4)

    bar = QWidget()
    bar.setFixedHeight(28)
    toolbar = QHBoxLayout(bar)
    toolbar.setContentsMargins(0, 0, 0, 0)
    toolbar.setSpacing(2)
    column.addWidget(bar)
    up_button = icon_button("up", lambda: go_up(), size=28, strong=True)
    up_button.setToolTip("Up one folder")
    home_button = icon_button("home", lambda: reveal(str(Path.home())), size=28, strong=True)
    home_button.setToolTip("Your user folder")
    path_box = QLineEdit()
    path_box.setPlaceholderText("Type a path and press Enter")
    path_box.setFixedHeight(28)
    path_box.setMinimumWidth(160)
    path_box.setStyleSheet(
        f"QLineEdit {{ background: {RAISED}; border: 1px solid transparent; border-radius: 14px; padding: 0 12px; }}"
        f"QLineEdit:focus {{ border: 1px solid {ACCENT}; }}")
    hidden_button = button("Hidden", lambda: set_hidden(not state["hidden"]), kind="toggle", width=70)
    hidden_button.setToolTip("Show hidden files and folders")
    for widget in (up_button, home_button):
        toolbar.addWidget(widget)
    toolbar.addSpacing(4)
    toolbar.addWidget(path_box, 1)
    toolbar.addSpacing(4)
    toolbar.addWidget(hidden_button)

    model = QFileSystemModel(frame)
    model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs)
    model.setRootPath("")                      # "" = every drive
    tree = QTreeView()
    tree.setModel(model)
    tree.setRootIndex(model.index(""))
    for number in (1, 2, 3):                   # size, type and date modified are not shown: names only
        tree.setColumnHidden(number, True)
    tree.setHeaderHidden(True)
    tree.setSortingEnabled(True)
    tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
    tree.setAnimated(True)
    tree.setUniformRowHeights(True)
    tree.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
    tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    tree.header().setStretchLastSection(False)
    tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    column.addWidget(tree, 1)

    # ---- where we are ----

    def current_path():
        index = tree.currentIndex()
        return os.path.normpath(model.filePath(index)) if index.isValid() else ""

    def shown(path):
        path_box.setText(path)
        path_box.setCursorPosition(0)

    def on_current(index, previous=None):
        path = current_path()
        if path and not path_box.hasFocus():
            shown(path)

    tree.selectionModel().currentChanged.connect(on_current)

    def reveal(path, expand=True):
        """Open the tree down to `path`, select it and scroll to it. False if there is no such path."""
        state["error"] = None
        path = os.path.normpath(os.path.expanduser(str(path).strip().strip('"')))
        if not os.path.exists(path):
            state["error"] = f"There is no such path: {path}"
            say(state["error"])
            return False
        state["pending"] = (path, expand, 30)
        settle()
        return True

    def settle():
        """The model fills folders in the background, so keep opening towards the path until it is
        all there (or a couple of seconds have passed)."""
        pending = state["pending"]
        if not pending:
            return
        path, expand, tries = pending
        chain, probe = [], path
        while True:
            chain.append(probe)
            above = os.path.dirname(probe)
            if above == probe:
                break
            probe = above
        for ancestor in reversed(chain[1:]):
            index = model.index(ancestor)
            if index.isValid():
                tree.expand(index)
        index = model.index(path)
        if index.isValid():
            tree.setCurrentIndex(index)
            tree.scrollTo(index, QTreeView.ScrollHint.PositionAtCenter)
            if expand and os.path.isdir(path):
                tree.expand(index)
        if tries > 1:
            state["pending"] = (path, expand, tries - 1)
            QTimer.singleShot(100, settle)
        else:
            state["pending"] = None
        shown(path)

    def go_up():
        path = current_path() or START
        above = os.path.dirname(path.rstrip("\\/"))
        if above and above != path:
            reveal(above, expand=False)

    path_box.returnPressed.connect(lambda: (reveal(path_box.text()), tree.setFocus()))

    # ---- hidden files ----

    def set_hidden(on):
        state["hidden"] = bool(on)
        flags = QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs
        if on:
            flags |= QDir.Filter.Hidden | QDir.Filter.System
        model.setFilter(flags)
        set_on(hidden_button, state["hidden"])

    # ---- opening things ----

    def open_file(path, quiet=False):
        """Open a file with its default program. A program or script asks first (quiet=True skips the
        question; only the chatbot's tool uses that, after its own question)."""
        path = os.path.normpath(path)
        state["error"] = None
        if not os.path.exists(path):
            state["error"] = f"There is no such file: {path}"
            return False
        if os.path.isdir(path):
            return reveal(path)
        if not quiet and os.path.splitext(path)[1].lower() in RISKY:
            answer = QMessageBox.question(
                frame, "Files", f"{os.path.basename(path)} is a program or script.\nRun / open it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return False
        try:
            os.startfile(path)
        except OSError as err:
            state["error"] = f"Could not open: {err}"
            say(state["error"])
            return False
        say("Opened " + os.path.basename(path))
        return True

    tree.activated.connect(lambda index: open_file(model.filePath(index)) if not model.isDir(index) else None)

    def in_notepad(path):
        inst = theme.open_app("notepad")
        if inst is None:
            return
        if not inst["app"]["open"](path, quiet=True):
            say(inst["app"]["last_error"]() or "Could not open the file")

    def viewer_for(path):
        """(app name, menu caption) of the built-in viewer for this file, or None."""
        if os.path.splitext(path)[1].lower() == ".pdf":
            return "pdf_viewer", "Open in PDF Viewer"
        try:
            from apps import media_viewer
        except Exception:
            return None
        return ("media_viewer", "Open in Media Viewer") if media_viewer.kind_of(path) else None

    def in_viewer(name, path):
        inst = theme.open_app(name)
        if inst is not None and not inst["app"]["open"](path, quiet=True):
            say(inst["app"]["last_error"]() or "Could not open the file")

    def terminal_here(path):
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        inst = theme.open_app("terminal")
        if inst is not None:
            inst["app"]["run"](f"Set-Location -LiteralPath {quote(folder)}")

    def show_in_explorer(path):
        if os.path.isdir(path):
            subprocess.Popen(["explorer", path], creationflags=NO_WINDOW)
        else:
            subprocess.Popen(["explorer", "/select,", path], creationflags=NO_WINDOW)

    def copy_path(path):
        QApplication.clipboard().setText(path)
        say("Copied " + path)

    def new_folder(path):
        base = path if os.path.isdir(path) else os.path.dirname(path)
        name, ok = QInputDialog.getText(frame, "New folder", "Name:")
        name = name.strip()
        if not ok or not name:
            return
        if os.path.basename(name) != name:
            say("A folder name cannot contain \\ or /")
            return
        try:
            os.mkdir(os.path.join(base, name))
        except OSError as err:
            say(f"Could not make the folder: {err}")
            return
        say("Made " + os.path.join(base, name))
        reveal(os.path.join(base, name))

    def rename(path):
        old = os.path.basename(path)
        name, ok = QInputDialog.getText(frame, "Rename", "New name:", text=old)
        name = name.strip()
        if not ok or not name or name == old:
            return
        if os.path.basename(name) != name:
            say("A name cannot contain \\ or /")
            return
        target = os.path.join(os.path.dirname(path), name)
        if os.path.exists(target):
            say(f"{name} already exists here")
            return
        try:
            os.rename(path, target)
        except OSError as err:
            say(f"Could not rename: {err}")
            return
        say(f"Renamed to {name}")
        reveal(target, expand=False)

    def show_menu(pos):
        index = tree.indexAt(pos)
        if not index.isValid():
            return
        tree.setCurrentIndex(index)
        path = os.path.normpath(model.filePath(index))
        is_dir = os.path.isdir(path)
        items = [("Open" if not is_dir else "Open folder",
                  (lambda p=path: reveal(p)) if is_dir else (lambda p=path: open_file(p)), "folder" if is_dir else "page", False)]
        if not is_dir:
            viewer = viewer_for(path)
            if viewer:
                items.append((viewer[1], lambda p=path, n=viewer[0]: in_viewer(n, p), "image" if viewer[0] == "media_viewer" else "document", False))
            items.append(("Open in Notepad", lambda p=path: in_notepad(p), "note", False))
        items += [("Open terminal here", lambda p=path: terminal_here(p), "terminal", False),
                  ("Show in Explorer", lambda p=path: show_in_explorer(p), "folder", False),
                  ("Copy path", lambda p=path: copy_path(p), "copy", False),
                  ("New folder…", lambda p=path: new_folder(p), "newfolder", False)]
        if len(path) > 3:                      # not a drive
            items.append(("Rename…", lambda p=path: rename(p), "rename", False))
        popup_menu(tree, items, width=230, at=tree.viewport().mapToGlobal(pos))

    tree.customContextMenuRequested.connect(show_menu)

    # ---- what main.json keeps ----

    def get_state():
        return {"path": current_path() or START, "hidden": state["hidden"]}

    def set_state(data):
        set_hidden(bool(data.get("hidden")))
        path = data.get("path")
        if isinstance(path, str) and os.path.exists(path):
            reveal(path, expand=False)

    set_hidden(False)
    reveal(START if os.path.isdir(START) else str(Path.home()), expand=False)
    return {"frame": frame, "toolbar": bar, "tree": tree, "model": model, "state": state, "reveal": reveal,
            "current_path": current_path, "open_file": open_file, "set_hidden": set_hidden,
            "get_state": get_state, "set_state": set_state, "path_box": path_box,
            "hidden_button": hidden_button, "up_button": up_button, "home_button": home_button,
            "go_up": go_up, "show_menu": show_menu, "last_error": lambda: state["error"],
            "can_close": lambda: True}


# --------------------------------------------------------------------------
# Tools for the chatbot
# --------------------------------------------------------------------------

def tools(api):
    window_prop = {"window": {"type": "string", "description": "Files window id or title; may be left out when only one Files window is open"}}

    def pick(args):
        return api["find_window"](args.get("window"), "files")

    def listing(args):
        path = os.path.normpath(text_arg(args, "path").strip().strip('"') or START)
        limit = int_arg(args, "limit", 100, 1, 300)
        if not os.path.isdir(path):
            raise ValueError(f"{path} is not a folder" if os.path.exists(path) else f"There is no such folder: {path}")
        rows = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            rows.append((0, entry.name.lower(), f"[folder] {entry.name}"))
                        else:
                            rows.append((1, entry.name.lower(), f"{entry.name}  ({entry.stat().st_size} bytes)"))
                    except OSError:
                        rows.append((1, entry.name.lower(), f"{entry.name}  (not readable)"))
        except OSError as err:
            raise ValueError(f"Could not read {path}: {err}")
        rows.sort()
        more = f"\n... and {len(rows) - limit} more" if len(rows) > limit else ""
        return f"{path} ({len(rows)} items):\n" + "\n".join(r[2] for r in rows[:limit]) + more

    def goto(args):
        inst = pick(args)
        path = text_arg(args, "path").strip()
        if not inst["app"]["reveal"](path):
            raise ValueError(inst["app"]["last_error"]() or "could not open that path")
        return f"{inst['title']} now shows {os.path.normpath(path)}."

    def selected(args):
        inst = pick(args)
        return f"{inst['title']} has selected: {inst['app']['current_path']() or '(nothing)'}"

    def open_confirm(args):
        return f"Open {os.path.normpath(text_arg(args, 'path').strip())} with the program Windows uses for it"

    def open_it(args):
        path = os.path.normpath(text_arg(args, "path").strip().strip('"'))
        inst = pick(args)
        if not inst["app"]["open_file"](path, quiet=True):
            raise ValueError(inst["app"]["last_error"]() or "could not open it")
        return f"Opened {path}."

    return [
        tool("files_list", "List the files and folders inside a folder on this PC (names and sizes). Does not need a Files window.",
             {"path": {"type": "string", "description": "full path of the folder; default is D:\\raid-disk"},
              "limit": {"type": "integer", "description": "most entries to show (default 100)"}}, run=listing),
        tool("files_goto", "Show a path in a Files window: open the tree down to it and select it.",
             {**window_prop, "path": {"type": "string"}}, ["path"], goto),
        tool("files_selected", "Tell which file or folder is selected in a Files window.", window_prop, run=selected),
        tool("files_open", "Open a file with the program Windows uses for it (asks Martin first).",
             {**window_prop, "path": {"type": "string", "description": "full path of the file"}}, ["path"], open_it, open_confirm),
    ]

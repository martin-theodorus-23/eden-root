"""Servers: the files and folders on your SSH servers, as a tree.

    build(parent, say) -> dict

The servers are the Host entries of your ~/.ssh/config, listed as the top of the tree. Click an
arrow to connect (with the key that entry names) and open a folder; folders are read when you open
them. This app only looks: it never changes anything on a server. Right-click a file to Download
it (you choose where it goes) or to see a copy of a small text file in Notepad.

Connections use SFTP over SSH and trust only servers already in your known_hosts (connect once
with `ssh <name>` in a terminal if a server says its key is not known). Passwords are never asked
or stored: a server that needs a password must have a key set up.
Paths are shown as ssh://<name>/<folder>; the same form (or <name>:/<folder>) can be typed in the
address box.

A status bar along the bottom shows the selected server's connection (connecting, connected, not
connected), what is selected (the number of folders and files in an opened folder, or the size and
date of a file, or the error), how many servers are connected, and "working…" while it talks to one.

The returned dict holds: frame, toolbar, tree, state, hosts, goto, list_dir, current, refresh,
set_hidden, get_state, set_state, shutdown, can_close, status_label, status_text, update_status.
"""
import html
import os
import posixpath
import queue
import re
import socket
import stat as statmod
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (QApplication, QFileDialog, QFileIconProvider, QHBoxLayout, QHeaderView,
                             QLineEdit, QSizePolicy, QTreeView, QVBoxLayout, QWidget)

import theme
from chat.tools import bool_arg, clip, int_arg, text_arg, tool
from theme import ACCENT, DANGER, MUTED, RAISED, button, icon_button, label, popup_menu, set_on

CONFIG = Path.home() / ".ssh" / "config"
KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts"
MAX_ENTRIES = 5000
VIEW_LIMIT = 1_000_000                     # the biggest file shown in Notepad
ROLE = Qt.ItemDataRole.UserRole


# ---- the servers: ~/.ssh/config ----

def host_names(config=None):
    """The Host names in the config file, in file order (patterns with * or ? are left out)."""
    try:
        text = Path(config or CONFIG).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names = []
    for line in text.splitlines():
        match = re.match(r"^\s*Host\s+(.+?)\s*$", line, re.IGNORECASE)
        if match:
            for name in match.group(1).split():
                if not re.search(r"[*?!]", name) and name not in names:
                    names.append(name)
    return names


def host_info(name, config=None):
    """What the config says about one server: address, user, port and key files."""
    import paramiko
    try:
        cfg = paramiko.SSHConfig.from_path(str(config or CONFIG)).lookup(name)
    except OSError:
        cfg = {}
    keys = [os.path.expanduser(k.strip('"')) for k in cfg.get("identityfile", [])]
    return {"name": name, "hostname": cfg.get("hostname", name), "user": cfg.get("user"),
            "port": int(cfg.get("port", 22)), "keys": keys,
            "only_these_keys": str(cfg.get("identitiesonly", "no")).lower() == "yes"}


def explain(err):
    """A short reason, in words, for a failed connection or listing."""
    import paramiko
    if isinstance(err, paramiko.AuthenticationException):
        return "login refused (the key was not accepted, or this server needs a password)"
    if isinstance(err, paramiko.ssh_exception.BadHostKeyException):
        return "the server's key does not match known_hosts (it may have changed)"
    if isinstance(err, paramiko.SSHException) and "known_hosts" in str(err):
        return "this server is not in known_hosts yet: run `ssh <name>` once in a terminal to trust it"
    if isinstance(err, (socket.timeout, TimeoutError)):
        return "the server did not answer (timed out)"
    if isinstance(err, PermissionError):
        return "permission denied"
    if isinstance(err, FileNotFoundError):
        return "no such file or folder"
    return f"{type(err).__name__}: {err}" if str(err) else type(err).__name__


class Session:
    """One open SSH + SFTP connection to a server."""

    def __init__(self, name):
        import paramiko
        info = host_info(name)
        self.info = info
        self.client = paramiko.SSHClient()
        self.client.load_system_host_keys(str(KNOWN_HOSTS))
        self.client.set_missing_host_key_policy(paramiko.RejectPolicy())     # only servers already trusted
        self.client.connect(
            info["hostname"], port=info["port"], username=info["user"], key_filename=info["keys"] or None,
            look_for_keys=not info["only_these_keys"], allow_agent=not info["only_these_keys"],
            timeout=12, banner_timeout=15, auth_timeout=15)
        self.sftp = self.client.open_sftp()

    def home(self):
        return self.sftp.normalize(".")

    def listdir(self, path):
        """[{name, dir, size, mtime}] for a folder, folders first."""
        rows = []
        for attr in self.sftp.listdir_attr(path)[:MAX_ENTRIES]:
            is_dir = statmod.S_ISDIR(attr.st_mode or 0)
            if statmod.S_ISLNK(attr.st_mode or 0):             # a link: is what it points to a folder?
                try:
                    is_dir = statmod.S_ISDIR(self.sftp.stat(posixpath.join(path, attr.filename)).st_mode)
                except OSError:
                    pass
            rows.append({"name": attr.filename, "dir": is_dir, "size": attr.st_size or 0,
                         "mtime": attr.st_mtime or 0})
        rows.sort(key=lambda r: (not r["dir"], r["name"].lower()))
        return rows

    def download(self, remote, local):
        self.sftp.get(remote, local)
        return os.path.getsize(local)

    def read(self, remote, limit=VIEW_LIMIT):
        with self.sftp.open(remote, "rb") as f:
            return f.read(limit + 1)

    def close(self):
        for thing in (self.sftp, self.client):
            try:
                thing.close()
            except Exception:
                pass


def open_session(name):
    """Connect to a server (the one place that talks to the network: tests replace it)."""
    return Session(name)


# ---- doing network work off the window's thread ----

class Future:
    def __init__(self, callback=None):
        self.done, self.value, self.error, self.callback = False, None, None, callback


class Worker(threading.Thread):
    """One thread per server: it connects when first needed and does its jobs one after another."""

    def __init__(self, name, results):
        super().__init__(daemon=True)
        self.name_, self.results, self.jobs, self.session = name, results, queue.Queue(), None
        self.start()

    def submit(self, func, callback=None):
        future = Future(callback)
        self.jobs.put((func, future))
        return future

    def run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                break
            func, future = job
            try:
                if self.session is None:
                    self.session = open_session(self.name_)
                future.value = func(self.session)
            except Exception as err:
                future.error = explain(err)
                if not isinstance(err, (FileNotFoundError, PermissionError)):
                    self.close_session()                     # a broken connection: start a new one next time
            future.done = True
            self.results.put(future)
        self.close_session()

    def close_session(self):
        session, self.session = self.session, None
        if session is not None:
            session.close()

    def stop(self):
        self.jobs.put(None)


def size_text(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def build(parent, say=None):
    say = say or (lambda text: None)
    state = {"hidden": False, "goto": None, "closed": False, "homes": {}, "last": None, "busy": 0, "status": ""}
    results, workers, nodes = queue.Queue(), {}, {}
    icons = QFileIconProvider()

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
    refresh_button = icon_button("refresh", lambda: refresh(), size=28, strong=True)
    refresh_button.setToolTip("Read this folder again")
    path_box = QLineEdit()
    path_box.setPlaceholderText("ssh://name/folder  (press Enter)")
    path_box.setFixedHeight(28)
    path_box.setMinimumWidth(160)
    path_box.setStyleSheet(
        f"QLineEdit {{ background: {RAISED}; border: 1px solid transparent; border-radius: 14px; padding: 0 12px; }}"
        f"QLineEdit:focus {{ border: 1px solid {ACCENT}; }}")
    hidden_button = button("Hidden", lambda: set_hidden(not state["hidden"]), kind="toggle", width=70)
    hidden_button.setToolTip("Show hidden files (names that start with a dot)")
    toolbar.addWidget(up_button)
    toolbar.addWidget(refresh_button)
    toolbar.addSpacing(4)
    toolbar.addWidget(path_box, 1)
    toolbar.addSpacing(4)
    toolbar.addWidget(hidden_button)

    model = QStandardItemModel(frame)
    model.setHorizontalHeaderLabels(["Name", "Size", "Modified"])
    tree = QTreeView()
    tree.setModel(model)
    tree.setAnimated(True)
    tree.setUniformRowHeights(True)
    tree.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
    tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    tree.header().setStretchLastSection(False)
    tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    tree.setColumnHidden(1, True)                  # size and date modified are not shown: names only
    tree.setColumnHidden(2, True)
    tree.setHeaderHidden(True)
    column.addWidget(tree, 1)

    # the status bar: the selected server's connection, what is selected, how many servers are connected
    status_label = label("", 12, MUTED)
    status_label.setTextFormat(Qt.TextFormat.RichText)
    status_label.setFixedHeight(22)
    status_label.setContentsMargins(8, 0, 8, 0)
    status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)     # a long line is cut, not widening the window
    column.addWidget(status_label)

    # ---- the tree's items ----

    def node_of(item):
        return nodes.get(item.data(ROLE)) if item is not None else None

    def add_row(parent_item, entry_node, size="", when=""):
        """One row (name, size, modified). entry_node: dict with host, path, kind, name."""
        name_item = QStandardItem(entry_node["name"])
        name_item.setEditable(False)
        name_item.setData(len(nodes) + 1, ROLE)
        kind = entry_node["kind"]
        name_item.setIcon(icons.icon(QFileIconProvider.IconType.Network if kind == "host" else
                                     QFileIconProvider.IconType.Folder if kind == "dir" else
                                     QFileIconProvider.IconType.File))
        entry_node.update(item=name_item, loaded=False, loading=False, error=None, size=size, when=when)
        nodes[name_item.data(ROLE)] = entry_node
        size_item, when_item = QStandardItem(size), QStandardItem(when)
        for cell in (size_item, when_item):
            cell.setEditable(False)
            cell.setForeground(QColor(MUTED))
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        parent_item.appendRow([name_item, size_item, when_item])
        if kind in ("host", "dir"):
            placeholder(name_item)
        return name_item

    def placeholder(item, text="…"):
        row = QStandardItem(text)
        row.setEnabled(False)
        row.setEditable(False)
        item.appendRow([row, QStandardItem(), QStandardItem()])

    for name in host_names():
        add_row(model.invisibleRootItem(), {"name": name, "host": name, "path": "/", "kind": "host"})

    def hosts():
        return [node_of(model.item(r))["name"] for r in range(model.rowCount()) if node_of(model.item(r))]

    def apply_hidden(item):
        """Hide (or show) the dot-files among an item's children."""
        parent_index = item.index() if item is not model.invisibleRootItem() else tree.rootIndex()
        for row in range(item.rowCount()):
            child = item.child(row, 0)
            node = node_of(child)
            tree.setRowHidden(row, parent_index, bool(node and node["name"].startswith(".") and not state["hidden"]))

    def walk(item):
        for row in range(item.rowCount()):
            child = item.child(row, 0)
            if child is not None:
                yield child
                yield from walk(child)

    def set_hidden(on):
        state["hidden"] = bool(on)
        set_on(hidden_button, state["hidden"])
        for item in walk(model.invisibleRootItem()):
            if node_of(item) and node_of(item)["kind"] != "file":
                apply_hidden(item)
        update_status()

    # ---- loading a folder ----

    def worker_for(host):
        if host not in workers:
            workers[host] = Worker(host, results)
        return workers[host]

    def submit(host, func, callback=None):
        state["busy"] += 1                                      # counted down in poll() when the answer arrives
        future = worker_for(host).submit(func, callback)
        update_status()                                         # "working…"
        return future

    def list_dir(host, path):
        """Read a folder in the background. Returns a Future (.done, .value = rows, .error)."""
        return submit(host, lambda session: session.listdir(path))

    def load(item):
        node = node_of(item)
        if node is None or node["loading"] or node["loaded"] or node["kind"] == "file":
            return
        node["loading"] = True
        node["error"] = None
        item.removeRows(0, item.rowCount())
        placeholder(item, "Loading…")
        update_status()
        host, path, is_host = node["host"], node["path"], node["kind"] == "host"
        if is_host:
            say(f"Connecting to {host}…")

        def job(session):
            rows = session.listdir(path)
            home = session.home() if is_host else None
            return rows, home

        submit(host, job, lambda value, error: loaded(item, value, error))

    def loaded(item, value, error):
        node = node_of(item)
        if state["closed"] or node is None or node.get("item") is not item:
            return
        try:
            item.removeRows(0, item.rowCount())
        except RuntimeError:
            return
        node["loading"] = False
        node["error"] = error or None
        if error:
            placeholder(item, "⚠ " + error)
            item.child(0, 0).setForeground(QColor(DANGER))
            say(f"{node['host']}: {error}")
            state["goto"] = None
            return
        rows, home = value
        node["loaded"] = True
        for entry in rows:
            add_row(item, {"name": entry["name"], "host": node["host"], "kind": "dir" if entry["dir"] else "file",
                           "path": posixpath.join(node["path"], entry["name"])},
                    "" if entry["dir"] else size_text(entry["size"]),
                    time.strftime("%d-%m-%Y %H:%M", time.localtime(entry["mtime"])) if entry["mtime"] else "")
        apply_hidden(item)
        if home:
            state["homes"][node["host"]] = home
            say(f"Connected to {node['host']}")
            if state["goto"] is None:
                begin_goto(node["host"], home)
        advance_goto()

    def on_expanded(index):
        load(model.itemFromIndex(index.siblingAtColumn(0)))

    tree.expanded.connect(on_expanded)

    def refresh(item=None):
        """Read the selected folder (or the one given) again."""
        item = item or current_item()
        if item is None:
            return
        node = node_of(item)
        if node["kind"] == "file":
            item = item.parent() or model.invisibleRootItem()
            node = node_of(item)
        if node is None:
            return
        node["loaded"] = False
        if tree.isExpanded(item.index()):
            load(item)
        else:
            item.removeRows(0, item.rowCount())
            placeholder(item)

    # ---- going to a path ----

    def current_item():
        index = tree.currentIndex()
        return model.itemFromIndex(index.siblingAtColumn(0)) if index.isValid() else None

    def current():
        """(host, path) of the selected row, or None."""
        node = node_of(current_item())
        return (node["host"], node["path"]) if node else None

    def address(host, path):
        return f"ssh://{host}{path if path.startswith('/') else '/' + path}"

    def on_current(index, previous=None):
        where = current()
        if where and not path_box.hasFocus():
            path_box.setText(address(*where))
            path_box.setCursorPosition(0)
        update_status()

    tree.selectionModel().currentChanged.connect(on_current)

    # ---- the status bar ----

    def count_text(item):
        """'12 items: 3 folders, 9 files' for an opened folder (dot-files hidden by the switch are left out)."""
        parent_index = item.index() if item is not model.invisibleRootItem() else tree.rootIndex()
        folders = files = hidden = 0
        for row in range(item.rowCount()):
            node = node_of(item.child(row, 0))
            if node is None:
                continue
            if tree.isRowHidden(row, parent_index):
                hidden += 1
            elif node["kind"] == "dir":
                folders += 1
            else:
                files += 1
        total = folders + files
        text = "empty folder" if total == 0 else f"{total} item{'s' if total != 1 else ''}: {folders} folder{'s' if folders != 1 else ''}, {files} file{'s' if files != 1 else ''}"
        return text + (f" (+{hidden} hidden)" if hidden else "")

    def connected_hosts():
        return [name for name, worker in workers.items() if worker.session is not None]

    def status_parts():
        """[(text, colour)]: connection of the selected server, the selection, then the totals."""
        node = node_of(current_item())
        parts = []
        if node is None:
            parts.append(("Select a server or a folder", MUTED))
        else:
            host = node["host"]
            connected = host in connected_hosts()
            host_node = node_of(find_host_item(host)) if find_host_item(host) else node
            if host_node["loading"] and not connected:
                parts.append((f"● {host}: connecting…", ACCENT))
            elif connected:
                parts.append((f"● {host}: connected", ACCENT))
            elif host_node.get("error"):
                parts.append((f"● {host}: not connected", DANGER))
            else:
                parts.append((f"● {host}: not connected", MUTED))
            if node.get("error"):
                parts.append((node["error"], DANGER))
            elif node["kind"] == "file":
                parts.append((" · ".join(t for t in (node.get("size"), node.get("when")) if t) or "file", MUTED))
            elif node["loading"]:
                parts.append(("reading…", MUTED))
            elif node["loaded"]:
                text = count_text(node["item"])
                if node["kind"] == "host" and state["homes"].get(host):
                    text += f" · home {state['homes'][host]}"
                parts.append((text, MUTED))
            else:
                parts.append(("open it to connect" if node["kind"] == "host" else "not opened yet", MUTED))
        total = len(hosts())
        parts.append((f"{len(connected_hosts())} of {total} server{'s' if total != 1 else ''} connected", MUTED))
        if state["busy"] > 0:
            parts.append(("working…", ACCENT))
        return parts

    def update_status():
        """Redraw the status bar (only when its words changed)."""
        if state["closed"]:
            return
        parts = status_parts()
        plain = "  ·  ".join(text for text, _ in parts)
        if plain == state["status"]:
            return
        state["status"] = plain
        gap = f"<span style='color:{MUTED}'>&nbsp;&nbsp;·&nbsp;&nbsp;</span>"
        status_label.setText(gap.join(f"<span style='color:{colour}'>{html.escape(text)}</span>" for text, colour in parts))
        status_label.setToolTip(plain)

    def status_text():
        """The status bar's words, parts joined with ' · '."""
        update_status()
        return state["status"]

    def find_host_item(host):
        for r in range(model.rowCount()):
            node = node_of(model.item(r))
            if node and node["name"].lower() == host.lower():
                return model.item(r)
        return None

    def begin_goto(host, path, expand=True):
        item = find_host_item(host)
        if item is None:
            say(f"There is no server called {host}")
            return False
        parts = [p for p in posixpath.normpath(path or "/").split("/") if p]
        state["goto"] = {"item": item, "parts": parts, "expand": expand, "host": node_of(item)["name"]}
        return True

    def advance_goto():
        """Open the tree one folder at a time towards the wanted path (each folder is read first)."""
        goal = state["goto"]
        if goal is None:
            return
        item = goal["item"]
        node = node_of(item)
        if not node["loaded"]:
            tree.expand(item.index())                  # reads it; loaded() calls this again
            return
        if not tree.isExpanded(item.index()):
            tree.expand(item.index())
        if not goal["parts"]:
            state["goto"] = None
            tree.setCurrentIndex(item.index())
            tree.scrollTo(item.index(), QTreeView.ScrollHint.PositionAtCenter)
            return
        wanted = goal["parts"][0]
        child = next((item.child(r, 0) for r in range(item.rowCount())
                      if node_of(item.child(r, 0)) and node_of(item.child(r, 0))["name"] == wanted), None)
        if child is None:
            say(f"{posixpath.join(node['path'], wanted)} was not found on {goal['host']}")
            state["goto"] = None
            tree.setCurrentIndex(item.index())
            return
        goal["parts"].pop(0)
        goal["item"] = child
        child_node = node_of(child)
        if child_node["kind"] == "file":
            state["goto"] = None
            tree.setCurrentIndex(child.index())
            tree.scrollTo(child.index(), QTreeView.ScrollHint.PositionAtCenter)
            return
        if not goal["parts"] and not goal["expand"]:
            state["goto"] = None
            tree.setCurrentIndex(child.index())
            tree.scrollTo(child.index(), QTreeView.ScrollHint.PositionAtCenter)
            return
        advance_goto()

    def goto(host, path=None, expand=True):
        """Open the tree down to a folder or file on a server, connecting if needed. `path` None = its home folder."""
        item = find_host_item(host)
        if item is None:
            say(f"There is no server called {host}")
            return False
        if path is None:
            home = state["homes"].get(node_of(item)["name"])
            if home is None:                           # not connected yet: connecting opens the home folder
                state["goto"] = None
                tree.setCurrentIndex(item.index())
                tree.expand(item.index())
                return True
            path = home
        begin_goto(host, path, expand)
        advance_goto()
        return True

    def parse_address(text):
        text = text.strip()
        match = re.match(r"^ssh://([^/]+)(/.*)?$", text, re.IGNORECASE) or re.match(r"^([^:/\s]+):(/.*)?$", text)
        if match:
            return match.group(1), match.group(2) or "/"
        if find_host_item(text):
            return text, None
        return None

    def go_from_box():
        parsed = parse_address(path_box.text())
        if parsed is None:
            say("Type ssh://<server>/<folder>, for example ssh://EdenNexus/var/www")
            return
        goto(parsed[0], parsed[1])
        tree.setFocus()

    path_box.returnPressed.connect(go_from_box)

    def go_up():
        where = current()
        if where is None:
            return
        host, path = where
        node = node_of(current_item())
        if node["kind"] == "host":
            return
        above = posixpath.dirname(path.rstrip("/")) or "/"
        goto(host, above, expand=False)

    # ---- what can be done with a file ----

    def download(item):
        node = node_of(item)
        start = os.path.join(str(Path.home() / "Downloads"), node["name"])
        local, _ = QFileDialog.getSaveFileName(frame, "Save a copy", start)
        if not local:
            return
        say(f"Downloading {node['name']}…")

        def done(value, error):
            say(f"Could not download {node['name']}: {error}" if error else f"Downloaded {node['name']} ({size_text(value)})")

        submit(node["host"], lambda session: session.download(node["path"], local), done)

    def view_in_notepad(item):
        node = node_of(item)
        say(f"Reading {node['name']}…")

        def done(value, error):
            if error:
                say(f"Could not read {node['name']}: {error}")
                return
            if len(value) > VIEW_LIMIT:
                say(f"{node['name']} is bigger than {size_text(VIEW_LIMIT)}: use Download")
                return
            try:
                text = value.decode("utf-8")
            except UnicodeDecodeError:
                say(f"{node['name']} is not a text file: use Download")
                return
            inst = theme.open_app("notepad")
            if inst is not None:
                inst["app"]["set_text"](text)
                theme.set_window_title(inst, f"{node['host']}: {node['name']}")
                say(f"A copy of {node['name']} is in Notepad (saving it does not change the server)")

        submit(node["host"], lambda session: session.read(node["path"]), done)

    def copy_path(item):
        node = node_of(item)
        text = address(node["host"], node["path"])
        QApplication.clipboard().setText(text)
        say("Copied " + text)

    def disconnect(item):
        node = node_of(item)
        worker = workers.pop(node["host"], None)
        if worker is not None:
            worker.stop()
        node["loaded"], node["loading"] = False, False
        item.removeRows(0, item.rowCount())
        placeholder(item)
        tree.collapse(item.index())
        say(f"Disconnected from {node['host']}")
        update_status()

    def show_menu(pos):
        index = tree.indexAt(pos)
        if not index.isValid():
            return
        tree.setCurrentIndex(index)
        item = model.itemFromIndex(index.siblingAtColumn(0))
        node = node_of(item)
        if node is None:
            return
        items = []
        if node["kind"] == "file":
            items += [("Download…", lambda: download(item), "download", False),
                      ("View copy in Notepad", lambda: view_in_notepad(item), "note", False)]
        else:
            items.append(("Read again", lambda: refresh(item), "refresh", False))
        items.append(("Copy path", lambda: copy_path(item), "copy", False))
        if node["kind"] == "host":
            items.append(("Disconnect", lambda: disconnect(item), "close", False))
        popup_menu(tree, items, width=230, at=tree.viewport().mapToGlobal(pos))

    tree.customContextMenuRequested.connect(show_menu)

    # ---- results come back here (the threads never touch the window) ----

    def poll():
        got = False
        while True:
            try:
                future = results.get_nowait()
            except queue.Empty:
                break
            state["busy"] = max(0, state["busy"] - 1)
            got = True
            if future.callback and not state["closed"]:
                try:
                    future.callback(future.value, future.error)
                except RuntimeError:               # the row or window is gone
                    pass
        if got:
            update_status()                        # the connection may have come up or gone down meanwhile

    timer = QTimer(frame)
    timer.timeout.connect(poll)
    timer.start(40)

    def shutdown():
        state["closed"] = True
        try:
            timer.stop()
        except RuntimeError:
            pass
        for worker in workers.values():
            worker.stop()
        workers.clear()

    frame.destroyed.connect(lambda *args: shutdown())

    # ---- what main.json keeps: only the hidden switch (a server is never contacted by itself) ----

    def get_state():
        return {"hidden": state["hidden"]}

    def set_state(data):
        set_hidden(bool(data.get("hidden")))

    if not host_names():
        say("No servers found in " + str(CONFIG))
    update_status()
    return {"status_label": status_label, "status_text": status_text, "update_status": update_status,
"frame": frame, "toolbar": bar, "tree": tree, "model": model, "state": state, "hosts": hosts,
            "goto": goto, "list_dir": list_dir, "current": current, "refresh": refresh, "set_hidden": set_hidden,
            "get_state": get_state, "set_state": set_state, "shutdown": shutdown, "can_close": lambda: True,
            "path_box": path_box, "hidden_button": hidden_button, "show_menu": show_menu, "poll": poll,
            "up_button": up_button, "refresh_button": refresh_button, "go_up": go_up, "nodes": nodes,
            "find_host_item": find_host_item, "download": download, "view_in_notepad": view_in_notepad,
            "disconnect": disconnect}


# --------------------------------------------------------------------------
# Tools for the chatbot
# --------------------------------------------------------------------------

def tools(api):
    window_prop = {"window": {"type": "string", "description": "Servers window id or title; may be left out when only one Servers window is open"}}
    host_prop = {"host": {"type": "string", "description": "server name from servers_hosts, e.g. EdenNexus"}}

    def pick(args):
        return api["find_window"](args.get("window"), "servers")

    def known_host(inst, args):
        name = text_arg(args, "host").strip()
        for candidate in inst["app"]["hosts"]():
            if candidate.lower() == name.lower():
                return candidate
        raise ValueError(f"There is no server called '{name}'. Servers: {', '.join(inst['app']['hosts']()) or 'none'}")

    def hosts_tool(args):
        lines = []
        for name in host_names():
            info = host_info(name)
            lines.append(f"{name}: {info['user'] or '?'}@{info['hostname']}:{info['port']}"
                         f"{' (key ' + os.path.basename(info['keys'][0]) + ')' if info['keys'] else ' (no key set)'}")
        return "\n".join(lines) or f"No servers in {CONFIG}"

    def list_confirm(args):
        inst = pick(args)
        host = known_host(inst, args)
        info = host_info(host)
        return f"Connect to {host} ({info['user']}@{info['hostname']}) and list the folder {text_arg(args, 'path', '/') or '/'}"

    def list_tool(args):
        inst = pick(args)
        host = known_host(inst, args)
        path = text_arg(args, "path", "/").strip() or "/"
        limit = int_arg(args, "limit", 100, 1, 300)
        future = inst["app"]["list_dir"](host, path)
        started = time.time()

        def poll():
            if not future.done:
                return None if time.time() - started < 45 else "Error: the server did not answer"
            if future.error:
                return f"Error: {host}: {future.error}"
            rows = future.value
            more = f"\n... and {len(rows) - limit} more" if len(rows) > limit else ""
            return (f"{address_text(host, path)} ({len(rows)} items):\n"
                    + "\n".join(f"[folder] {r['name']}" if r["dir"] else f"{r['name']}  ({r['size']} bytes)" for r in rows[:limit]) + more)
        return poll

    def address_text(host, path):
        return f"ssh://{host}{path if path.startswith('/') else '/' + path}"

    def goto_tool(args):
        inst = pick(args)
        host = known_host(inst, args)
        path = text_arg(args, "path").strip() or None
        inst["app"]["goto"](host, path)
        return f"{inst['title']} is opening {address_text(host, path or '~')}. It may take a moment while it connects."

    return [
        tool("servers_hosts", "List the SSH servers set up on this PC (name, user, address, key). Does not connect to anything.", run=hosts_tool),
        tool("servers_list", "Connect to a server and list the files and folders in one of its folders (names and sizes). Needs a Servers window. Asks Martin first. Read-only.",
             {**window_prop, **host_prop, "path": {"type": "string", "description": "folder on the server, e.g. /var/www (default /)"},
              "limit": {"type": "integer", "description": "most entries to show (default 100)"}}, ["host"], list_tool, list_confirm, timeout=60),
        tool("servers_goto", "Show a server folder or file in a Servers window: open the tree down to it (connects to the server; asks Martin first).",
             {**window_prop, **host_prop, "path": {"type": "string", "description": "folder or file on the server; default is its home folder"}},
             ["host"], goto_tool, list_confirm),
    ]

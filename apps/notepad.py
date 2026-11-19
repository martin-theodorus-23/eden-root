"""Notepad: write, edit, open and save plain text files.

    build(parent, say) -> dict

Builds the notepad inside `parent` (an app window's body). `say(text)` shows a short message
(the status bar). The returned dict holds the widgets and functions:
frame, text, new, open, save, save_as, write, can_close.

Keys: Ctrl+N new, Ctrl+O open, Ctrl+S save, Ctrl+Shift+S save as, Ctrl+A select all.
A dot next to the file name means there are unsaved changes.

Two dropdowns in the toolbar:
  View      Text, Markdown preview, Text + Markdown side by side, Table (CSV). The preview and the
            table follow what you type.
  Language  colours for code (Python, JSON, JavaScript, HTML, CSS, SQL, Markdown, Shell). Auto picks
            by the file's ending; Plain turns the colours off.
Also in the returned dict: set_view, set_language, get_view, get_language.
"""
import csv
import io
import os
import re

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QHeaderView, QMessageBox, QPlainTextEdit,
                             QSplitter, QTableView, QTextBrowser, QVBoxLayout, QWidget)

from chat.tools import bool_arg, clip, text_arg, tool
from theme import ACCENT, CODE_BG, FG, MUTED, SYNTAX, NameChip, button, font, label, mono_font, popup_menu, set_color, set_on

FILTER = "Text files (*.txt *.md *.py *.json *.csv *.log);;All files (*.*)"
DOT = "  ●"
VIEWS = (("text", "Text"), ("markdown", "Markdown preview"), ("split", "Text + Markdown side by side"),
         ("table", "Table (CSV)"))
MAX_ROWS = 20000                                 # the table shows this many rows at most
MAX_COLOURED = 1_500_000                         # bigger texts are not coloured (it would lag)


# ---- code colours ----

def words(*names):
    return r"\b(?:" + "|".join(names) + r")\b"


def line_comment(marker):
    """A comment to the end of the line, skipping over strings (so a # inside "..." is not one).
    Only group 1 (the comment) gets coloured."""
    return rf"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|({marker}.*$)"""


DQ = r'"(?:[^"\\]|\\.)*"'
SQ = r"'(?:[^'\\]|\\.)*'"
NUMBER = r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b"
C_COMMENT = [(r"/\*", r"\*/", "comment")]

LANGS = {
    "Python": {"ext": (".py", ".pyw"), "rules": [
        (words("and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif", "else",
               "except", "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
               "or", "pass", "raise", "return", "try", "while", "with", "yield", "None", "True", "False", "self"), "keyword"),
        (NUMBER, "number"), (r"\b(?:def|class)\s+(\w+)", "function"), (r"@\w+(?:\.\w+)*", "special"),
        (r"[rbfRBF]{0,2}" + DQ, "string"), (r"[rbfRBF]{0,2}" + SQ, "string"), (line_comment("#"), "comment")],
        "multi": [('"""', '"""', "string"), ("'''", "'''", "string")]},
    "JSON": {"ext": (".json", ".jsonc"), "rules": [
        (DQ, "string"), (r'"(?:[^"\\]|\\.)*"(?=\s*:)', "key"), (r"-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b", "number"),
        (words("true", "false", "null"), "keyword")], "multi": []},
    "JavaScript": {"ext": (".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx"), "rules": [
        (words("async", "await", "break", "case", "catch", "class", "const", "continue", "default", "delete", "do",
               "else", "export", "extends", "finally", "for", "from", "function", "if", "import", "in", "instanceof",
               "let", "new", "null", "of", "return", "static", "super", "switch", "this", "throw", "try", "typeof",
               "undefined", "var", "void", "while", "yield", "true", "false", "interface", "type"), "keyword"),
        (NUMBER, "number"), (r"\bfunction\s+(\w+)", "function"), (r"\b(\w+)(?=\()", "function"),
        (DQ, "string"), (SQ, "string"), (r"`(?:[^`\\]|\\.)*`", "string"), (line_comment("//"), "comment")],
        "multi": C_COMMENT},
    "HTML": {"ext": (".html", ".htm", ".xml", ".svg"), "rules": [
        (r"</?[A-Za-z][\w:.-]*|/?>", "tag"), (r"[\w:.-]+(?=\s*=)", "attr"), (DQ, "string"), (SQ, "string"),
        (r"&#?\w+;", "special")], "multi": [("<!--", "-->", "comment")]},
    "CSS": {"ext": (".css",), "rules": [
        (r"@[\w-]+", "keyword"), (r"[\w-]+(?=\s*:)", "key"), (r"#[0-9a-fA-F]{3,8}\b", "number"),
        (r"-?\b\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|s|ms|deg|fr)?", "number"), (DQ, "string"), (SQ, "string")],
        "multi": C_COMMENT},
    "SQL": {"ext": (".sql",), "nocase": True, "rules": [
        (words("select", "from", "where", "and", "or", "not", "null", "insert", "into", "values", "update", "set",
               "delete", "create", "table", "drop", "alter", "add", "index", "join", "left", "right", "inner", "outer",
               "on", "as", "group", "by", "order", "having", "limit", "offset", "distinct", "union", "all", "in",
               "is", "like", "between", "case", "when", "then", "else", "end", "primary", "key", "foreign",
               "references", "default", "unique", "view", "with", "exists", "asc", "desc", "count", "sum", "avg",
               "min", "max", "pragma"), "keyword"),
        (NUMBER, "number"), (SQ, "string"), (line_comment("--"), "comment")], "multi": C_COMMENT},
    "Markdown": {"ext": (".md", ".markdown"), "rules": [
        (r"^\s*(?:[-*+]|\d+\.)\s", "special"), (r"\*\*[^*]+\*\*|__[^_]+__", "attr"), (r"`[^`]+`", "string"),
        (r"\[[^\]]*\]\([^)]*\)", "function"), (r"^>.*$", "comment"), (r"^#{1,6}\s.*$", "heading")],
        "multi": [("^```", "^```", "string")]},
    "Shell": {"ext": (".ps1", ".bat", ".cmd", ".sh"), "nocase": True, "rules": [
        (words("if", "then", "else", "elseif", "elif", "fi", "for", "foreach", "while", "do", "done", "function",
               "return", "param", "switch", "case", "esac", "in", "exit", "goto", "call", "set", "echo", "try",
               "catch", "finally"), "keyword"),
        (r"\$\{?\w+\}?|%\w+%", "special"), (NUMBER, "number"), (DQ, "string"), (SQ, "string"),
        (line_comment("#"), "comment"), (r"^\s*(?:rem\b|::).*$", "comment")], "multi": []},
}
LANG_NAMES = ["Plain"] + list(LANGS)
BOLD, ITALIC = ("heading", "attr"), ("comment",)


def detect_language(path):
    ext = os.path.splitext(path or "")[1].lower()
    for name, spec in LANGS.items():
        if ext in spec["ext"]:
            return name
    return "Plain"


class Colours(QSyntaxHighlighter):
    """Colours the words of code in the notepad's text (set_language picks the rules)."""

    def __init__(self, document):
        super().__init__(document)
        self.name = "Plain"
        self.rules, self.multi = [], []
        self.formats = {}
        for key, colour in SYNTAX.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colour))
            if key in BOLD:
                fmt.setFontWeight(QFont.Weight.DemiBold)
            if key in ITALIC:
                fmt.setFontItalic(True)
            self.formats[key] = fmt

    def set_language(self, name):
        spec = LANGS.get(name)
        flags = (re.I if spec and spec.get("nocase") else 0) | re.M
        self.name = name if spec else "Plain"
        self.rules = [(re.compile(p, flags), k) for p, k in spec["rules"]] if spec else []
        self.multi = [(re.compile(a, flags), re.compile(b, flags), k) for a, b, k in spec["multi"]] if spec else []
        self.rehighlight()

    def highlightBlock(self, text):
        self.setCurrentBlockState(0)
        if not self.rules and not self.multi:
            return
        start, before = 0, self.previousBlockState()
        if 1 <= before <= len(self.multi):                         # inside a /* ... */ from an earlier line
            _, ending, key = self.multi[before - 1]
            found = ending.search(text)
            if not found:
                self.setFormat(0, len(text), self.formats[key])
                self.setCurrentBlockState(before)
                return
            self.setFormat(0, found.end(), self.formats[key])
            start = found.end()
        for pattern, key in self.rules:
            for found in pattern.finditer(text, start):
                a, b = found.span(1) if pattern.groups else found.span()
                if a >= 0 and b > a:
                    self.setFormat(a, b - a, self.formats[key])
        position = start
        while position < len(text):                                # a new multi-line span starts here?
            nearest = None
            for number, (opening, ending, key) in enumerate(self.multi, 1):
                found = opening.search(text, position)
                if found and (nearest is None or found.start() < nearest[0].start()):
                    nearest = (found, number, ending, key)
            if nearest is None:
                break
            found, number, ending, key = nearest
            closed = ending.search(text, found.end())
            end = closed.end() if closed else len(text)
            self.setFormat(found.start(), end - found.start(), self.formats[key])
            if not closed:
                self.setCurrentBlockState(number)
                break
            position = end


# ---- the CSV table ----

def parse_csv(content):
    """(rows, cut) from CSV text; the separator (comma, semicolon, tab or bar) is guessed.
    cut is True when only the first MAX_ROWS rows are kept."""
    try:
        dialect = csv.Sniffer().sniff(content[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows, cut = [], False
    try:
        for row in csv.reader(io.StringIO(content), dialect):
            if len(rows) >= MAX_ROWS:
                cut = True
                break
            rows.append(row)
    except csv.Error:
        pass
    return rows, cut


class Grid(QAbstractTableModel):
    """The rows of a CSV file, for the table view."""

    def __init__(self):
        super().__init__()
        self.rows, self.heads, self.width = [], None, 0

    def load(self, rows, header):
        self.beginResetModel()
        self.heads = rows[0] if header and rows else None
        self.rows = rows[1:] if header and rows else rows
        self.width = max((len(r) for r in rows), default=0)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.width

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and index.isValid():
            row = self.rows[index.row()]
            return row[index.column()] if index.column() < len(row) else ""
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and self.heads and section < len(self.heads) and self.heads[section]:
            return self.heads[section]
        return str(section + 1)


def build(parent, say=None):
    say = say or (lambda text: None)
    state = {"path": None, "view": "text", "language": None, "header": True}

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
    chip = NameChip()
    chip_row = QHBoxLayout(chip)
    chip_row.setContentsMargins(12, 0, 12, 0)
    name_label = label("", 12, MUTED)
    name_label.setMinimumWidth(1)                    # the file name gives way first when the window is narrow
    chip.setMinimumWidth(1)
    chip_row.addWidget(name_label)

    text = QPlainTextEdit()
    text.setFont(mono_font(14))
    text.setStyleSheet("QPlainTextEdit { padding: 8px; }")
    text.setTabStopDistance(32)
    document = text.document()
    colours = Colours(document)

    preview = QTextBrowser()                         # the Markdown view
    preview.setOpenExternalLinks(True)
    preview.setFont(font(14))
    preview.setStyleSheet(f"QTextBrowser {{ background: {CODE_BG}; padding: 8px; }}")
    preview.document().setDefaultStyleSheet(
        f"a {{ color: {ACCENT}; }} h1, h2, h3 {{ color: {FG}; }} code, pre {{ color: {SYNTAX['string']}; }}")
    preview.hide()
    grid = Grid()                                    # the Table (CSV) view
    table = QTableView()
    table.setModel(grid)
    table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setDefaultSectionSize(24)
    table.horizontalHeader().setStretchLastSection(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.hide()
    splitter = QSplitter(Qt.Orientation.Horizontal)
    for part in (text, preview, table):
        splitter.addWidget(part)
    splitter.setChildrenCollapsible(False)
    column.addWidget(splitter, 1)
    refresh_later = QTimer(frame)                    # the preview and table catch up a moment after typing
    refresh_later.setSingleShot(True)
    refresh_later.setInterval(250)

    # ---- helpers ----

    def file_name():
        return os.path.basename(state["path"]) if state["path"] else "Untitled"

    def refresh(*args):
        modified = document.isModified()
        name_label.setText(file_name() + (DOT if modified else ""))
        set_color(name_label, FG if modified else MUTED)

    def clear():
        text.clear()
        document.clearUndoRedoStacks()
        document.setModified(False)

    def confirm_discard():
        """True if it is safe to replace the text: nothing unsaved, saved now, or discarded."""
        if not document.isModified():
            return True
        answer = QMessageBox.question(
            frame, "Notepad", f"Save changes to {file_name()}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        return save() if answer == QMessageBox.StandardButton.Yes else True

    # ---- views (text, Markdown, table) and code colours ----

    view_names = dict(VIEWS)

    def render_markdown():
        bar_ = preview.verticalScrollBar()
        keep = bar_.value()
        preview.setMarkdown(text.toPlainText())
        bar_.setValue(keep)

    def render_table():
        rows, cut = parse_csv(text.toPlainText())
        grid.load(rows, state["header"])
        if grid.rowCount() <= 500:
            table.resizeColumnsToContents()
        else:
            for number in range(grid.columnCount()):
                table.setColumnWidth(number, 140)
        for number in range(grid.columnCount()):
            table.setColumnWidth(number, max(50, min(table.columnWidth(number), 360)))
        state["table_note"] = f"{grid.rowCount()} rows × {grid.columnCount()} columns" + (f" (first {MAX_ROWS} only)" if cut else "")

    def update_views():
        mode = state["view"]
        if mode in ("markdown", "split"):
            render_markdown()
        elif mode == "table":
            render_table()

    def set_view(mode):
        """Show the text as it is, as a rendered Markdown page, both side by side, or as a table."""
        if mode not in view_names:
            return False
        state["view"] = mode
        text.setVisible(mode in ("text", "split"))
        preview.setVisible(mode in ("markdown", "split"))
        table.setVisible(mode == "table")
        if mode == "split":
            splitter.setSizes([500, 500])
        update_views()
        view_button.setText({"text": "Text", "markdown": "Markdown", "split": "Split", "table": "Table"}[mode] + " ▾")
        set_on(view_button, mode != "text")
        if mode == "table":
            say("Table: " + state["table_note"])
        return True

    def get_language():
        return state["language"] or detect_language(state["path"])

    def apply_language():
        name = get_language()
        if len(document.toPlainText()) > MAX_COLOURED:
            name = "Plain"
        if name != colours.name:
            colours.set_language(name)
        language_button.setText(name + " ▾")
        set_on(language_button, state["language"] is not None)      # tinted when you picked it yourself
        language_button.setToolTip("Colours for code: " + ("chosen by you" if state["language"] else "picked from the file's ending (Auto)"))

    def set_language(name):
        """A language by name, or 'Auto' to go by the file's ending. False if there is no such language."""
        if name == "Auto":
            state["language"] = None
        elif name in LANG_NAMES:
            state["language"] = name
        else:
            return False
        apply_language()
        return True

    def set_header(on):
        state["header"] = bool(on)
        if state["view"] == "table":
            render_table()

    def view_menu():
        items = [(view_names[mode], lambda m=mode: set_view(m), "check" if state["view"] == mode else None, False)
                 for mode in view_names]
        items.append(("First row is the header", lambda: set_header(not state["header"]),
                      "check" if state["header"] else None, False))
        popup_menu(view_button, items, width=280)

    def language_menu():
        items = [(name, lambda n=name: set_language(n), "check" if (state["language"] or "Auto") == name else None, False)
                 for name in ["Auto"] + LANG_NAMES]
        popup_menu(language_button, items, width=200)

    refresh_later.timeout.connect(update_views)
    text.textChanged.connect(lambda: refresh_later.start() if state["view"] != "text" else None)

    # ---- file actions ----

    def write(path, quiet=False):
        """Save to a file. quiet=True reports a problem in state["error"] instead of a dialog."""
        state["error"] = None
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text.toPlainText())
        except OSError as err:
            state["error"] = f"Could not save: {err}"
            if not quiet:
                QMessageBox.critical(frame, "Notepad", f"Could not save:\n{err}")
            return False
        state["path"] = path
        document.setModified(False)
        apply_language()
        refresh()
        say(f"Saved {path}")
        return True

    def save_as():
        path, _ = QFileDialog.getSaveFileName(
            frame, "Save as", file_name() if state["path"] else "", FILTER)
        if path and not os.path.splitext(path)[1]:
            path += ".txt"
        return write(path) if path else False

    def save():
        return write(state["path"]) if state["path"] else save_as()

    def open_file(path=None, quiet=False):
        """Open a file. quiet=True never asks or shows a dialog: a problem (or unsaved changes)
        is reported in state["error"] and False is returned."""
        state["error"] = None
        if quiet and document.isModified():
            state["error"] = "There are unsaved changes."
            return False
        if not quiet and not confirm_discard():
            return False
        if not path:
            path, _ = QFileDialog.getOpenFileName(frame, "Open", "", FILTER)
        if not path:
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError) as err:
            state["error"] = f"Could not open: {err}"
            if not quiet:
                QMessageBox.critical(frame, "Notepad", f"Could not open:\n{err}")
            return False
        clear()
        text.setPlainText(content)
        document.clearUndoRedoStacks()
        document.setModified(False)
        text.moveCursor(QTextCursor.MoveOperation.Start)
        state["path"] = path
        apply_language()
        update_views()
        refresh()
        say(f"Opened {path}")
        return True

    def new():
        if not confirm_discard():
            return False
        clear()
        state["path"] = None
        apply_language()
        update_views()
        refresh()
        say("New file")
        return True

    def can_close():
        return confirm_discard()

    # ---- toolbar, keys ----

    for caption, command, kind in (("New", new, "ghost"), ("Open", open_file, "ghost"),
                                   ("Save", save, "accent"), ("Save As", save_as, "ghost")):
        toolbar.addWidget(button(caption, command, kind=kind, width=68 if caption == "Save As" else 56))
    toolbar.addSpacing(6)
    view_button = button("Text ▾", view_menu, kind="toggle", width=80)
    view_button.setToolTip("How to show the text: as it is, rendered Markdown, or a CSV table")
    language_button = button("Plain ▾", language_menu, kind="toggle", width=96)
    toolbar.addWidget(view_button)
    toolbar.addWidget(language_button)
    toolbar.addStretch(1)
    toolbar.addWidget(chip)

    for keys, command in (("Ctrl+N", new), ("Ctrl+O", open_file), ("Ctrl+S", save),
                          ("Ctrl+Shift+S", save_as)):
        shortcut = QShortcut(QKeySequence(keys), frame)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)   # only this notepad
        shortcut.activated.connect(command)
    document.modificationChanged.connect(refresh)

    # ---- for the chatbot's tools ----

    def get_text():
        return text.toPlainText()

    def set_text(content, append=False):
        """Replace the text (or add to its end) as one undoable step."""
        cursor = text.textCursor()
        cursor.beginEditBlock()
        if append:
            cursor.movePosition(QTextCursor.MoveOperation.End)
        else:
            cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(content)
        cursor.endEditBlock()
        text.setTextCursor(cursor)
        text.ensureCursorVisible()
        refresh()

    def mark_clean():
        document.setModified(False)
        refresh()

    refresh()
    apply_language()
    text.setFocus()
    return {"frame": frame, "toolbar": bar, "text": text, "new": new, "preview": preview, "table": table,
            "grid": grid, "colours": colours, "set_view": set_view, "set_language": set_language,
            "get_view": lambda: state["view"], "get_language": get_language,
            "view_button": view_button, "language_button": language_button, "set_header": set_header,
            "table_note": lambda: state.get("table_note", ""), "open": open_file, "save": save,
            "save_as": save_as, "write": write, "can_close": can_close, "get_text": get_text,
            "set_text": set_text, "get_path": lambda: state["path"],
            "is_dirty": lambda: document.isModified(), "mark_clean": mark_clean,
            "last_error": lambda: state.get("error")}


# --------------------------------------------------------------------------
# Tools for the chatbot
# --------------------------------------------------------------------------

def tools(api):
    window_prop = {"window": {"type": "string", "description": "Notepad window id or title; may be left out when only one Notepad is open"}}

    def pick(args):
        return api["find_window"](args.get("window"), "notepad")

    def read(args):
        inst = pick(args)
        app = inst["app"]
        name = app["get_path"]() or "no file yet"
        flag = ", unsaved changes" if app["is_dirty"]() else ""
        return f"{inst['title']} ({name}{flag}):\n" + clip(app["get_text"](), 8000)

    def write_confirm(args):
        inst = pick(args)
        app = inst["app"]
        if text_arg(args, "mode", "replace") != "append" and app["is_dirty"]() and app["get_text"]().strip():
            return f"Replace the text in {inst['title']} (it has unsaved changes that would be lost)"
        return None

    def write(args):
        inst = pick(args)
        mode = text_arg(args, "mode", "replace").lower()
        if mode not in ("replace", "append"):
            raise ValueError("'mode' must be replace or append")
        content = text_arg(args, "text")
        inst["app"]["set_text"](content, append=(mode == "append"))
        return f"{'Added' if mode == 'append' else 'Wrote'} {len(content)} characters in {inst['title']} ({mode}). It now has {len(inst['app']['get_text']())} characters; not saved yet."

    def need_clean(inst, args):
        app = inst["app"]
        if app["is_dirty"]():
            if not bool_arg(args, "discard_changes"):
                raise ValueError(f"{inst['title']} has unsaved changes. Save them first, or call again with discard_changes=true.")
            app["mark_clean"]()

    def discard_confirm(args):
        inst = pick(args)
        if inst["app"]["is_dirty"]() and bool_arg(args, "discard_changes"):
            return f"Throw away the unsaved changes in {inst['title']}"
        return None

    def new(args):
        inst = pick(args)
        need_clean(inst, args)
        inst["app"]["new"]()
        return f"{inst['title']} is empty and has no file."

    def open_confirm(args):
        inst = pick(args)
        extra = " (its unsaved changes would be lost)" if inst["app"]["is_dirty"]() and bool_arg(args, "discard_changes") else ""
        return f"Open the file {text_arg(args, 'path')} in {inst['title']}{extra}"

    def open_file(args):
        inst = pick(args)
        path = text_arg(args, "path").strip().strip('"')
        need_clean(inst, args)
        if not inst["app"]["open"](path, quiet=True):
            raise ValueError(inst["app"]["last_error"]() or "could not open the file")
        return f"Opened {path} in {inst['title']} ({len(inst['app']['get_text']())} characters)."

    def save_confirm(args):
        inst = pick(args)
        path = inst["app"]["get_path"]()
        return f"Save {inst['title']} to {path}" if path else None

    def save(args):
        inst = pick(args)
        path = inst["app"]["get_path"]()
        if not path:
            raise ValueError(f"{inst['title']} has no file name yet. Use notepad_save_as with a path.")
        if not inst["app"]["write"](path, quiet=True):
            raise ValueError(inst["app"]["last_error"]() or "could not save")
        return f"Saved {inst['title']} to {path}."

    def save_as_confirm(args):
        inst = pick(args)
        path = text_arg(args, "path").strip().strip('"')
        import os
        again = " (this replaces the existing file)" if os.path.exists(path) else ""
        return f"Save {inst['title']} as {path}{again}"

    def save_as(args):
        inst = pick(args)
        path = text_arg(args, "path").strip().strip('"')
        if not inst["app"]["write"](path, quiet=True):
            raise ValueError(inst["app"]["last_error"]() or "could not save")
        return f"Saved {inst['title']} as {path}."

    def set_look(args):
        inst = pick(args)
        app = inst["app"]
        view, language = text_arg(args, "view").strip().lower(), text_arg(args, "language").strip()
        if not view and not language:
            raise ValueError("Give a 'view' (text, markdown, split, table) or a 'language'")
        if view and not app["set_view"](view):
            raise ValueError("'view' must be text, markdown, split or table")
        if language:
            match = next((n for n in ["Auto"] + LANG_NAMES if n.lower() == language.lower()), None)
            if match is None or not app["set_language"](match):
                raise ValueError("'language' must be one of: " + ", ".join(["Auto"] + LANG_NAMES))
        note = f" ({app['table_note']()})" if app["get_view"]() == "table" else ""
        return f"{inst['title']} shows the {app['get_view']()} view{note}, colours: {app['get_language']()}."

    return [
        tool("notepad_view", "Change how a Notepad window shows its text: view = text, markdown (rendered page), split (text and page side by side) or table (CSV); language = colours for code (Auto, Plain, Python, JSON, JavaScript, HTML, CSS, SQL, Markdown, Shell).",
             {**window_prop, "view": {"type": "string", "enum": ["text", "markdown", "split", "table"]},
              "language": {"type": "string"}}, run=set_look),
        tool("notepad_read", "Read the text in a Notepad window, with its file name and whether it has unsaved changes.", window_prop, run=read),
        tool("notepad_write", "Put text into a Notepad window: replace everything (default) or append to the end. Does not save the file.",
             {**window_prop, "text": {"type": "string"}, "mode": {"type": "string", "enum": ["replace", "append"]}},
             ["text"], write, write_confirm),
        tool("notepad_new", "Clear a Notepad window and forget its file. Refuses if there are unsaved changes unless discard_changes is true.",
             {**window_prop, "discard_changes": {"type": "boolean"}}, run=new, confirm=discard_confirm),
        tool("notepad_open", "Open a text file from disk in a Notepad window (asks Martin first). Refuses if there are unsaved changes unless discard_changes is true.",
             {**window_prop, "path": {"type": "string", "description": "full path of the file"}, "discard_changes": {"type": "boolean"}},
             ["path"], open_file, open_confirm),
        tool("notepad_save", "Save a Notepad window to its current file (asks Martin first). Use notepad_save_as if it has no file yet.",
             window_prop, run=save, confirm=save_confirm),
        tool("notepad_save_as", "Save a Notepad window to a new file path (asks Martin first).",
             {**window_prop, "path": {"type": "string", "description": "full path of the file to write"}}, ["path"], save_as, save_as_confirm),
    ]

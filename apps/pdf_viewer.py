"""PDF Viewer: read PDF files, with page jump, zoom and search.

    build(parent, say) -> dict

Builds the viewer inside `parent` (an app window's body). Scroll to read, type a page number to jump,
type words in the search box and press Enter for the next match (F3 / Shift+F3 go on / back).
Password-protected files ask for the password.
Keys: Ctrl+O open, Ctrl+F search, + - zoom, 0 fit width.

The returned dict holds: frame, toolbar, view, document, open, goto, find, page_text, describe,
get_path, get_state, set_state, shutdown, can_close, last_error.
"""
import os

from PyQt6.QtCore import QMargins, QModelIndex, QPointF, Qt
from PyQt6.QtGui import QColor, QKeySequence, QPalette, QShortcut
from PyQt6.QtPdf import QPdfDocument, QPdfSearchModel
from PyQt6.QtPdfWidgets import QPdfView
from PyQt6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLineEdit, QVBoxLayout, QWidget

from chat.tools import int_arg, text_arg, tool, clip
from theme import ACCENT, CODE_BG, FG, MUTED, RAISED, NameChip, button, icon_button, label, set_on

START = r"D:\raid-disk" if os.path.isdir(r"D:\raid-disk") else os.path.expanduser("~")      # your home folder on other PCs
FILTER = "PDF files (*.pdf);;All files (*.*)"
NO_ERROR = QPdfDocument.Error.None_


def build(parent, say=None):
    say = say or (lambda text: None)
    state = {"path": None, "error": None, "search": "", "want_first": False, "hit": -1}

    frame = QWidget(parent)
    frame.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
    column = QVBoxLayout(frame)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(4)

    bar = QWidget()
    bar.setFixedHeight(28)
    toolbar = QHBoxLayout(bar)
    toolbar.setContentsMargins(0, 0, 0, 0)
    toolbar.setSpacing(2)
    column.addWidget(bar)

    document = QPdfDocument(frame)
    view = QPdfView(frame)
    view.setDocument(document)
    view.setPageMode(QPdfView.PageMode.MultiPage)
    view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
    view.setPageSpacing(10)
    view.setDocumentMargins(QMargins(10, 10, 10, 10))
    view.setStyleSheet("QPdfView { border: none; }")
    colours = view.palette()
    colours.setColor(QPalette.ColorRole.Dark, QColor(CODE_BG))      # QPdfView paints its backdrop with this
    view.setPalette(colours)
    column.addWidget(view, 1)
    finder = QPdfSearchModel(frame)
    finder.setDocument(document)
    view.setSearchModel(finder)
    navigator = view.pageNavigator()

    def hits():
        return finder.rowCount(QModelIndex())

    # ---- toolbar ----

    def field(width, placeholder=""):
        box = QLineEdit()
        box.setFixedSize(width, 28)
        box.setPlaceholderText(placeholder)
        box.setStyleSheet(
            f"QLineEdit {{ background: {RAISED}; border: 1px solid transparent; border-radius: 14px; padding: 0 10px; }}"
            f"QLineEdit:focus {{ border: 1px solid {ACCENT}; }}")
        return box

    open_button = button("Open", lambda: open_file(), kind="ghost", width=58)
    previous_button = icon_button("up", lambda: goto(current_page() - 1), size=28, strong=True)
    previous_button.setToolTip("Previous page")
    next_button = icon_button("chevron_down", lambda: goto(current_page() + 1), size=28, strong=True)
    next_button.setToolTip("Next page")
    page_box = field(44)
    page_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
    page_box.setToolTip("Type a page number and press Enter")
    pages_label = label("/ –", 12, MUTED)
    out_button = icon_button("zoom_out", lambda: zoom_by(1 / 1.25), size=28)
    out_button.setToolTip("Zoom out (-)")
    in_button = icon_button("zoom_in", lambda: zoom_by(1.25), size=28)
    in_button.setToolTip("Zoom in (+)")
    fit_button = button("Fit", lambda: fit_width(), kind="toggle", width=40)
    fit_button.setToolTip("Fit the page width to the window (0)")
    search_box = field(120, "Find in PDF")
    search_box.setToolTip("Enter or F3 = next match, Shift+F3 = previous")
    count_label = label("", 12, MUTED)
    chip = NameChip()
    chip.setMaximumWidth(240)
    chip_row = QHBoxLayout(chip)
    chip_row.setContentsMargins(12, 0, 12, 0)
    name_label = label("Nothing open", 12, MUTED)
    name_label.setMinimumWidth(1)                    # the file name gives way first when the window is narrow
    chip.setMinimumWidth(1)
    chip_row.addWidget(name_label)
    for widget in (open_button, previous_button, page_box, pages_label, next_button, out_button, in_button,
                   fit_button):
        toolbar.addWidget(widget)
    toolbar.addSpacing(4)
    toolbar.addWidget(search_box)
    toolbar.addWidget(count_label)
    toolbar.addStretch(1)
    toolbar.addWidget(chip)

    # ---- where we are ----

    def total():
        return document.pageCount() if state["path"] else 0

    def current_page():
        return navigator.currentPage()

    def refresh(*args):
        pages = total()
        page_box.setText(str(current_page() + 1) if pages else "")
        pages_label.setText(f"/ {pages}" if pages else "/ –")
        name_label.setText(os.path.basename(state["path"]) if state["path"] else "Nothing open")
        name_label.setToolTip(state["path"] or "")
        set_on(fit_button, view.zoomMode() == QPdfView.ZoomMode.FitToWidth)
        for widget in (previous_button, next_button):
            widget.setEnabled(pages > 1)

    def goto(page):
        """Show page number `page` (counted from 0). False if there is no such page."""
        if not 0 <= page < total():
            return False
        navigator.jump(page, QPointF(0, 0))
        refresh()
        return True

    navigator.currentPageChanged.connect(lambda page: refresh())
    page_box.returnPressed.connect(lambda: (goto(int(page_box.text()) - 1) if page_box.text().strip().isdigit() else None,
                                            refresh(), view.setFocus()))

    # ---- opening ----

    def fail(text):
        state["error"] = text
        say(text)
        return False

    def open_file(path=None, quiet=False, page=0):
        """Open a PDF. quiet=True never shows a dialog (the chatbot's tool): a problem, a missing
        file or a password is reported in last_error() and False is returned."""
        state["error"] = None
        if not path:
            if quiet:
                return fail("No file given")
            path, _ = QFileDialog.getOpenFileName(frame, "Open", os.path.dirname(state["path"]) if state["path"] else START, FILTER)
            if not path:
                return False
        path = os.path.normpath(str(path).strip().strip('"'))
        if not os.path.isfile(path):
            return fail(f"There is no such file: {path}")
        document.close()
        state["path"] = None
        error = document.load(path)
        tries = 0
        while error == QPdfDocument.Error.IncorrectPassword and not quiet and tries < 3:
            tries += 1
            word, ok = QInputDialog.getText(frame, "PDF Viewer", f"{os.path.basename(path)} needs a password:",
                                            QLineEdit.EchoMode.Password)
            if not ok:
                return False
            document.setPassword(word)
            error = document.load(path)
        if error != NO_ERROR:
            document.close()
            reason = {QPdfDocument.Error.IncorrectPassword: "it needs a password",
                      QPdfDocument.Error.InvalidFileFormat: "it is not a valid PDF"}.get(error, str(error).split(".")[-1])
            refresh()
            return fail(f"Could not open {os.path.basename(path)}: {reason}")
        state["path"] = path
        state["search"] = ""
        finder.setSearchString("")
        count_label.setText("")
        search_box.clear()
        if page:
            goto(page)
        refresh()
        say(f"Opened {os.path.basename(path)} ({document.pageCount()} pages)")
        return True

    # ---- zoom ----

    def fit_width():
        view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        refresh()

    def zoom_by(ratio):
        if not state["path"]:
            return
        now = view.zoomFactor()
        view.setZoomMode(QPdfView.ZoomMode.Custom)
        view.setZoomFactor(min(8.0, max(0.1, now * ratio)))
        refresh()

    # ---- search ----

    def show_hit(index):
        link = finder.resultAtIndex(index)
        state["hit"] = index
        view.setCurrentSearchResultIndex(index)
        navigator.jump(link.page(), link.location())
        count_label.setText(f"{index + 1} / {hits()}")
        refresh()

    def on_results(*args):
        count = hits()
        if state["want_first"] and count > 0:
            state["want_first"] = False
            show_hit(0)
        elif state["search"]:
            count_label.setText(f"{state['hit'] + 1} / {count}" if state["hit"] >= 0 else f"{count} found")

    finder.rowsInserted.connect(on_results)

    def find(words, backwards=False):
        """Search the PDF. A new search jumps to the first match; the same words again go on to the next
        (or the one before). Returns the number of matches found so far (the search runs in the
        background, so a long file may still be adding to it)."""
        words = words.strip()
        if not state["path"] or not words:
            return 0
        if words != state["search"]:
            state["search"], state["hit"], state["want_first"] = words, -1, True
            count_label.setText("…")
            finder.setSearchString(words)
            return hits()
        count = hits()
        if count:
            show_hit((state["hit"] + (-1 if backwards else 1)) % count)
        return count

    search_box.returnPressed.connect(lambda: find(search_box.text()))
    search_box.textChanged.connect(lambda t: count_label.setText("") if not t.strip() else None)

    # ---- keys ----

    for keys, command in (("Ctrl+O", open_file), ("Ctrl+F", lambda: (search_box.setFocus(), search_box.selectAll())),
                          ("F3", lambda: find(search_box.text())),
                          ("Shift+F3", lambda: find(search_box.text(), backwards=True)),
                          ("+", lambda: zoom_by(1.25)), ("=", lambda: zoom_by(1.25)),
                          ("-", lambda: zoom_by(1 / 1.25)), ("0", fit_width)):
        shortcut = QShortcut(QKeySequence(keys), frame)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)   # only this viewer
        shortcut.activated.connect(command)

    # ---- for the chatbot's tools ----

    def page_text(page):
        """The text on page number `page` (from 0); '' for a scanned page that has no text."""
        return document.getAllText(page).text().strip() if 0 <= page < total() else ""

    def describe():
        if not state["path"]:
            return "Nothing is open."
        title = document.metaData(QPdfDocument.MetaDataField.Title)
        zoom = "fit to width" if view.zoomMode() == QPdfView.ZoomMode.FitToWidth else f"{round(view.zoomFactor() * 100)}%"
        text = f"{os.path.basename(state['path'])}, {total()} pages, showing page {current_page() + 1}, zoom {zoom}"
        if title:
            text += f", title '{title}'"
        if state["search"]:
            text += f", searched '{state['search']}' ({hits()} matches)"
        return text

    # ---- what main.json keeps ----

    def get_state():
        return {"path": state["path"], "page": current_page() if state["path"] else 0}

    def set_state(data):
        path = data.get("path")
        if isinstance(path, str) and os.path.isfile(path):
            page = data.get("page")
            open_file(path, quiet=True, page=page if isinstance(page, int) else 0)

    def shutdown():
        document.close()

    refresh()
    return {"frame": frame, "toolbar": bar, "view": view, "document": document, "finder": finder, "hits": hits,
            "open": open_file, "goto": goto, "find": find, "page_text": page_text, "describe": describe,
            "get_path": lambda: state["path"], "get_state": get_state, "set_state": set_state,
            "shutdown": shutdown, "can_close": lambda: True, "last_error": lambda: state["error"],
            "current_page": current_page, "page_count": total, "zoom_by": zoom_by, "fit_width": fit_width,
            "search_box": search_box, "page_box": page_box, "count_label": count_label,
            "pages_label": pages_label}


# --------------------------------------------------------------------------
# Tools for the chatbot
# --------------------------------------------------------------------------

def tools(api):
    window_prop = {"window": {"type": "string", "description": "PDF Viewer window id or title; may be left out when only one is open"}}

    def pick(args):
        return api["find_window"](args.get("window"), "pdf_viewer")

    def open_confirm(args):
        return f"Open {os.path.normpath(text_arg(args, 'path').strip())} in the PDF Viewer"

    def open_it(args):
        inst = pick(args)
        path = text_arg(args, "path").strip().strip('"')
        if not inst["app"]["open"](path, quiet=True):
            raise ValueError(inst["app"]["last_error"]() or "could not open it")
        return f"{inst['title']}: {inst['app']['describe']()}"

    def info(args):
        inst = pick(args)
        return f"{inst['title']}: {inst['app']['describe']()}"

    def go(args):
        inst = pick(args)
        app = inst["app"]
        page = int_arg(args, "page", 1, 1, 100000)
        if not app["goto"](page - 1):
            raise ValueError(f"There is no page {page}; the file has {app['page_count']()} pages")
        return f"{inst['title']} now shows page {page} of {app['page_count']()}."

    def read(args):
        inst = pick(args)
        app = inst["app"]
        if not app["get_path"]():
            raise ValueError("Nothing is open in this PDF Viewer")
        first = int_arg(args, "page", app["current_page"]() + 1, 1, 100000)
        count = int_arg(args, "pages", 1, 1, 5)
        out = []
        for number in range(first, min(first + count, app["page_count"]() + 1)):
            out.append(f"--- page {number} ---\n" + (app["page_text"](number - 1) or "(no text on this page; it may be a scan)"))
        if not out:
            raise ValueError(f"There is no page {first}; the file has {app['page_count']()} pages")
        return clip("\n".join(out), 8000)

    def search(args):
        inst = pick(args)
        words = text_arg(args, "text").strip()
        if not words:
            raise ValueError("Give the words to look for as 'text'")
        count = inst["app"]["find"](words)
        return f"{inst['title']}: searching '{words}' (matches so far: {count}; the view jumps to the first one)."

    return [
        tool("pdf_open", "Open a PDF file in a PDF Viewer window (asks Martin first).",
             {**window_prop, "path": {"type": "string", "description": "full path of the PDF"}}, ["path"], open_it, open_confirm),
        tool("pdf_info", "Tell what a PDF Viewer window shows: file, number of pages, current page, zoom.", window_prop, run=info),
        tool("pdf_goto", "Show a page of the PDF in a PDF Viewer window (pages count from 1).",
             {**window_prop, "page": {"type": "integer"}}, ["page"], go),
        tool("pdf_read", "Read the text of one or more pages of the open PDF (default: the current page).",
             {**window_prop, "page": {"type": "integer", "description": "first page, counting from 1"},
              "pages": {"type": "integer", "description": "how many pages (1-5, default 1)"}}, run=read),
        tool("pdf_find", "Search the open PDF for some words and jump to the first match.",
             {**window_prop, "text": {"type": "string"}}, ["text"], search),
    ]

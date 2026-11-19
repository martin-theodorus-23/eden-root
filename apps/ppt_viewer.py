"""PPT Viewer: look through a PowerPoint presentation (.pptx) slide by slide.

    build(parent, say) -> dict

Builds the viewer inside `parent` (an app window's body). `say(text)` shows a short message
(the status bar). Open a file with the Open button; a strip of thumbnails sits on the left,
the slide is drawn to fit the window, and speaker notes can be shown below it. Moving to
another slide pushes the old one out and the new one in.

Slides are painted by the renderer of the existing PPT Viewer project (python-pptx for reading
the file, Qt for painting). No PowerPoint needed.
Not drawn: SmartArt, embedded media, shape effects, animations and transitions.

Keys (click the slide first): Left/Up/PageUp previous, Right/Down/PageDown/Space next,
Home first, End last.
The returned dict holds: frame, state, open, goto, next, prev, toggle_notes, toggle_thumbs,
slide, can_close.
"""
import importlib.util
import json
import os
import sys

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QListWidget, QListWidgetItem,
                             QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget)

import theme
from chat.tools import bool_arg, clip, int_arg, text_arg, tool
from theme import (ACCENT, EDITOR, FG, MUTED, RAISED, SURFACE, button, ease_in_out, font,
                   icon_button, label, set_color, set_on, tween)

# The renderer is a separate project that is not part of this repository; point EDEN_PPT_RENDERER at its renderer.py
ENGINE_PATH = os.environ.get("EDEN_PPT_RENDERER") or r"D:\raid-disk\martin\projects\ppt_viewer\renderer.py"
FILTER = "PowerPoint (*.pptx *.pptm *.ppsx *.potx);;All files (*.*)"

THUMB_WIDTH = 150
SLIDE_RADIUS = 10
SLIDE_GAP = 28                            # space between two slides while one pushes the other out

engine = {"module": None}                 # loaded once, shared by every viewer


def load_engine():
    """Load the renderer of the ppt_viewer project."""
    if engine["module"] is None:
        if not os.path.exists(ENGINE_PATH):
            raise FileNotFoundError("the PowerPoint renderer is not installed on this PC (set EDEN_PPT_RENDERER to its renderer.py)")
        spec = importlib.util.spec_from_file_location("ppt_renderer", ENGINE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules["ppt_renderer"] = module        # dataclasses in it look itself up here
        try:
            spec.loader.exec_module(module)
        except Exception:
            del sys.modules["ppt_renderer"]
            raise
        engine["module"] = module
    return engine["module"]


def logical_size(pixmap):
    ratio = pixmap.devicePixelRatio() or 1.0
    return pixmap.width() / ratio, pixmap.height() / ratio


class Stage(QWidget):
    """Shows the slide, centred. While `push` is set, two slides are drawn sliding sideways."""

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(120, 90)
        self.current, self.push, self.message = None, None, "Open a presentation (.pptx)"
        self.on_key = self.on_resize = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        card = QPainterPath()
        card.addRoundedRect(QRectF(self.rect()), 10, 10)
        p.fillPath(card, QColor(EDITOR))
        p.setClipPath(card)
        if self.push:
            motion = self.push
            sw, sh = logical_size(motion["new"])
            travel = sw + SLIDE_GAP
            x, y = (self.width() - sw) / 2, (self.height() - sh) / 2
            for pix, dx in ((motion["old"], -motion["direction"] * travel * motion["t"]),
                            (motion["new"], motion["direction"] * travel * (1 - motion["t"]))):
                self.draw_slide(p, pix, x + dx, y)
        elif self.current is not None:
            sw, sh = logical_size(self.current)
            self.draw_slide(p, self.current, (self.width() - sw) / 2, (self.height() - sh) / 2)
        else:
            p.setPen(QColor(MUTED))
            p.setFont(font(12))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)

    @staticmethod
    def draw_slide(p, pixmap, x, y):
        sw, sh = logical_size(pixmap)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(x, y, sw, sh), SLIDE_RADIUS, SLIDE_RADIUS)
        p.save()
        p.setClipPath(shape, Qt.ClipOperation.IntersectClip)
        p.drawPixmap(QRectF(x, y, sw, sh), pixmap, QRectF(pixmap.rect()))
        p.restore()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.on_resize:
            self.on_resize()

    def keyPressEvent(self, event):
        if not (self.on_key and self.on_key(event)):
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()


def rounded(pixmap, radius):
    out = QPixmap(pixmap.size())
    out.setDevicePixelRatio(pixmap.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    w, h = logical_size(pixmap)
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(shape)
    p.drawPixmap(QRectF(0, 0, w, h), pixmap, QRectF(pixmap.rect()))
    p.end()
    return out


def build(parent, say=None):
    say = say or (lambda text: None)
    state = {"deck": None, "path": None, "index": 0, "shown": None, "slide_motion": None,
             "thumbs": [], "thumb_gen": 0, "notes_on": False, "thumbs_on": True}

    frame = QWidget(parent)
    column = QVBoxLayout(frame)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(4)

    bar = QWidget()
    bar.setFixedHeight(28)
    toolbar = QHBoxLayout(bar)
    toolbar.setContentsMargins(0, 0, 0, 0)
    toolbar.setSpacing(0)
    column.addWidget(bar)
    chip = QFrame()
    chip.setObjectName("chip")
    chip.setFixedHeight(28)
    chip_row = QHBoxLayout(chip)
    chip_row.setContentsMargins(12, 0, 12, 0)
    name_label = label("No file", 12, MUTED)
    chip_row.addWidget(name_label)

    middle = QHBoxLayout()
    middle.setContentsMargins(0, 0, 0, 0)
    middle.setSpacing(6)
    column.addLayout(middle, 1)

    thumbs_list = QListWidget()
    thumbs_list.setFixedWidth(THUMB_WIDTH + 34)
    thumbs_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
    thumbs_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    thumbs_list.setIconSize(QSize(THUMB_WIDTH, 84))
    thumbs_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    thumbs_list.setStyleSheet(
        f"QListWidget {{ background: {SURFACE}; border: none; outline: 0; }}"
        f"QListWidget::item {{ border: 2px solid transparent; border-radius: 8px; padding: 1px; margin: 2px 0; }}"
        f"QListWidget::item:selected {{ border: 2px solid {ACCENT}; background: transparent; }}"
        f"QListWidget::item:hover {{ background: transparent; }}")
    middle.addWidget(thumbs_list)

    right = QVBoxLayout()
    right.setContentsMargins(0, 0, 0, 0)
    right.setSpacing(6)
    middle.addLayout(right, 1)
    stage = Stage()
    right.addWidget(stage, 1)
    notes_box = QPlainTextEdit()
    notes_box.setReadOnly(True)
    notes_box.setFont(font(13))
    notes_box.setFixedHeight(96)
    notes_box.hide()
    right.addWidget(notes_box)

    # ---- showing a slide ----

    def count():
        return state["deck"].slide_count if state["deck"] else 0

    def push(old, new, direction):
        """Slide `old` out and `new` in. direction +1 moves left (next slide), -1 moves right."""
        stage.push = {"old": old, "new": new, "direction": direction, "t": 0.0}

        def step(t):
            if stage.push:
                stage.push["t"] = t
                stage.update()

        def done():
            stage.push, stage.current, state["shown"] = None, new, new
            stage.update()

        state["slide_motion"] = tween(step, ms=300, done=done, ease=ease_in_out)

    def draw(direction=0):
        """Draw the current slide as large as fits in the stage. With a direction (+1 / -1)
        the slide pushes the previous one out instead of just appearing."""
        deck = state["deck"]
        if deck is None:
            return
        if state["slide_motion"]:
            state["slide_motion"]["finish"]()           # settle a push that is still moving
        w, h = stage.width(), stage.height()
        if w < 40 or h < 40:
            return
        ratio = stage.devicePixelRatioF()
        width = max(60, min(w - 8, int((h - 8) * deck.aspect)))
        try:
            new = QPixmap.fromImage(deck.render(state["index"], int(width * ratio)))
            new.setDevicePixelRatio(ratio)
        except Exception as err:
            stage.current, stage.message = None, f"Could not draw this slide:\n{err}"
            stage.update()
            return
        old = state["shown"]
        if direction and old is not None and old.size() == new.size() and theme.MOTION["on"]:
            push(old, new, direction)
        else:
            state["shown"], stage.current = new, new
            stage.update()

    fit_timer = QTimer(frame)
    fit_timer.setSingleShot(True)
    fit_timer.setInterval(120)
    fit_timer.timeout.connect(draw)

    def fit_later():
        """Redraw once the window has stopped changing size."""
        fit_timer.start()

    def show_notes():
        text = state["deck"].notes(state["index"]) if state["deck"] else ""
        notes_box.setPlainText(text.strip() or "(no notes)")

    def mark_thumb():
        thumbs_list.blockSignals(True)
        thumbs_list.setCurrentRow(state["index"] if state["thumbs"] else -1)
        thumbs_list.blockSignals(False)
        if state["thumbs"]:
            thumbs_list.scrollToItem(thumbs_list.item(state["index"]))

    def goto(index):
        total = count()
        if not total:
            return False
        before = state["index"]
        state["index"] = max(0, min(total - 1, index))
        counter.setText(f"{state['index'] + 1} / {total}")
        mark_thumb()
        show_notes()
        step = state["index"] - before
        draw(direction=(1 if step > 0 else -1) if step else 0)
        return True

    def next_slide():
        goto(state["index"] + 1)

    def prev_slide():
        goto(state["index"] - 1)

    # ---- thumbnails: made one at a time, so a big deck does not freeze the window ----

    def clear_thumbs():
        state["thumb_gen"] += 1
        thumbs_list.blockSignals(True)
        thumbs_list.clear()
        thumbs_list.blockSignals(False)
        state["thumbs"] = []

    def make_thumbs():
        clear_thumbs()
        deck = state["deck"]
        height = max(1, int(THUMB_WIDTH / deck.aspect))
        thumbs_list.setIconSize(QSize(THUMB_WIDTH, height))
        blank = QPixmap(THUMB_WIDTH, height)
        blank.fill(QColor(RAISED))
        for n in range(deck.slide_count):
            item = QListWidgetItem(QIcon(blank), "")
            item.setSizeHint(QSize(THUMB_WIDTH + 12, height + 10))
            thumbs_list.addItem(item)
            state["thumbs"].append(item)
        fill_thumb(state["thumb_gen"], 0)

    def fill_thumb(generation, n):
        deck = state["deck"]
        if generation != state["thumb_gen"] or deck is None or n >= len(state["thumbs"]):
            return
        try:
            image = QPixmap.fromImage(deck.render(n, THUMB_WIDTH * 2, cache=False))
            image = image.scaled(THUMB_WIDTH, max(1, int(THUMB_WIDTH / deck.aspect)),
                                 Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            state["thumbs"][n].setIcon(QIcon(rounded(image, 6)))
        except Exception:
            pass                                       # a slide that cannot be drawn keeps its blank
        QTimer.singleShot(15, lambda: fill_thumb(generation, n + 1))

    thumbs_list.currentRowChanged.connect(lambda row: goto(row) if row >= 0 else None)

    # ---- opening a file ----

    def open_file(path=None, quiet=False):
        """Open a presentation. quiet=True reports a problem in state["error"] instead of a dialog."""
        state["error"] = None
        if not path:
            path, _ = QFileDialog.getOpenFileName(frame, "Open a presentation", "", FILTER)
        if not path:
            return False
        say("Opening " + os.path.basename(path) + "...")
        try:
            deck = load_engine().Deck(path)
        except Exception as err:
            state["error"] = f"Could not open: {err}"
            if not quiet:
                QMessageBox.critical(frame, "PPT Viewer", f"Could not open:\n{err}")
            say("Could not open " + os.path.basename(path))
            return False
        if deck.slide_count == 0:
            state["error"] = "This presentation has no slides."
            if not quiet:
                QMessageBox.critical(frame, "PPT Viewer", "This presentation has no slides.")
            return False
        state["deck"], state["path"] = deck, path
        state["shown"], stage.current = None, None      # a new deck appears, it does not push
        state["index"] = 0
        name_label.setText(os.path.basename(path))
        set_color(name_label, FG)
        make_thumbs()
        goto(0)
        say(f"Opened {os.path.basename(path)} - {deck.slide_count} slides")
        return True

    # ---- toolbar, panels, keys ----

    def toggle_thumbs():
        state["thumbs_on"] = not state["thumbs_on"]
        thumbs_list.setVisible(state["thumbs_on"])
        set_on(thumbs_button, state["thumbs_on"])
        fit_later()

    def toggle_notes():
        state["notes_on"] = not state["notes_on"]
        notes_box.setVisible(state["notes_on"])
        set_on(notes_button, state["notes_on"])
        fit_later()

    toolbar.addWidget(button("Open", open_file, kind="accent", width=62))
    toolbar.addSpacing(6)
    toolbar.addWidget(icon_button("left", prev_slide, size=28, strong=True))
    counter = label("0 / 0", 13, FG)
    counter.setFixedWidth(64)
    counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
    toolbar.addWidget(counter)
    toolbar.addWidget(icon_button("right", next_slide, size=28, strong=True))
    toolbar.addSpacing(8)
    thumbs_button = button("Thumbnails", toggle_thumbs, kind="toggle", width=96)
    toolbar.addWidget(thumbs_button)
    toolbar.addSpacing(4)
    notes_button = button("Notes", toggle_notes, kind="toggle", width=62)
    toolbar.addWidget(notes_button)
    toolbar.addStretch(1)
    toolbar.addWidget(chip)
    set_on(thumbs_button, True)

    def on_key(event):
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            prev_slide()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            next_slide()
        elif key == Qt.Key.Key_Home:
            goto(0)
        elif key == Qt.Key.Key_End:
            goto(count() - 1)
        else:
            return False
        return True

    stage.on_key, stage.on_resize = on_key, fit_later
    return {"frame": frame, "toolbar": bar, "state": state, "open": open_file, "goto": goto, "next": next_slide,
            "prev": prev_slide, "toggle_notes": toggle_notes, "toggle_thumbs": toggle_thumbs,
            "slide": stage, "can_close": lambda: True, "last_error": lambda: state.get("error")}


# --------------------------------------------------------------------------
# Tools for the chatbot
# --------------------------------------------------------------------------

def shape_text(shape, out):
    """The text of a shape, a group of shapes or a table, one entry per paragraph or row."""
    try:
        if getattr(shape, "shapes", None) is not None and shape.shape_type == 6:      # a group
            for inner in shape.shapes:
                shape_text(inner, out)
            return
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            text = shape.text_frame.text.strip()
            if text:
                out.append(text)
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                out.append(" | ".join(cell.text.strip() for cell in row.cells))
    except Exception:
        pass


def slide_text(deck, index):
    """All the text on one slide (0-based index) as a list of strings."""
    out = []
    for shape in deck.prs.slides[index].shapes:
        shape_text(shape, out)
    return out


def tools(api):
    window_prop = {"window": {"type": "string", "description": "PPT Viewer window id or title; may be left out when only one is open"}}

    def pick(args):
        return api["find_window"](args.get("window"), "ppt_viewer")

    def deck_of(args):
        inst = pick(args)
        deck = inst["app"]["state"]["deck"]
        if deck is None:
            raise ValueError(f"{inst['title']} has no presentation open. Use ppt_open with a file path.")
        return inst, inst["app"]["state"], deck

    def open_confirm(args):
        return f"Open the presentation {text_arg(args, 'path').strip().strip(chr(34))} in {pick(args)['title']}"

    def open_file(args):
        inst = pick(args)
        path = text_arg(args, "path").strip().strip('"')
        if not inst["app"]["open"](path, quiet=True):
            raise ValueError(inst["app"]["last_error"]() or "could not open the file")
        deck = inst["app"]["state"]["deck"]
        return f"Opened {os.path.basename(path)} in {inst['title']}: {deck.slide_count} slides. Showing slide 1: {deck.title(0) or '(no title)'}."

    def info(args):
        inst, state, deck = deck_of(args)
        i = state["index"]
        return json.dumps({"window": inst["title"], "file": state["path"], "slides": deck.slide_count,
                           "shown_slide": i + 1, "shown_slide_title": deck.title(i),
                           "notes_panel": state["notes_on"], "thumbnails_panel": state["thumbs_on"]}, ensure_ascii=False)

    def goto(args):
        inst, state, deck = deck_of(args)
        number = int_arg(args, "slide", None, 1, deck.slide_count)
        inst["app"]["goto"](number - 1)
        return f"Showing slide {number} of {deck.slide_count}: {deck.title(number - 1) or '(no title)'}"

    def step(args):
        inst, state, deck = deck_of(args)
        change = int_arg(args, "change", None)       # a big number just means "to the end"
        if not change:
            raise ValueError("'change' must be a non-zero whole number (1 = next slide, -1 = previous)")
        target = max(0, min(deck.slide_count - 1, state["index"] + change))
        inst["app"]["goto"](target)
        return f"Showing slide {target + 1} of {deck.slide_count}: {deck.title(target) or '(no title)'}"

    def read_slide(args):
        inst, state, deck = deck_of(args)
        number = int_arg(args, "slide", state["index"] + 1, 1, deck.slide_count)
        lines = slide_text(deck, number - 1)
        notes = deck.notes(number - 1).strip()
        return clip(f"Slide {number} of {deck.slide_count}\nTitle: {deck.title(number - 1) or '(none)'}\n"
                    f"Text:\n" + ("\n".join(lines) if lines else "(no text)") +
                    f"\nSpeaker notes:\n{notes or '(none)'}", 7000)

    def panel(args):
        inst, state, deck = deck_of(args)
        which = text_arg(args, "panel").strip().lower()
        if which not in ("notes", "thumbnails"):
            raise ValueError("'panel' must be notes or thumbnails")
        key, toggle = ("notes_on", "toggle_notes") if which == "notes" else ("thumbs_on", "toggle_thumbs")
        want = bool_arg(args, "show", True)
        if state[key] != want:
            inst["app"][toggle]()
        return f"The {which} panel is now {'shown' if state[key] else 'hidden'}."

    return [
        tool("ppt_open", "Open a PowerPoint file (.pptx) in a PPT Viewer window (asks Martin first).",
             {**window_prop, "path": {"type": "string", "description": "full path of the presentation"}}, ["path"], open_file, open_confirm),
        tool("ppt_info", "Tell which presentation a PPT Viewer shows: file, number of slides, the slide on screen and its title.", window_prop, run=info),
        tool("ppt_goto", "Show a slide by its number (1 = first).", {**window_prop, "slide": {"type": "integer"}}, ["slide"], goto),
        tool("ppt_step", "Move forward or back some slides from the one shown (1 = next, -1 = previous).", {**window_prop, "change": {"type": "integer"}}, ["change"], step),
        tool("ppt_read_slide", "Read a slide: its title, all its text and its speaker notes. Without a slide number it reads the one on screen.",
             {**window_prop, "slide": {"type": "integer", "description": "slide number (1 = first); default is the slide on screen"}}, run=read_slide),
        tool("ppt_panel", "Show or hide the speaker-notes panel or the thumbnail strip of a PPT Viewer.",
             {**window_prop, "panel": {"type": "string", "enum": ["notes", "thumbnails"]}, "show": {"type": "boolean", "description": "true to show (default), false to hide"}},
             ["panel"], panel),
    ]

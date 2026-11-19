"""The floating chatbot: a round button hovering in the bottom-right corner of the window, which
opens a chat panel that floats above it. Both are small borderless windows of their own (with
see-through corners), so the panel can be dragged anywhere, even outside the main window.
Chat with a model from Ollama (pick one with the chip in the panel's title bar).

    build(root, say, db_path, tools) -> dict

`root` is the main window. Replies come from chat/core.py. The red ring around the button
means a reply arrived while the panel was closed.
"""
import html
import queue
import threading
import time

from PyQt6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from chat import core
from chat import tools as chat_tools
from theme import (ACCENT, ACCENT_DIM, ACCENT_HOVER, BORDER, DANGER, DANGER_BG, FG, HOVER, ICONS,
                   MONO_FAMILY, MUTED, ON_ACCENT, RAISED, SURFACE, button, ease_in, font,
                   icon_button, icon_font, label, popup_menu, set_color, title_font, tween)

W, H = 410, 580              # the panel
MARGIN = 24                  # from the main window's edges
FAB = 54                     # the round button
BUBBLE_PX = 318              # widest a message bubble gets
PAD_X, PAD_Y = 12, 9         # space between a bubble's edge and its text
FLOATING = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint


def model_label(name):
    """A short name for the title bar: 'gemma4:31b-cloud' -> 'gemma4:31b · cloud'."""
    if not name:
        return "Choose model"
    return name[:-6] + " · cloud" if core.is_cloud(name) and name.endswith("-cloud") else name


def fit_label(lab, plain, widest, hug=True):
    """Give a wrapping label the size its text needs: as wide as the text (up to `widest`) and
    exactly as tall as the wrapped text."""
    metrics = lab.fontMetrics()
    longest = max((metrics.horizontalAdvance(line) for line in plain.split("\n")), default=0)
    width = max(24, min(widest, longest + 6)) if hug else widest
    lab.setFixedWidth(width)
    lab.setFixedHeight(max(metrics.height(), lab.heightForWidth(width)))


class Fab(QWidget):
    """The round button: its own window, so it can float over the main window's corner."""
    clicked = pyqtSignal()

    def __init__(self, root):
        super().__init__(root, FLOATING | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(FAB, FAB)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hover, self.ring, self.glyph = False, False, ICONS["chat"]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self.ring:                                   # a reply is waiting
            p.setBrush(QColor(DANGER))
            p.drawEllipse(QRectF(0, 0, FAB, FAB))
            inner = QRectF(3, 3, FAB - 6, FAB - 6)
        else:
            inner = QRectF(0, 0, FAB, FAB)
        p.setBrush(QColor(ACCENT_HOVER if self.hover else ACCENT))
        p.drawEllipse(inner)
        p.setPen(QPen(QColor(ON_ACCENT)))
        p.setFont(icon_font(22))
        p.drawText(QRectF(0, 0, FAB, FAB), Qt.AlignmentFlag.AlignCenter, self.glyph)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()


class Header(QWidget):
    """The panel's title bar: drag it to move the panel."""

    def __init__(self):
        super().__init__()
        self.on_press = self.on_move = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.on_press:
            self.on_press(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self.on_move:
            self.on_move(event.globalPosition().toPoint())


class MessageBox(QPlainTextEdit):
    """Where you type: Enter sends, Shift+Enter makes a new line, Esc closes the panel."""

    def __init__(self):
        super().__init__()
        self.on_send = self.on_escape = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if self.on_send:
                self.on_send()
            return
        if event.key() == Qt.Key.Key_Escape and self.on_escape:
            self.on_escape()
            return
        super().keyPressEvent(event)


class Watcher(QObject):
    """Calls `callback` when the main window moves, is resized or is shown."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show,
                            QEvent.Type.WindowActivate):
            self.callback()
        return False


# ---- the chatbot ----

def build(root, say=None, db_path=None, tools=None):
    say = say or (lambda text: None)
    db_path = db_path or core.DB_PATH            # looked up now, so tests can point it elsewhere
    db = core.load_db(db_path)
    state = {"open": False, "busy": False, "bot": None, "handle": None, "events": queue.Queue(),
             "dx": MARGIN, "dy": MARGIN + FAB + 12, "motion": None, "drag": None, "bubbles": [],
             "models": [], "models_error": None, "dirty": False, "answers": queue.Queue(),
             "toolmap": {}, "confirm": None, "polls": {}, "cards": [], "noticed": set(),
             "unread": False}

    # ---- the round button: a window of its own ----
    fab = Fab(root)

    # ---- the panel: another window ----
    panel = QWidget(root, FLOATING)
    panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    panel.resize(W, H)
    outer = QVBoxLayout(panel)
    outer.setContentsMargins(0, 0, 0, 0)
    card = QFrame()
    card.setObjectName("chatcard")
    card.setStyleSheet(f"QFrame#chatcard {{ background: {SURFACE}; border: 1px solid {BORDER}; "
                       f"border-radius: 20px; }}")
    outer.addWidget(card)
    column = QVBoxLayout(card)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)

    header = Header()
    header.setFixedHeight(56)
    head = QHBoxLayout(header)
    head.setContentsMargins(14, 10, 8, 4)
    head.setSpacing(0)
    avatar = QLabel(ICONS["robot"])
    avatar.setFont(icon_font(16))
    avatar.setFixedSize(32, 32)
    avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
    avatar.setStyleSheet(f"background: {ACCENT_DIM}; color: {ACCENT}; border-radius: 16px;")
    names = QVBoxLayout()
    names.setContentsMargins(10, 0, 0, 0)
    names.setSpacing(0)
    title = label(core.BOT_NAME, 14, FG, family="Segoe UI Semibold")
    status = label("Ready", 11, MUTED)
    names.addWidget(title)
    names.addWidget(status)
    for widget in (avatar, title, status):
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    head.addWidget(avatar)
    head.addLayout(names)
    head.addStretch(1)
    model_button = button(model_label(db["model"]), lambda: open_model_menu(), kind="soft",
                          width=140, height=26, size=11)
    head.addWidget(model_button)
    head.addSpacing(6)
    compose_button = icon_button("compose", lambda: new_chat(), size=30)
    head.addWidget(compose_button)
    head.addWidget(icon_button("chevron_down", lambda: close_panel(), size=30))
    column.addWidget(header)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    inner = QWidget()
    inner.setObjectName("messagesinner")
    inner.setStyleSheet("QWidget#messagesinner { background: transparent; }")   # only itself, not its buttons
    messages = QVBoxLayout(inner)
    messages.setContentsMargins(10, 4, 4, 4)
    messages.setSpacing(8)
    messages.addStretch(1)
    scroll.setWidget(inner)
    column.addWidget(scroll, 1)

    box_frame = QFrame()
    box_frame.setStyleSheet(f"QFrame#inputbox {{ background: {RAISED}; border-radius: 18px; }}")
    box_frame.setObjectName("inputbox")
    box_layout = QHBoxLayout(box_frame)
    box_layout.setContentsMargins(8, 4, 8, 4)
    box = MessageBox()
    box.setFont(font(13))
    box.setPlaceholderText(f"Message {core.BOT_NAME}…")
    box.setFixedHeight(54)
    box.setStyleSheet(f"QPlainTextEdit {{ background: transparent; border: none; padding: 4px; color: {FG}; }}")
    box_layout.addWidget(box, 1)
    send_button = QPushButton(ICONS["send"])
    send_button.setFont(icon_font(15))
    send_button.setFixedSize(36, 36)
    send_button.setCursor(Qt.CursorShape.PointingHandCursor)
    send_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    send_button.clicked.connect(lambda: stop() if state["busy"] else send())
    box_layout.addWidget(send_button, 0, Qt.AlignmentFlag.AlignBottom)
    wrapper = QVBoxLayout()
    wrapper.setContentsMargins(12, 4, 12, 12)
    wrapper.addWidget(box_frame)
    column.addLayout(wrapper)

    def style_send(busy):
        fill, hover, ink = (DANGER_BG, DANGER_BG, DANGER) if busy else (ACCENT, ACCENT_HOVER, ON_ACCENT)
        send_button.setText(ICONS["stop"] if busy else ICONS["send"])
        send_button.setStyleSheet(
            f"QPushButton {{ background: {fill}; color: {ink}; border: none; border-radius: 18px; padding: 0; }}"
            f"QPushButton:hover {{ background: {hover}; }}")

    style_send(False)

    # ---- helpers ----

    def set_status(text, busy=False):
        status.setText(text)
        set_color(status, ACCENT if busy else MUTED)

    def scroll_down():
        """Scroll to the newest message (once the layout has caught up with new text)."""
        def go():
            bar = scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        go()
        QTimer.singleShot(0, go)
        QTimer.singleShot(60, go)

    def pinned():
        """True if the list is scrolled to the bottom (so new text should keep it there)."""
        bar = scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - 24

    def make_bubble(role):
        mine = role == "you"
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        color = ACCENT_DIM if mine else RAISED
        bubble_card = QFrame()
        bubble_card.setObjectName("bubble")
        bubble_card.setStyleSheet(f"QFrame#bubble {{ background: {color}; border-radius: 16px; }}")
        inside = QVBoxLayout(bubble_card)
        inside.setContentsMargins(PAD_X, PAD_Y, PAD_X, PAD_Y)
        inside.setSpacing(4)
        steps = QLabel()
        steps.setFont(font(11, family=MONO_FAMILY))
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.TextFormat.RichText)
        steps.hide()
        text = QLabel()
        text.setFont(font(13))
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.setCursor(Qt.CursorShape.IBeamCursor)
        inside.addWidget(steps)
        inside.addWidget(text)
        if mine:
            row_layout.addStretch(1)
            row_layout.addWidget(bubble_card)
        else:
            row_layout.addWidget(bubble_card)
            row_layout.addStretch(1)
        messages.insertWidget(messages.count() - 1, row)
        bubble = {"role": role, "row": row, "card": bubble_card, "text": text, "steps_label": steps,
                  "raw": ""}
        state["bubbles"].append(bubble)
        return bubble

    def set_bubble(bubble, raw, error=False):
        bubble["raw"] = raw
        t, widest = bubble["text"], BUBBLE_PX - 2 * PAD_X
        if bubble["role"] == "you":
            t.setTextFormat(Qt.TextFormat.PlainText)
            t.setText(raw)
            fit_label(t, raw, widest, hug=True)
        else:
            t.setTextFormat(Qt.TextFormat.MarkdownText)
            t.setText(raw)
            fit_label(t, raw, widest, hug="\n" not in raw and len(raw) < 60)
        set_color(t, DANGER if error else FG)
        steps = bubble.get("steps", [])
        lab = bubble["steps_label"]
        if steps:
            lab.setText("<br>".join(
                f"<span style='color:{DANGER if s['bad'] else MUTED}'>{html.escape(step_line(s))}</span>"
                for s in steps))
            fit_label(lab, "\n".join(step_line(s) for s in steps), widest, hug=False)
            lab.show()
        else:
            lab.hide()

    def drop_bubble(bubble):
        bubble["row"].hide()
        bubble["row"].deleteLater()
        if bubble in state["bubbles"]:
            state["bubbles"].remove(bubble)
        scroll_down()

    def step_line(step):
        """One line about a tool call in a reply: mark, call, and a short result."""
        result = f"  →  {step['result']}" if step.get("result") else ""
        return f"{step['mark']} {step['text']}{result}"

    def one_line(text, limit=70):
        """The first line of a result, and how many more lines there were."""
        lines = [line for line in str(text).strip().split("\n")]
        first = lines[0]
        more = f"  (+{len(lines) - 1} lines)" if len(lines) > 1 else ""
        return (first if len(first) <= limit else first[:limit - 1] + "…") + more

    def redraw_bot():
        bot = state["bot"]
        if bot is not None:
            was_pinned = pinned()
            set_bubble(bot, bot["raw"])
            if was_pinned:
                scroll_down()

    # ---- tools: the model asks for something to be done ----

    def answer_core(call_id, text):
        handle = state["handle"]
        if handle:
            handle["answer"](call_id, text)

    def finish_step(call_id, step, result, denied=False):
        """A tool call is done: show how it went and give the result back to the model."""
        bad = denied or str(result).startswith("Error:")
        step.update(mark="✗" if bad else "✓", result=one_line(result if not denied else "denied"), bad=bad)
        redraw_bot()
        set_status("Thinking…", busy=True)
        answer_core(call_id, result)

    def run_step(call_id, step, t, args):
        set_status(("Using " + t["name"])[:30] + "…", busy=True)
        result = chat_tools.call(t, args)
        if not callable(result):
            finish_step(call_id, step, result)
            return
        deadline = time.time() + t["timeout"]
        state["polls"][call_id] = True

        def poll_tool():                           # for tools that take a while (a terminal command)
            if call_id not in state["polls"]:
                return
            try:
                value = result()
            except Exception as err:
                value = f"Error: {err}"
            if value is None and time.time() < deadline:
                QTimer.singleShot(100, poll_tool)
                return
            state["polls"].pop(call_id, None)
            finish_step(call_id, step, value if value is not None else "Error: the tool took too long")

        poll_tool()

    def show_confirm(call_id, step, t, args, reason):
        """A card in the chat: what the model wants to do, with Allow and Deny."""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        card_ = QFrame()
        card_.setObjectName("confirm")
        card_.setStyleSheet(f"QFrame#confirm {{ background: {RAISED}; border: 1px solid {ACCENT}; "
                            f"border-radius: 14px; }}")
        card_.setFixedWidth(BUBBLE_PX)
        inside = QVBoxLayout(card_)
        inside.setContentsMargins(14, 10, 14, 10)
        inside.setSpacing(4)
        inside.addWidget(label(f"{core.BOT_NAME} wants to…", 11, ACCENT))
        why = label(reason, 12, FG)
        why.setWordWrap(True)
        fit_label(why, reason, BUBBLE_PX - 28, hug=False)
        inside.addWidget(why)
        buttons = QWidget()
        buttons_row = QHBoxLayout(buttons)
        buttons_row.setContentsMargins(0, 4, 0, 0)
        buttons_row.setSpacing(6)
        buttons_row.addWidget(button("Allow", lambda: decide(True), kind="primary", width=84))
        buttons_row.addWidget(button("Deny", lambda: decide(False), kind="soft", width=70))
        buttons_row.addStretch(1)
        inside.addWidget(buttons)
        row_layout.addWidget(card_)
        row_layout.addStretch(1)
        messages.insertWidget(messages.count() - 1, row)
        state["cards"].append(row)
        state["confirm"] = {"call_id": call_id, "step": step, "tool": t, "args": args, "card": card_,
                            "buttons": buttons, "reason": reason, "layout": inside}
        set_status("Waiting for your OK…", busy=True)
        notify()
        scroll_down()

    def settle_card(c, text):
        c["buttons"].hide()
        c["buttons"].deleteLater()
        c["layout"].addWidget(label(text, 11, MUTED))

    def decide(allowed):
        """Martin pressed Allow or Deny."""
        c = state["confirm"]
        if not c:
            return
        state["confirm"] = None
        settle_card(c, "Allowed" if allowed else "Denied")
        if allowed:
            run_step(c["call_id"], c["step"], c["tool"], c["args"])
        else:
            finish_step(c["call_id"], c["step"], "Denied: Martin said no, so this was not done.", denied=True)

    def cancel_pending():
        """Forget a question that is waiting for an answer, and tool calls that are running."""
        c, state["confirm"] = state["confirm"], None
        if c:
            settle_card(c, "Cancelled")
        state["polls"].clear()

    def on_tool_call(call_id, name, args):
        bot = state["bot"]
        t = state["toolmap"].get(name)
        step = {"mark": "›", "text": chat_tools.summary(name, args), "result": "", "bad": False}
        bot.setdefault("steps", []).append(step)
        if not bot.get("streaming"):
            bot["raw"] = "…"
        redraw_bot()
        if t is None:
            finish_step(call_id, step, f"Error: there is no tool called {name}.")
            return
        reason = chat_tools.needs_ok(t, args)
        if reason:
            show_confirm(call_id, step, t, args, reason)
        else:
            run_step(call_id, step, t, args)

    # ---- choosing the model ----

    def current_model():
        return db["model"] or core.default_model(state["models"])

    def show_model():
        name = current_model()
        model_button.setText(model_label(name) if name else ("Ollama?" if state["models_error"] else "Choose model"))

    def load_models(then=None):
        """Ask Ollama which models it has (in a thread, so the window never waits)."""
        def work():
            try:
                found, error = core.models(), None
            except OSError as err:
                found, error = [], str(err)
            state["answers"].put((found, error, then))     # poll() hands it to the window
        threading.Thread(target=work, daemon=True).start()

    def got_models(found, error, then):
        state["models"], state["models_error"] = found, error
        show_model()
        if then:
            then()

    def choose(name):
        db["model"] = name
        core.save_db(db, db_path)
        show_model()
        set_status("Using " + model_label(name)[:16])

    def open_model_menu():
        def menu():
            if state["models_error"] or not state["models"]:
                text = state["models_error"] or "No model found. Run: ollama pull gemma4:12b"
                say(text)
                set_status("Ollama?")
                return
            current = current_model()
            popup_menu(model_button, [
                (m["name"] + ("  · cloud" if m["cloud"] else ""), lambda n=m["name"]: choose(n), None,
                 m["name"] == current) for m in state["models"]], width=270, align="right")
        load_models(menu)

    # ---- sending and receiving ----

    def idle():
        cancel_pending()
        state["busy"], state["bot"], state["handle"] = False, None, None
        style_send(False)
        set_status("Ready")

    def paint_fab():
        fab.ring = state["unread"]
        fab.update()

    def notify():
        if not state["open"]:
            state["unread"] = True
            paint_fab()

    def send(text=None):
        """Send the typed message (or `text`). Returns False if there was nothing to send."""
        if state["busy"]:
            return False
        text = (box.toPlainText() if text is None else text).strip()
        if not text:
            return False
        box.clear()
        set_bubble(make_bubble("you"), text)
        core.add_message(db, "you", text)
        core.save_db(db, db_path)
        bot = make_bubble("bot")
        set_bubble(bot, "…")
        scroll_down()
        state.update(busy=True, bot=bot, events=queue.Queue())
        state["noticed"] = set()
        tool_list = []
        if tools:
            try:
                tool_list = tools()                # what the app can do right now (apps may have changed)
            except Exception as err:
                say(f"Tools unavailable: {err}")
        state["toolmap"] = {t["name"]: t for t in tool_list}
        state["handle"] = core.ask(db, state["events"], [chat_tools.schema(t) for t in tool_list] or None)
        style_send(True)
        set_status("Thinking…", busy=True)
        return True

    def stop():
        if state["busy"] and state["handle"]:
            state["handle"]["cancel"]()
            cancel_pending()                       # a question still waiting for Allow / Deny is dropped

    def finish(text):
        bot = state["bot"]
        spoken = bot["raw"] if bot.get("streaming") else ""
        raw = text or spoken or ("Done." if bot.get("steps") else "(no reply)")
        set_bubble(bot, raw)
        core.add_message(db, "bot", raw)
        core.save_db(db, db_path)
        idle()
        scroll_down()
        notify()

    def fail(message):
        set_bubble(state["bot"], "Something went wrong: " + message, error=True)
        idle()
        scroll_down()
        notify()
        say("Chat error: " + message[:80])

    def stopped():
        bot = state["bot"]
        if bot["raw"] and bot.get("streaming"):
            set_bubble(bot, bot["raw"] + "\n\n(stopped)")
            core.add_message(db, "bot", bot["raw"])
            core.save_db(db, db_path)
        else:
            drop_bubble(bot)
        idle()

    def handle_event(event):
        bot, kind = state["bot"], event[0]
        if bot is None:
            return
        if kind == "text":
            if not bot.get("streaming"):
                bot["streaming"], bot["raw"] = True, ""
            bot["raw"] += event[1]
            state["dirty"] = True
            set_status("Writing…", busy=True)
        elif kind == "thinking":
            set_status("Thinking…", busy=True)
        elif kind == "tool_call":
            on_tool_call(event[1], event[2], event[3])
        elif kind == "notice":
            note = event[1]
            if note not in state["noticed"]:       # say it once per message, not per round
                state["noticed"].add(note)
                bot.setdefault("steps", []).append({"mark": "i", "text": note, "result": "", "bad": False})
                redraw_bot()
        elif kind == "done":
            finish(event[1])
        elif kind == "error":
            fail(event[1])
        elif kind == "cancelled":
            stopped()

    def poll():
        while True:                                # the model lists that threads have fetched
            try:
                found, error, then = state["answers"].get_nowait()
            except queue.Empty:
                break
            got_models(found, error, then)
        while True:
            try:
                event = state["events"].get_nowait()
            except queue.Empty:
                break
            handle_event(event)
        if state["dirty"] and state["bot"] is not None:
            state["dirty"] = False
            redraw_bot()

    def new_chat():
        """Start a fresh conversation (the old one is forgotten)."""
        if state["busy"] and state["handle"]:
            state["handle"]["cancel"]()
        state["events"] = queue.Queue()            # whatever the old reply still sends is ignored
        cancel_pending()
        for bubble in list(state["bubbles"]):
            drop_bubble(bubble)
        for row in state["cards"]:                 # the Allow / Deny cards
            row.hide()
            row.deleteLater()
        state["cards"].clear()
        core.new_chat(db)
        core.save_db(db, db_path)
        idle()
        set_status("New chat")

    # ---- where the two windows are ----

    def origin():
        return root.mapToGlobal(QPoint(0, 0))

    def panel_xy(offset=0):
        """Top-left of the panel on the screen, from its distance to the main window's corner."""
        o = origin()
        return (o.x() + root.width() - state["dx"] - W, o.y() + root.height() - state["dy"] - H + offset)

    def place_panel(offset=0):
        x, y = panel_xy(offset)
        panel.move(x, y)

    def place_fab():
        o = origin()
        fab.move(o.x() + root.width() - MARGIN - FAB, o.y() + root.height() - MARGIN - FAB)

    def open_panel(animate=True):
        if state["open"]:
            return
        state["open"] = True
        if state["motion"]:
            state["motion"]["cancel"]()
        panel.setWindowOpacity(0.0)
        place_panel(24)
        panel.show()
        panel.raise_()
        fab.glyph = ICONS["close"]
        state["unread"] = False
        paint_fab()
        scroll_down()

        def step(t):
            panel.setWindowOpacity(t)
            place_panel(round(24 * (1 - t)))

        state["motion"] = tween(step, ms=210 if animate else 0)
        panel.activateWindow()
        box.setFocus()
        if not state["models"] and not state["models_error"]:
            load_models()

    def close_panel(animate=True):
        if not state["open"]:
            return
        state["open"] = False
        if state["motion"]:
            state["motion"]["cancel"]()
        fab.glyph = ICONS["chat"]
        fab.update()

        def step(t):
            panel.setWindowOpacity(1 - t)
            place_panel(round(24 * t))

        state["motion"] = tween(step, ms=160 if animate else 0, done=panel.hide, ease=ease_in)
        root.activateWindow()

    def toggle():
        close_panel() if state["open"] else open_panel()

    def grab(pos):
        state["drag"] = (pos.x(), pos.y(), state["dx"], state["dy"])

    def drag(pos):
        if not state["drag"] or not state["open"]:
            return
        x0, y0, dx0, dy0 = state["drag"]
        dx, dy = dx0 - (pos.x() - x0), dy0 - (pos.y() - y0)
        o, screen = origin(), QGuiApplication.primaryScreen().availableGeometry()
        x = o.x() + root.width() - dx - W                           # keep it on the screen
        y = o.y() + root.height() - dy - H
        x = max(screen.left(), min(screen.right() - W, x))
        y = max(screen.top(), min(screen.bottom() - 80, y))
        state["dx"] = o.x() + root.width() - x - W
        state["dy"] = o.y() + root.height() - y - H
        place_panel()

    def on_root_changed():
        place_fab()                                # the main window moved, was resized or shown
        if state["open"] and not (state["motion"] and state["motion"]["running"]()):
            place_panel()
        raise_floating()

    def ours(widget):
        """True for the main window, our two windows and anything opened from the main window."""
        while widget is not None:
            if widget is root or widget is panel or widget is fab:
                return True
            widget = widget.parentWidget()
        return False

    def raise_floating():
        """Keep the button (and the panel) in front of the main window. Windows lets a clicked
        window, especially one with a web page in it, climb over its own floating windows; so
        whenever Eden Root is the window in use, bring them forward again. (They are not raised
        while another program is in use, or over a dialog that is open.)"""
        if QApplication.activeModalWidget() is not None or not ours(QApplication.activeWindow()):
            return
        if state["open"]:
            panel.raise_()
        fab.raise_()

    # ---- keys and clicks ----

    box.on_send = send
    box.on_escape = close_panel
    fab.clicked.connect(toggle)
    header.on_press, header.on_move = grab, drag
    watcher = Watcher(on_root_changed)
    root.installEventFilter(watcher)
    keep_front = QTimer(root)                      # and now and then, for whatever the events missed
    keep_front.timeout.connect(raise_floating)
    keep_front.start(300)

    # ---- bring back the saved conversation, find the models, start listening ----
    for item in db["messages"]:
        set_bubble(make_bubble(item["role"]), item["text"])
    place_fab()
    fab.show()
    scroll_down()
    load_models()
    timer = QTimer(root)
    timer.timeout.connect(poll)
    timer.start(40)

    def shutdown():
        stop()

    return {"fab": fab, "panel": panel, "card": card, "state": state, "db": db, "send": send,
            "stop": stop, "toggle": toggle, "open": open_panel, "close": close_panel,
            "new_chat": new_chat, "bubbles": lambda: state["bubbles"], "shutdown": shutdown,
            "box": box, "status": status, "send_button": send_button, "header": header,
            "scroll": scroll, "model_button": model_button, "load_models": load_models,
            "choose": choose, "open_model_menu": open_model_menu, "watcher": watcher,
            "scroll_down": scroll_down, "allow": lambda: decide(True), "deny": lambda: decide(False),
            "poll": poll}

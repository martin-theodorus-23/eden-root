"""Terminal: type PowerShell commands and see what they print.

    build(parent, say) -> dict

Builds the terminal inside `parent` (an app window's body). One PowerShell session lives as
long as the terminal does, so variables and the current folder carry over from one command
to the next. Output is read through plain pipes, so full-screen programs (vim, ssh -t) and
commands that ask questions don't work; a command that asks fails instead of hanging.

Keys: Enter runs, Up/Down browse the history, Ctrl+C stops a running command.
The returned dict holds: frame, output, state, run, stop, clear, shutdown, can_close, get_output,
mark, since, is_busy, get_cwd.
"""
import os
import queue
import subprocess
import threading
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget)

from chat.tools import clip, int_arg, text_arg, tool
from theme import ACCENT, FG, MUTED, button, label, mono_font, set_color

START_DIR = r"D:\raid-disk" if os.path.isdir(r"D:\raid-disk") else os.path.expanduser("~")      # your home folder on other PCs
MARK = "<<EDEN-DONE>>"
MARK_COMMAND = f'Write-Host "{MARK}$((Get-Location).Path)"'   # printed after every command
MAX_LINES = 5000
KEEP_CHARS = 400_000                              # how much of everything printed is kept for the chat
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def kill_tree(proc):
    """Stop a process and everything it started."""
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                   creationflags=NO_WINDOW)


def read_output(proc, lines):
    """Runs in a thread: pass every line PowerShell prints on to the window."""
    for line in proc.stdout:
        lines.put((proc, line))
    lines.put((proc, None))                       # None = the session ended


class InputLine(QLineEdit):
    """The command box: Up/Down walk through the history, Ctrl+C stops a running command."""

    def __init__(self):
        super().__init__()
        self.on_key = None

    def keyPressEvent(self, event):
        if self.on_key and self.on_key(event):
            return
        super().keyPressEvent(event)


def build(parent, say=None):
    say = say or (lambda text: None)
    lines = queue.Queue()
    state = {"proc": None, "pending": 0, "cwd": START_DIR, "history": [], "pos": 0,
             "log": "", "dropped": 0}

    frame = QWidget(parent)
    column = QVBoxLayout(frame)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(4)

    bar = QWidget()
    bar.setFixedHeight(28)
    toolbar = QHBoxLayout(bar)
    toolbar.setContentsMargins(0, 0, 0, 0)
    toolbar.setSpacing(4)
    column.addWidget(bar)
    chip = QFrame()
    chip.setObjectName("chip")
    chip.setFixedHeight(28)
    chip_row = QHBoxLayout(chip)
    chip_row.setContentsMargins(12, 0, 12, 0)
    chip_row.setSpacing(6)
    dot = label("\u25cf", 11, MUTED)
    status_label = label("Idle", 12, MUTED)
    chip_row.addWidget(dot)
    chip_row.addWidget(status_label)

    output = QPlainTextEdit()
    output.setReadOnly(True)
    output.setFont(mono_font(14))
    output.setMaximumBlockCount(MAX_LINES)
    output.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
    output.setStyleSheet("QPlainTextEdit { padding: 8px; }")
    column.addWidget(output, 1)

    inputrow = QFrame()
    inputrow.setObjectName("inputrow")
    inputrow.setStyleSheet("QFrame#inputrow { background: #1e2128; border-radius: 10px; }")
    inputrow.setFixedHeight(34)
    input_layout = QHBoxLayout(inputrow)
    input_layout.setContentsMargins(12, 0, 8, 0)
    input_layout.setSpacing(6)
    prompt_label = label("", 14, ACCENT, family="Consolas")
    entry = InputLine()
    entry.setFont(mono_font(14))
    input_layout.addWidget(prompt_label)
    input_layout.addWidget(entry, 1)
    column.addWidget(inputrow)

    prompt_format = QTextCharFormat()
    prompt_format.setForeground(QColor(ACCENT))
    plain_format = QTextCharFormat()
    plain_format.setForeground(QColor(FG))

    # ---- output ----

    def append(text, tag=None):
        cursor = output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text, prompt_format if tag == "prompt" else plain_format)
        output.setTextCursor(cursor)
        output.ensureCursorVisible()
        state["log"] += text
        if len(state["log"]) > KEEP_CHARS:
            cut = len(state["log"]) - KEEP_CHARS // 2
            state["log"], state["dropped"] = state["log"][cut:], state["dropped"] + cut

    def mark():
        """A position in everything printed so far (for since())."""
        return state["dropped"] + len(state["log"])

    def since(position):
        """Everything printed after a position given by mark()."""
        return state["log"][max(0, position - state["dropped"]):]

    def clear():
        output.clear()

    def show_state():
        busy = bool(state["pending"])
        prompt_label.setText(state["cwd"] + ">")
        status_label.setText("Running..." if busy else "Idle")
        set_color(status_label, FG if busy else MUTED)
        set_color(dot, ACCENT if busy else MUTED)

    # ---- the PowerShell session ----

    def send_command(command):
        proc = state["proc"]
        state["pending"] += 1
        try:
            proc.stdin.write(command + "\n" + MARK_COMMAND + "\n")
            proc.stdin.flush()
        except OSError:
            state["pending"] -= 1

    def start_session():
        cwd = state["cwd"] if os.path.isdir(state["cwd"]) else START_DIR
        state["proc"] = subprocess.Popen(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1, cwd=cwd, creationflags=NO_WINDOW)
        state["pending"] = 0
        threading.Thread(target=read_output, args=(state["proc"], lines), daemon=True).start()
        quoted = cwd.replace("'", "''")
        send_command("try { [Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                     "$OutputEncoding = [Text.Encoding]::UTF8 } catch {}; "
                     f"Set-Location -LiteralPath '{quoted}'")

    def run(command):
        command = command.strip()
        if not command:
            return
        state["history"].append(command)
        state["pos"] = len(state["history"])
        if command.lower() in ("cls", "clear", "clear-host"):
            clear()
            return
        append(state["cwd"] + "> " + command + "\n", "prompt")
        proc = state["proc"]
        if proc is None or proc.poll() is not None:
            start_session()
        send_command(command)
        show_state()

    def stop():
        """Stop the running command. The session restarts in the same folder on the next command."""
        proc = state["proc"]
        if proc is None or not state["pending"]:
            return
        kill_tree(proc)
        state["proc"] = None
        state["pending"] = 0
        append("^C\n", "prompt")
        show_state()
        say("Stopped")

    def shutdown():
        try:
            timer.stop()
        except RuntimeError:                      # the window (and its timer) is already gone
            pass
        proc = state["proc"]
        state["proc"] = None
        if proc is not None and proc.poll() is None:
            kill_tree(proc)

    def handle(line):
        if line is None:                          # the session ended (exit, or it was killed)
            state["proc"] = None
            state["pending"] = 0
            append("[session ended]\n", "prompt")
            return
        where = line.find(MARK)
        if where < 0:
            append(line)
            return
        if line[:where].strip():
            append(line[:where] + "\n")
        state["cwd"] = line[where + len(MARK):].strip() or state["cwd"]
        state["pending"] = max(0, state["pending"] - 1)

    def poll():
        changed = False
        while True:
            try:
                proc, line = lines.get_nowait()
            except queue.Empty:
                break
            if proc is state["proc"]:             # ignore leftovers from a stopped session
                handle(line)
                changed = True
        if changed:
            show_state()

    timer = QTimer(frame)                         # no timer is left running for a dead window
    timer.timeout.connect(poll)
    timer.start(40)
    frame.destroyed.connect(lambda *args: shutdown())

    # ---- toolbar, keys ----

    toolbar.addWidget(button("Clear", clear, width=62))
    toolbar.addWidget(button("Stop", stop, width=62))
    toolbar.addStretch(1)
    toolbar.addWidget(chip)

    def on_enter():
        run(entry.text())
        entry.clear()

    def on_key(event):
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            if state["history"]:
                state["pos"] = min(len(state["history"]),
                                   max(0, state["pos"] + (-1 if key == Qt.Key.Key_Up else 1)))
                entry.setText(state["history"][state["pos"]] if state["pos"] < len(state["history"]) else "")
            return True
        if (key == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and state["pending"] and not entry.hasSelectedText()):
            stop()                                # running: Ctrl+C stops it; otherwise it copies
            return True
        return False

    entry.returnPressed.connect(on_enter)
    entry.on_key = on_key

    start_session()
    show_state()
    entry.setFocus()
    return {"frame": frame, "toolbar": bar, "output": output, "state": state, "run": run, "stop": stop,
            "clear": clear, "shutdown": shutdown, "can_close": lambda: True,
            "get_output": lambda: output.toPlainText(), "mark": mark, "since": since,
            "is_busy": lambda: bool(state["pending"]), "get_cwd": lambda: state["cwd"]}


# --------------------------------------------------------------------------
# Tools for the chatbot
# --------------------------------------------------------------------------

def tools(api):
    window_prop = {"window": {"type": "string", "description": "Terminal window id or title; may be left out when only one Terminal is open"}}

    def pick(args):
        return api["find_window"](args.get("window"), "terminal")

    def run_confirm(args):
        inst = pick(args)
        return f"Run this in {inst['title']} (PowerShell, folder {inst['app']['get_cwd']()}):\n{text_arg(args, 'command').strip()}"

    def run_command(args):
        inst = pick(args)
        app = inst["app"]
        command = text_arg(args, "command").strip()
        if not command:
            raise ValueError("'command' is empty")
        limit = int_arg(args, "timeout_seconds", 30, 1, 300)
        if command.lower() in ("cls", "clear", "clear-host"):
            app["run"](command)
            return "Cleared the screen."
        marker = app["mark"]()
        started = time.time()
        app["run"](command)

        def poll():
            """Called again and again by the chat until the command is finished (or too slow)."""
            if app["is_busy"]() and time.time() - started < limit:
                return None
            shown = clip(app["since"](marker).rstrip(), 5000)
            if app["is_busy"]():
                return (f"{shown}\n[Still running after {limit} s. Use terminal_read to look again "
                        f"or terminal_stop to stop it.]")
            return f"{shown}\n[folder: {app['get_cwd']()}]"

        return poll

    def read(args):
        inst = pick(args)
        count = int_arg(args, "lines", 40, 1, 400)
        lines = inst["app"]["get_output"]().rstrip().split("\n")
        tail = clip("\n".join(lines[-count:]), 6000)
        busy = "\n[a command is still running]" if inst["app"]["is_busy"]() else ""
        return f"{inst['title']} (folder {inst['app']['get_cwd']()}), last {min(count, len(lines))} lines:\n{tail}{busy}"

    def stop(args):
        inst = pick(args)
        if not inst["app"]["is_busy"]():
            return f"Nothing is running in {inst['title']}."
        inst["app"]["stop"]()
        return f"Stopped the running command in {inst['title']}."

    def clear(args):
        inst = pick(args)
        inst["app"]["clear"]()
        return f"Cleared {inst['title']}."

    return [
        tool("terminal_run", "Run a PowerShell command in a Terminal window and return what it printed. Asks Martin first. The session keeps its variables and current folder between commands. Commands that wait for input or run full-screen programs do not work.",
             {**window_prop, "command": {"type": "string"}, "timeout_seconds": {"type": "integer", "description": "how long to wait for the command to finish (default 30)"}},
             ["command"], run_command, run_confirm, timeout=330),
        tool("terminal_read", "Read the last lines printed in a Terminal window.", {**window_prop, "lines": {"type": "integer", "description": "how many lines (default 40)"}}, run=read),
        tool("terminal_stop", "Stop the command that is running in a Terminal window.", window_prop, run=stop),
        tool("terminal_clear", "Clear the screen of a Terminal window.", window_prop, run=clear),
    ]

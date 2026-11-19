"""Media Viewer: look at pictures, watch videos, listen to sound files.

    build(parent, say) -> dict

Builds the viewer inside `parent` (an app window's body).
Pictures: png, jpg, gif (animated too), webp, bmp, tiff, ico, svg. Mouse wheel zooms, drag moves the
picture, double-click switches between "fit" and 100%.
Videos and sound: mp4, mkv, webm, avi, mov, mp3, wav, flac and more, played by Windows' own codecs
(so H.264 / AAC files work here even though the Browser cannot play them). Click the picture to
pause, click the bar to jump.
Keys: Ctrl+O open, Space play/pause, Left/Right previous/next picture (seek 5 s in a video),
PgUp/PgDn previous/next file in the folder, + - zoom, 0 fit, 1 actual size, M mute.

The returned dict holds: frame, toolbar, view, player, open, step, control, describe, get_path,
get_state, set_state, shutdown, can_close, last_error.
"""
import os
import re

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QImageReader, QKeySequence, QMovie, QPainter, QPixmap, QShortcut
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (QFileDialog, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
                             QHBoxLayout, QSlider, QStackedWidget, QStyle, QVBoxLayout, QWidget)

from chat.tools import int_arg, text_arg, tool
from theme import (ACCENT, BORDER, CODE_BG, FG, HOVER, ICONS, MUTED, NameChip, button, icon_button, label, set_on)

IMAGES = {".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".ico", ".svg"}
VIDEOS = {".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".mpg", ".mpeg", ".3gp", ".flv"}
SOUNDS = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma"}
FILTER = ("Pictures, videos and sound (" + " ".join("*" + e for e in sorted(IMAGES | VIDEOS | SOUNDS)) + ");;"
          "Pictures (" + " ".join("*" + e for e in sorted(IMAGES)) + ");;"
          "Videos (" + " ".join("*" + e for e in sorted(VIDEOS)) + ");;"
          "Sound (" + " ".join("*" + e for e in sorted(SOUNDS)) + ");;All files (*.*)")
START = r"D:\raid-disk" if os.path.isdir(r"D:\raid-disk") else os.path.expanduser("~")      # your home folder on other PCs
QImageReader.setAllocationLimit(1024)           # megabytes; big photos and scans are fine


def kind_of(path):
    """'image', 'video', 'audio' or None, from the file name's ending."""
    ext = os.path.splitext(str(path))[1].lower()
    return "image" if ext in IMAGES else "video" if ext in VIDEOS else "audio" if ext in SOUNDS else None


def natural(name):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def siblings(path):
    """The media files in the same folder as `path`, in natural name order (photo2 before photo10)."""
    folder = os.path.dirname(path)
    try:
        names = [n for n in os.listdir(folder) if kind_of(n) and os.path.isfile(os.path.join(folder, n))]
    except OSError:
        return [path]
    return [os.path.join(folder, n) for n in sorted(names, key=natural)]


def clock(ms):
    seconds = max(0, int(ms // 1000))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


SLIDER = (f"QSlider::groove:horizontal {{ height: 4px; background: {HOVER}; border-radius: 2px; }}"
          f"QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}"
          f"QSlider::handle:horizontal {{ background: {FG}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}")


class Seek(QSlider):
    """A slider that jumps to where you click and follows the mouse while you drag."""
    sought = pyqtSignal(int)

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.dragging = False
        self.setStyleSheet(SLIDER)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def seek_to(self, event):
        value = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(event.position().x()), max(1, self.width()))
        self.setValue(value)
        self.sought.emit(value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.seek_to(event)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.seek_to(event)

    def mouseReleaseEvent(self, event):
        self.dragging = False


class ImageView(QGraphicsView):
    """One picture you can zoom (wheel), move (drag) and fit to the window."""

    def __init__(self, on_change):
        super().__init__()
        self.on_change = on_change
        self.fit = True
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self.item)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QColor(CODE_BG))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def has_image(self):
        return not self.item.pixmap().isNull()

    def size_of_image(self):
        pixmap = self.item.pixmap()
        return pixmap.width(), pixmap.height()

    def show_new(self, pixmap):
        self.item.setPixmap(pixmap)
        self.scene().setSceneRect(self.item.boundingRect())
        self.fit_view()

    def next_frame(self, pixmap):
        """The next frame of an animation: same size means nothing else changes."""
        changed = pixmap.size() != self.item.pixmap().size()
        self.item.setPixmap(pixmap)
        if changed:
            self.scene().setSceneRect(self.item.boundingRect())
            if self.fit:
                self.fit_view()

    def clear(self):
        self.item.setPixmap(QPixmap())
        self.fit = True
        self.on_change()

    def set_factor(self, factor, fit=False):
        self.fit = fit
        self.resetTransform()
        self.scale(factor, factor)
        self.on_change()

    def fit_view(self):
        """As big as fits in the window, but never bigger than the picture itself."""
        self.fit = True
        width, height = self.size_of_image()
        view = self.viewport().size()
        if width and view.width() > 0 and view.height() > 0:
            self.set_factor(min(view.width() / width, view.height() / height, 1.0), fit=True)

    def factor(self):
        return self.transform().m11()

    def zoom_by(self, ratio):
        if not self.has_image():
            return
        now = self.factor()
        wanted = min(32.0, max(0.02, now * ratio))
        self.fit = False
        self.scale(wanted / now, wanted / now)
        self.on_change()

    def wheelEvent(self, event):
        self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit:
            self.fit_view()

    def mouseDoubleClickEvent(self, event):
        if self.fit:
            self.set_factor(1.0)
        else:
            self.fit_view()


class Screen(QVideoWidget):
    """The video picture; a click pauses or plays."""

    def __init__(self, on_click):
        super().__init__()
        self.on_click = on_click
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() == Qt.MouseButton.LeftButton:
            self.on_click()


def build(parent, say=None):
    say = say or (lambda text: None)
    state = {"path": None, "kind": None, "error": None, "movie": None}

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

    pages = QStackedWidget()
    column.addWidget(pages, 1)
    message = label("Open a picture, video or sound file", 13, MUTED)
    message.setAlignment(Qt.AlignmentFlag.AlignCenter)
    message.setWordWrap(True)
    view = ImageView(lambda: refresh())
    screen = Screen(lambda: play_pause())
    for page in (message, view, screen):
        pages.addWidget(page)

    player = QMediaPlayer(frame)
    sound = QAudioOutput(frame)
    sound.setVolume(0.8)
    player.setAudioOutput(sound)
    player.setVideoOutput(screen)

    # the control strip under a video or sound
    strip = QWidget()
    strip.setFixedHeight(26)
    controls = QHBoxLayout(strip)
    controls.setContentsMargins(6, 0, 6, 0)
    controls.setSpacing(8)
    now_label = label("0:00", 12, MUTED)
    now_label.setMinimumWidth(40)
    seek = Seek()
    seek.setRange(0, 0)
    length_label = label("0:00", 12, MUTED)
    length_label.setMinimumWidth(40)
    mute_button = icon_button("volume", lambda: toggle_mute(), size=24, glyph_size=13)
    mute_button.setToolTip("Mute (M)")
    loudness = Seek()
    loudness.setRange(0, 100)
    loudness.setValue(80)
    loudness.setFixedWidth(80)
    for widget in (now_label, seek):
        controls.addWidget(widget, 1 if widget is seek else 0)
    for widget in (length_label, mute_button, loudness):
        controls.addWidget(widget)
    column.addWidget(strip)

    # ---- toolbar ----

    open_button = button("Open", lambda: open_file(), kind="ghost", width=58)
    previous_button = icon_button("left", lambda: step(-1), size=28, strong=True)
    previous_button.setToolTip("Previous file in the folder (Page Up)")
    next_button = icon_button("right", lambda: step(1), size=28, strong=True)
    next_button.setToolTip("Next file in the folder (Page Down)")
    play_button = icon_button("play", lambda: play_pause(), size=28, strong=True)
    play_button.setToolTip("Play / pause (Space)")
    fit_button = button("Fit", lambda: view.fit_view(), kind="toggle", width=40)
    fit_button.setToolTip("Fit the picture to the window (0)")
    actual_button = button("100%", lambda: view.set_factor(1.0), kind="ghost", width=52)
    actual_button.setToolTip("Actual size (1)")
    out_button = icon_button("zoom_out", lambda: view.zoom_by(1 / 1.25), size=28)
    out_button.setToolTip("Zoom out (-)")
    in_button = icon_button("zoom_in", lambda: view.zoom_by(1.25), size=28)
    in_button.setToolTip("Zoom in (+)")
    info_label = label("", 12, MUTED)
    chip = NameChip()
    chip.setMaximumWidth(280)
    chip_row = QHBoxLayout(chip)
    chip_row.setContentsMargins(12, 0, 12, 0)
    name_label = label("Nothing open", 12, MUTED)
    name_label.setMinimumWidth(1)                    # the file name gives way first when the window is narrow
    chip.setMinimumWidth(1)
    chip_row.addWidget(name_label)
    for widget in (open_button, previous_button, next_button, play_button, fit_button, actual_button,
                   out_button, in_button):
        toolbar.addWidget(widget)
    toolbar.addStretch(1)
    toolbar.addWidget(info_label)
    toolbar.addSpacing(4)
    toolbar.addWidget(chip)

    # ---- what shows depends on what is open ----

    def moving():
        """True for a video, sound or animated picture."""
        return state["kind"] in ("video", "audio") or state["movie"] is not None

    def playing():
        if state["kind"] in ("video", "audio"):
            return player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        movie = state["movie"]
        return movie is not None and movie.state() == QMovie.MovieState.Running

    def refresh(*args):
        kind = state["kind"]
        picture = kind == "image"
        timed = kind in ("video", "audio")
        for widget in (fit_button, actual_button, out_button, in_button):
            widget.setVisible(picture)
        play_button.setVisible(moving())
        play_button.setText(ICONS["pause" if playing() else "play"])
        strip.setVisible(timed)
        set_on(fit_button, picture and view.fit)
        path = state["path"]
        name_label.setText(os.path.basename(path) if path else "Nothing open")
        name_label.setToolTip(path or "")
        bits = []
        if picture and view.has_image():
            width, height = view.size_of_image()
            bits.append(f"{width}×{height}  {round(view.factor() * 100)}%")
        if path:
            files = siblings(path)
            if len(files) > 1:
                same = [os.path.normcase(f) for f in files]
                here = same.index(os.path.normcase(path)) + 1 if os.path.normcase(path) in same else 0
                bits.append(f"{here}/{len(files)}")
            previous_button.setEnabled(len(files) > 1)
            next_button.setEnabled(len(files) > 1)
        else:
            previous_button.setEnabled(False)
            next_button.setEnabled(False)
        info_label.setText("   ".join(bits))

    def stop_everything():
        player.stop()
        player.setSource(QUrl())
        movie = state["movie"]
        if movie is not None:
            movie.stop()
            movie.deleteLater()
            state["movie"] = None
        view.clear()
        seek.setRange(0, 0)
        now_label.setText("0:00")
        length_label.setText("0:00")

    def fail(text):
        state["error"] = text
        say(text)
        return False

    # ---- opening ----

    def show_image(path):
        reader = QImageReader(path)
        reader.setAutoTransform(True)                 # turn photos the way the camera held them
        if reader.supportsAnimation() and reader.imageCount() != 1:
            movie = QMovie(path)
            if not movie.isValid():
                return fail(f"Could not open {os.path.basename(path)}: {movie.lastErrorString() or 'unknown format'}")
            movie.frameChanged.connect(lambda n: view.next_frame(movie.currentPixmap()))
            movie.stateChanged.connect(lambda s: refresh())
            state["movie"] = movie
            movie.jumpToFrame(0)
            view.show_new(movie.currentPixmap())
            movie.start()
        else:
            image = reader.read()
            if image.isNull():
                return fail(f"Could not open {os.path.basename(path)}: {reader.errorString()}")
            view.show_new(QPixmap.fromImage(image))
        pages.setCurrentWidget(view)
        return True

    def show_player(path, kind, play):
        pages.setCurrentWidget(screen if kind == "video" else message)
        if kind == "audio":
            message.setText(os.path.basename(path) + "\n(sound)")
        player.setSource(QUrl.fromLocalFile(path))
        if play:
            player.play()
        else:
            player.pause()                            # shows the first picture without playing
        return True

    def open_file(path=None, quiet=False, play=True):
        """Show a picture or play a video / sound. quiet=True never shows a dialog (the chatbot's
        tool); a problem is reported in last_error() and False is returned."""
        state["error"] = None
        if not path:
            if quiet:
                return fail("No file given")
            start = os.path.dirname(state["path"]) if state["path"] else START
            path, _ = QFileDialog.getOpenFileName(frame, "Open", start, FILTER)
            if not path:
                return False
        path = os.path.normpath(str(path).strip().strip('"'))
        if not os.path.isfile(path):
            return fail(f"There is no such file: {path}")
        kind = kind_of(path)
        if kind is None:
            return fail(f"{os.path.basename(path)} is not a picture, video or sound file I know")
        stop_everything()
        state["path"], state["kind"] = path, kind
        ok = show_image(path) if kind == "image" else show_player(path, kind, play)
        if not ok:
            state["path"], state["kind"] = None, None
            pages.setCurrentWidget(message)
            message.setText(state["error"] or "Could not open the file")
        else:
            say("Opened " + os.path.basename(path))
        refresh()
        return ok

    def step(delta):
        """The next (or previous) media file in the same folder; it wraps around."""
        path = state["path"]
        if not path:
            return False
        files = siblings(path)
        same = [os.path.normcase(f) for f in files]
        if len(files) < 2 or os.path.normcase(path) not in same:
            return False
        return open_file(files[(same.index(os.path.normcase(path)) + delta) % len(files)])

    # ---- playing ----

    def play_pause():
        if state["kind"] in ("video", "audio"):
            if playing():
                player.pause()
            else:
                player.play()
        elif state["movie"] is not None:
            movie = state["movie"]
            movie.setPaused(playing())
        refresh()

    def seek_by(seconds):
        if state["kind"] in ("video", "audio"):
            player.setPosition(int(max(0, min(player.duration() or 0, player.position() + seconds * 1000))))

    def set_volume(percent):
        percent = max(0, min(100, int(percent)))
        sound.setVolume(percent / 100)
        loudness.setValue(percent)

    def toggle_mute():
        sound.setMuted(not sound.isMuted())
        mute_button.setText(ICONS["mute" if sound.isMuted() else "volume"])

    def on_position(ms):
        if not seek.dragging:
            seek.setValue(ms)
        now_label.setText(clock(ms))

    def on_duration(ms):
        seek.setRange(0, max(0, ms))
        length_label.setText(clock(ms))

    def on_error(error, text):
        if error != QMediaPlayer.Error.NoError:
            fail(f"Could not play {os.path.basename(state['path'] or '')}: {text}")
            pages.setCurrentWidget(message)
            message.setText(state["error"])

    player.positionChanged.connect(on_position)
    player.durationChanged.connect(on_duration)
    player.playbackStateChanged.connect(lambda s: refresh())
    player.errorOccurred.connect(on_error)
    seek.sought.connect(player.setPosition)
    loudness.sought.connect(lambda value: sound.setVolume(value / 100))

    # ---- keys ----

    def arrow(delta):
        if state["kind"] in ("video", "audio"):
            seek_by(5 * delta)
        else:
            step(delta)

    for keys, command in (("Ctrl+O", open_file), ("Space", play_pause), ("Left", lambda: arrow(-1)),
                          ("Right", lambda: arrow(1)), ("PgUp", lambda: step(-1)), ("PgDown", lambda: step(1)),
                          ("+", lambda: view.zoom_by(1.25)), ("=", lambda: view.zoom_by(1.25)),
                          ("-", lambda: view.zoom_by(1 / 1.25)), ("0", lambda: view.fit_view()),
                          ("1", lambda: view.set_factor(1.0)), ("M", toggle_mute)):
        shortcut = QShortcut(QKeySequence(keys), frame)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)   # only this viewer
        shortcut.activated.connect(command)

    # ---- for the chatbot's tools ----

    def describe():
        path, kind = state["path"], state["kind"]
        if not path:
            return "Nothing is open."
        text = f"{os.path.basename(path)} ({kind}) in {os.path.dirname(path)}"
        if kind == "image":
            width, height = view.size_of_image()
            text += f", {width}x{height} pixels, zoom {round(view.factor() * 100)}%{' (fit)' if view.fit else ''}"
            if state["movie"] is not None:
                text += ", animated, " + ("playing" if playing() else "paused")
        else:
            names = {QMediaPlayer.PlaybackState.PlayingState: "playing", QMediaPlayer.PlaybackState.PausedState: "paused",
                     QMediaPlayer.PlaybackState.StoppedState: "stopped"}
            text += (f", {names[player.playbackState()]} at {clock(player.position())} of {clock(player.duration())}"
                     f", volume {'muted' if sound.isMuted() else str(round(sound.volume() * 100)) + '%'}")
        if state["error"]:
            text += f". Problem: {state['error']}"
        return text

    def control(action, value=None):
        """One command for the chatbot. Returns a sentence about what happened; raises ValueError."""
        if not state["path"] and action not in ("volume", "mute", "unmute"):
            raise ValueError("Nothing is open in this Media Viewer")
        timed = state["kind"] in ("video", "audio")
        picture = state["kind"] == "image"
        if action in ("play", "pause", "toggle"):
            if not moving():
                raise ValueError("This is a still picture; there is nothing to play")
            if action == "toggle" or (action == "play") != playing():
                play_pause()
            return "Playing." if playing() else "Paused."
        if action in ("next", "previous"):
            if not step(1 if action == "next" else -1):
                raise ValueError(state["error"] or "There is no other media file in this folder")
            return "Now showing " + os.path.basename(state["path"]) + "."
        if action == "seek":
            if not timed:
                raise ValueError("Seeking only works in a video or sound")
            if value is None:
                raise ValueError("Give the position in seconds as 'value'")
            player.setPosition(int(max(0, min(player.duration() or 0, float(value) * 1000))))
            return f"Moved to {clock(player.position())}."
        if action == "volume":
            if value is None:
                raise ValueError("Give the volume 0-100 as 'value'")
            set_volume(float(value))
            return f"Volume is {round(sound.volume() * 100)}%."
        if action in ("mute", "unmute"):
            if sound.isMuted() != (action == "mute"):
                toggle_mute()
            return "Muted." if sound.isMuted() else "Sound on."
        if action in ("fit", "actual", "zoom_in", "zoom_out"):
            if not picture:
                raise ValueError("Zoom only works on a picture")
            if action == "fit":
                view.fit_view()
            elif action == "actual":
                view.set_factor(1.0)
            else:
                view.zoom_by(1.25 if action == "zoom_in" else 1 / 1.25)
            return f"Zoom is {round(view.factor() * 100)}%."
        raise ValueError("Unknown action: " + action)

    # ---- what main.json keeps ----

    def get_state():
        return {"path": state["path"], "volume": round(sound.volume() * 100)}

    def set_state(data):
        volume = data.get("volume")
        if isinstance(volume, (int, float)):
            set_volume(volume)
        path = data.get("path")
        if isinstance(path, str) and os.path.isfile(path):
            open_file(path, play=False)               # a restart never starts a video by itself

    def shutdown():
        stop_everything()

    refresh()
    return {"frame": frame, "toolbar": bar, "view": view, "player": player, "screen": screen, "pages": pages,
            "audio": sound, "seek": seek, "strip": strip, "play_button": play_button, "open": open_file,
            "step": step, "control": control, "describe": describe, "get_path": lambda: state["path"],
            "get_kind": lambda: state["kind"], "get_state": get_state, "set_state": set_state,
            "shutdown": shutdown, "can_close": lambda: True, "last_error": lambda: state["error"],
            "play_pause": play_pause, "set_volume": set_volume, "message": message}


# --------------------------------------------------------------------------
# Tools for the chatbot
# --------------------------------------------------------------------------

def tools(api):
    window_prop = {"window": {"type": "string", "description": "Media Viewer window id or title; may be left out when only one is open"}}

    def pick(args):
        return api["find_window"](args.get("window"), "media_viewer")

    def open_confirm(args):
        return f"Show {os.path.normpath(text_arg(args, 'path').strip())} in the Media Viewer"

    def open_it(args):
        inst = pick(args)
        path = text_arg(args, "path").strip().strip('"')
        play = args.get("play", True) is not False
        if not inst["app"]["open"](path, quiet=True, play=play):
            raise ValueError(inst["app"]["last_error"]() or "could not open it")
        return f"{inst['title']}: {inst['app']['describe']()}"

    def info(args):
        inst = pick(args)
        return f"{inst['title']}: {inst['app']['describe']()}"

    def run(args):
        inst = pick(args)
        action = text_arg(args, "action").strip().lower()
        value = args.get("value")
        return f"{inst['title']}: {inst['app']['control'](action, value)}"

    return [
        tool("media_open", "Show a picture, or play a video or sound file, in a Media Viewer window (asks Martin first).",
             {**window_prop, "path": {"type": "string", "description": "full path of the file"},
              "play": {"type": "boolean", "description": "start playing a video or sound (default true)"}},
             ["path"], open_it, open_confirm),
        tool("media_info", "Tell what a Media Viewer window shows: the file, size, zoom, or play position and volume.",
             window_prop, run=info),
        tool("media_control", "Control a Media Viewer window: play, pause, toggle, next or previous file in the folder, "
                              "seek (value = seconds), volume (value = 0-100), mute, unmute, fit, actual (100%), zoom_in, zoom_out.",
             {**window_prop, "action": {"type": "string", "enum": ["play", "pause", "toggle", "next", "previous", "seek", "volume",
                                                                    "mute", "unmute", "fit", "actual", "zoom_in", "zoom_out"]},
              "value": {"type": "number", "description": "seconds for seek, 0-100 for volume"}},
             ["action"], run),
    ]

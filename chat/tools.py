"""The tools the chatbot can use: everything main.py and the apps can do.

    collect(api) -> list of tools

main.py hands in `api` (its own functions). The tools for the main window live here; every app in
apps/ can bring its own by defining `tools(api)` that returns a list of tool() dicts (apps added
later show up by themselves). A tool is a dict made by tool():

    name, description, properties, required    what the model sees
    run(args)       does it, on the window's thread. Returns the result text, or a function that
                    is called again and again until it returns the text (None = not yet), for
                    things that take a while, like a terminal command
    confirm(args)   returns a sentence describing what is about to happen when Martin must say
                    yes first (running commands, opening or saving files), else None

Errors meant for the model are raised as ValueError("message").
"""
import importlib
import json
import time

CLIP = 6000


def tool(name, description, properties=None, required=None, run=None, confirm=None, timeout=60):
    return {"name": name, "description": description, "properties": properties or {},
            "required": required or [], "run": run, "confirm": confirm, "timeout": timeout}


def schema(t):
    """What the model is told about a tool."""
    return {"type": "function", "function": {
        "name": t["name"], "description": t["description"],
        "parameters": {"type": "object", "properties": t["properties"], "required": t["required"]}}}


def text_arg(args, key, default=""):
    value = args.get(key, default)
    return default if value is None else str(value)


def int_arg(args, key, default=None, low=None, high=None):
    value = args.get(key, default)
    if value is None or value == "":
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        raise ValueError(f"'{key}' must be a whole number, not {value!r}")
    if low is not None and number < low:
        raise ValueError(f"'{key}' must be at least {low}")
    if high is not None and number > high:
        raise ValueError(f"'{key}' must be at most {high}")
    return number


def bool_arg(args, key, default=False):
    value = args.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


def clip(text, limit=CLIP):
    text = str(text)
    return text if len(text) <= limit else text[:limit] + f"\n... (cut: {len(text) - limit} more characters)"


def call(t, args):
    """Run a tool. Returns the result text, or a function to poll for it. Never raises."""
    args = args if isinstance(args, dict) else {}
    missing = [k for k in t["required"] if args.get(k) in (None, "")]
    if missing:
        return "Error: missing " + ", ".join(missing)
    try:
        return t["run"](args)
    except ValueError as err:
        return "Error: " + str(err)
    except Exception as err:                      # a bug in a tool must not stop the chat
        return f"Error: {type(err).__name__}: {err}"


def needs_ok(t, args):
    """A sentence describing what needs Martin's approval, or None if the tool can just run."""
    if not t.get("confirm"):
        return None
    try:
        return t["confirm"](args if isinstance(args, dict) else {})
    except ValueError:
        return None                               # run() will report the same problem to the model
    except Exception:
        return "Run " + t["name"]                 # if in doubt, ask


def summary(name, args, limit=70):
    """A one-line description of a call for the chat: name(arg=value, ...)."""
    shown = ", ".join(f"{k}={str(v)[:30]!r}" if isinstance(v, str) else f"{k}={v}" for k, v in (args or {}).items())
    line = f"{name}({shown})"
    return line if len(line) <= limit else line[:limit - 1] + "…"


# ---- the main window's own tools ----

def main_tools(api):
    LAYOUTS = api["LAYOUTS"]
    pattern_names = {k: k for k in LAYOUTS}
    pattern_names.update({label.lower(): k for k, label in LAYOUTS.items()})
    window_prop = {"window": {"type": "string", "description": "the window id (from list_windows) or its title; may be left out when only one such window is open"}}

    def list_windows(args):
        return json.dumps(api["describe_windows"](), ensure_ascii=False)

    def list_apps(args):
        return json.dumps([{"app": n, "title": api["app_title"](n)} for n in api["app_names"]()])

    def system_status(args):
        status = api["status"]
        return json.dumps({"battery": status["battery"], "wifi": status["wifi"], "volume": status["volume"],
                           "time": time.strftime("%A %d %B %Y %H:%M:%S")})

    def open_app(args):
        name = text_arg(args, "app").strip().lower().replace(" ", "_")
        names = api["app_names"]()
        if name not in names:
            raise ValueError(f"There is no app '{name}'. Apps: {', '.join(names)}")
        inst = api["open_app"](name)
        if inst is None:
            raise ValueError(f"{api['app_title'](name)} could not be opened (see the status bar).")
        return f"Opened {inst['title']} (window id {inst['id']}) in {inst['tab']['title']}."

    def dirty(inst):
        check = inst["app"].get("is_dirty")
        return bool(check and check())

    def close_window_confirm(args):
        inst = api["find_window"](args.get("window"))
        if dirty(inst) and bool_arg(args, "discard_changes"):
            return f"Close {inst['title']} and throw away its unsaved changes"
        return None

    def close_window(args):
        inst = api["find_window"](args.get("window"))
        if dirty(inst):
            if not bool_arg(args, "discard_changes"):
                raise ValueError(f"{inst['title']} has unsaved changes. Save it first, or call again with discard_changes=true.")
            mark = inst["app"].get("mark_clean")
            if mark:
                mark()
        title = inst["title"]
        api["close_app"](inst)
        if inst in inst["tab"]["instances"]:
            raise ValueError(f"{title} was not closed (the app asked something and the answer was no).")
        return f"Closed {title}."

    def set_pattern(args):
        kind = pattern_names.get(text_arg(args, "pattern").strip().lower())
        if kind is None:
            raise ValueError(f"Unknown pattern. Choose one of: {', '.join(LAYOUTS)}")
        tab = api["find_tab"](args.get("tab"))
        api["set_pattern"](tab, kind)
        return f"{tab['title']} is now arranged as '{kind}' ({LAYOUTS[kind]})."

    def resize_window(args):
        change = int_arg(args, "change", None, -5, 5)
        if not change:
            raise ValueError("'change' must be a whole number from -5 to 5, not 0 (positive gives the window more space).")
        inst = api["find_window"](args.get("window"))
        before = inst["size"]
        for _ in range(abs(change)):
            api["resize"](inst, 1 if change > 0 else -1)
        return f"{inst['title']} space went from {before} to {inst['size']} (1 to {api['MAX_SIZE']})."

    def move_window(args):
        inst = api["find_window"](args.get("window"))
        position = int_arg(args, "position", None, 1, 50)
        if position is None:
            raise ValueError("'position' is missing: the place number to move the window to (1 = first).")
        other = api["move_window"](inst, position)
        if other is None:
            return f"{inst['title']} is already in place {position}."
        return f"{inst['title']} is now in place {position}; {other['title']} took its old place."

    def rename_window(args):
        name = text_arg(args, "name").strip()[:40]
        if not name:
            raise ValueError("'name' is empty")
        inst = api["find_window"](args.get("window"))
        old = inst["title"]
        api["set_window_title"](inst, name)
        return f"Renamed window {inst['id']} from '{old}' to '{name}'."

    def rename_tab(args):
        name = text_arg(args, "name").strip()[:40]
        if not name:
            raise ValueError("'name' is empty")
        tab = api["find_tab"](args.get("tab"))
        old = tab["title"]
        api["set_tab_title"](tab, name)
        return f"Renamed tab '{old}' to '{name}'."

    def new_tab(args):
        tab = api["new_tab"]()
        name = text_arg(args, "name").strip()[:40]
        if name:
            api["set_tab_title"](tab, name)
        return f"Opened tab '{tab['title']}' and switched to it."

    def switch_tab(args):
        tab = api["find_tab"](args.get("tab"))
        api["show_tab"](tab)
        return f"Showing {tab['title']}."

    def tab_dirty(tab):
        return [i["title"] for i in tab["instances"] if dirty(i)]

    def close_tab_confirm(args):
        tab = api["find_tab"](args.get("tab"))
        n = len(tab["instances"])
        return f"Close the tab '{tab['title']}' and its {n} window{'s' if n != 1 else ''}"

    def close_tab(args):
        tab = api["find_tab"](args.get("tab"))
        unsaved = tab_dirty(tab)
        if unsaved and not bool_arg(args, "discard_changes"):
            raise ValueError(f"These windows have unsaved changes: {', '.join(unsaved)}. Save them first, or call again with discard_changes=true.")
        for inst in tab["instances"]:
            mark = inst["app"].get("mark_clean")
            if mark and dirty(inst):
                mark()
        title = tab["title"]
        api["close_tab"](tab)
        if tab in api["tabs"]:
            raise ValueError(f"The tab '{title}' was not closed.")
        return f"Closed the tab '{title}'."

    def show_message(args):
        text = text_arg(args, "text").strip()
        if not text:
            raise ValueError("'text' is empty")
        api["say"](text[:200])
        return "Shown in the status bar."

    tab_prop = {"tab": {"type": "string", "description": "the tab's name or number (1 = leftmost); leave out for the tab being shown"}}
    return [
        tool("list_windows", "List the tabs and the app windows that are open now, with each window's id, title, app, size and place (its position in the tab's layout), and each tab's layout pattern. Call this first when you need to know what is on screen.", run=list_windows),
        tool("list_apps", "List the apps that can be opened (name and title).", run=list_apps),
        tool("system_status", "Battery level, Wi-Fi network, speaker volume and the date and time of this PC.", run=system_status),
        tool("open_app", "Open a new window of an app in the tab being shown. Returns the new window's id. Opening the same app again opens another window.",
             {"app": {"type": "string", "description": "the app name from list_apps, e.g. notepad, terminal, ppt_viewer"}}, ["app"], open_app),
        tool("close_window", "Close an app window. Refuses if the window has unsaved changes unless discard_changes is true.",
             {**window_prop, "discard_changes": {"type": "boolean", "description": "throw away unsaved changes (asks Martin first)"}},
             run=close_window, confirm=close_window_confirm),
        tool("set_pattern", "Arrange all windows of a tab in a pattern: grid (as square as possible), columns (side by side), rows (stacked) or main (the first window big on the left, the others stacked on the right).",
             {"pattern": {"type": "string", "enum": list(LAYOUTS)}, **tab_prop}, ["pattern"], set_pattern),
        tool("resize_window", "Give a window more or less of the space in its tab. change is -5..5: positive grows it, negative shrinks it (each step is one press of the + or - button; the size goes from 1 to 6).",
             {**window_prop, "change": {"type": "integer"}}, ["change"], resize_window),
        tool("move_window", "Move a window to another place in its tab: it swaps places with the window that is there now. position is the place number (1 = first, the order list_windows shows; in the 'main' pattern place 1 is the big one). Martin can also drag a window by its title.",
             {**window_prop, "position": {"type": "integer"}}, ["position"], move_window),
        tool("rename_window", "Change the title shown at the top of an app window.", {**window_prop, "name": {"type": "string"}}, ["name"], rename_window),
        tool("rename_tab", "Change a tab's name.", {**tab_prop, "name": {"type": "string"}}, ["name"], rename_tab),
        tool("new_tab", "Add a new empty tab (to the right of the others) and switch to it.", {"name": {"type": "string", "description": "optional name for the tab"}}, run=new_tab),
        tool("switch_tab", "Switch to another tab so its windows are shown.", tab_prop, ["tab"], switch_tab),
        tool("close_tab", "Close a tab and every window in it. Asks Martin first. Refuses if a window has unsaved changes unless discard_changes is true.",
             {**tab_prop, "discard_changes": {"type": "boolean"}}, ["tab"], close_tab, close_tab_confirm),
        tool("show_message", "Show a short message in the status bar at the top of the window.", {"text": {"type": "string"}}, ["text"], show_message),
    ]


def collect(api):
    """Every tool: the main window's, then the ones each app in the apps folder brings."""
    tools = main_tools(api)
    taken = {t["name"] for t in tools}
    for name in api["app_names"]():
        try:
            module = importlib.import_module("apps." + name)
            found = module.tools(api) if hasattr(module, "tools") else []
        except Exception:
            continue                              # a broken app must not take the chat down
        for t in found:
            if t["name"] not in taken:
                tools.append(t)
                taken.add(t["name"])
    return tools

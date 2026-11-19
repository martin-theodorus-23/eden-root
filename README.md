# Eden Root v3

**A complete dynamic workspace.**

One desktop window for the things you switch apps for: viewing and editing files, browsing with separate profiles, a real Edge for video, SSH servers, a terminal, and a floating chatbot that can use all of it. Python with PyQt6, dark theme, Windows 11.

## What it does

| Part | What it does |
|------|--------------|
| `main.py` | The main window and its look (colours, fonts, icons, buttons, popup menus, animation). A status bar on top (clock, Wi-Fi, volume, battery), browser-style tabs you can rename, and an **Apps** menu that opens as a grid of icon tiles. App windows are arranged in a workspace: grid, side by side, stacked, or main + stack, each resizable in steps. **Drag a window by its title onto another to swap places.** The clock reads `year:month:date:hour:min:sec:day`; hover it for a month calendar and a world clock. Scroll over the volume icon to change the volume. The battery icon turns from green through amber to red as the charge falls. |
| `apps/notepad.py` | Text editor with a **View** dropdown (text, rendered Markdown, side by side, CSV table) and a **Language** dropdown for code colours. |
| `apps/media_viewer.py` | Pictures (including animated GIF) with zoom and drag; video and sound through Qt Multimedia with Windows' own codecs, so H.264/AAC play. |
| `apps/pdf_viewer.py` | Qt PDF: scroll, page jump, zoom, search, password-protected files. |
| `apps/ppt_viewer.py` | PowerPoint slides, thumbnails, notes. Needs a separate renderer that is not part of this repository (set `EDEN_PPT_RENDERER` to its `renderer.py`); without it the viewer says so. |
| `apps/terminal.py` | A PowerShell terminal. |
| `apps/browser.py` | Chromium through Qt WebEngine: address bar that also searches, tabs, and named profiles that each keep their own logins. |
| `apps/edge.py` | The **real Microsoft Edge** inside a pane, separate from the Browser. Edge uses Windows' codecs, so H.264 video and live streams play. It has its own profile folder in `%APPDATA%\Eden\Eden Root\edge`. Needs Edge installed. |
| `apps/files.py` | The drives, folders and files of this PC as a tree: path box, hidden-files switch, right-click menu (open in a viewer, Notepad or a terminal, show in Explorer, copy path, new folder, rename). It never deletes. |
| `apps/servers.py` | The `Host` entries of `~/.ssh/config` as a tree of remote folders over SFTP, read-only plus Download. A status bar shows the connection, what is selected and how many servers are connected. Connects only when you open a server, and only to servers already in `known_hosts`. |
| `chat/` | The floating chatbot (round button, bottom right). It talks to models served by [Ollama](https://ollama.com) on your PC, and can use everything the window and the apps can do, asking first before running commands or opening and saving files. |

A new file in `apps/` shows up in the Apps menu without a restart. Each app is a file with `build(parent, say)` and an optional `tools(api)`.

`main.json` is written next to the program and keeps the window layout and each tab's windows. `chat/db.json` keeps the chat. Both are private; delete `main.json` for a fresh start.

## Run it

```
pip install -r requirements.txt
python main.py
```

Requires Python 3.13 and Windows 11. The four PyQt6 packages must stay at matching versions, or the Browser app crashes the moment a web view opens.

## Build the program

```
pip install pyinstaller
pyinstaller EdenRoot.spec --noconfirm --distpath release --workpath build
```

That makes `release/EdenRoot/EdenRoot.exe`, a folder you keep together. The packaged program keeps `main.json` and `db.json` next to itself. Ollama and Microsoft Edge are not included: the chat needs Ollama running on the PC, and the Edge app needs Edge installed.

## Known limits

- The Browser app is Chromium through Qt, which ships without H.264/AAC: many videos and live streams will not play there. Use the Edge app, or the Browser's "open in default browser" button.
- The Edge app is Edge's own window placed in a pane. A real window inside a pane is drawn above Eden Root's own overlays, so a window you drag across it passes behind it.
- No Chrome extensions in the Browser app. Google sign-in may be blocked in it.
- Windows only.

## Licence

Eden Root uses PyQt6, which is licensed under the GPL v3 (or commercially from Riverbank). This project is therefore released under the **GNU GPL v3**: see `LICENSE`.

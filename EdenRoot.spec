# Builds Eden Root as a Windows program (a folder with EdenRoot.exe in it).
#
#     pip install pyinstaller
#     pyinstaller EdenRoot.spec --noconfirm --distpath release --workpath build
#
# The result is release/EdenRoot/EdenRoot.exe (keep the folder together; zip it to hand it on).
# EdenRoot.exe keeps main.json and db.json (your window layout and chats) next to itself.
from pathlib import Path

# apps are found by listing the apps folder at run time and imported by name, so PyInstaller cannot see them:
# ship the files (for the listing) and name the modules (for the import)
apps = sorted(p.stem for p in Path("apps").glob("*.py") if not p.name.startswith("_"))

a = Analysis(
    ["main.py"],
    pathex=["."],
    datas=[("apps/*.py", "apps")],
    hiddenimports=["apps." + name for name in apps] + ["chat.core", "chat.tools", "chat.ui",
                   "psutil", "comtypes", "comtypes.client", "pycaw", "pycaw.pycaw", "paramiko"],
    excludes=["tkinter", "matplotlib", "numpy", "scipy", "pandas", "PyQt5", "PySide2", "PySide6"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="EdenRoot", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="EdenRoot", upx=False)

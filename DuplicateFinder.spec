# -*- mode: python ; coding: utf-8 -*-
"""Windowed onedir build. PyInstaller defines SPECPATH before executing this file."""

import sys

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

sys.path.insert(0, SPECPATH)
from duplicate_finder import __version__

_parts = [int(part) for part in __version__.split(".")]
if not 1 <= len(_parts) <= 4:
    raise ValueError(f"version must have 1 to 4 numeric parts, got {__version__!r}")
_version = tuple(_parts + [0] * (4 - len(_parts)))

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_version, prodvers=_version),
    kids=[
        StringFileInfo([
            StringTable(
                "040904B0",
                [
                    StringStruct("FileDescription", "Duplicate Finder"),
                    StringStruct("FileVersion", __version__),
                    StringStruct("InternalName", "DuplicateFinder"),
                    StringStruct("OriginalFilename", "DuplicateFinder.exe"),
                    StringStruct("ProductName", "Duplicate Finder"),
                    StringStruct("ProductVersion", __version__),
                ],
            )
        ]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(
    ["main.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=[],
    hiddenimports=[
        "duplicate_finder.ui.app",
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "tkinter.font",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DuplicateFinder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    version=version_info,
    manifest="DuplicateFinder.manifest",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DuplicateFinder",
)

"""Windows helpers shared by the tests."""

import ctypes
import os
import subprocess
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_INVALID_HANDLE = wintypes.HANDLE(-1).value


def write(path: Path, data: bytes = b"data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def add_attributes(path: Path, attributes: int) -> None:
    current = os.stat(path).st_file_attributes
    if not ctypes.windll.kernel32.SetFileAttributesW(str(path), current | attributes):
        raise ctypes.WinError()


def make_junction(link: Path, target: Path) -> None:
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, capture_output=True)


@contextmanager
def exclusive_lock(path: Path):
    handle = _kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        _kernel32.CloseHandle(handle)

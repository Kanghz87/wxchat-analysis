import ctypes
from ctypes import wintypes
import os
from pathlib import Path

import psutil

VERIFIED_VERSION = "4.1.13.65"


def weixin_processes() -> list[psutil.Process]:
    result = []
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info["name"] or "").lower() == "weixin.exe":
                result.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return result


def file_version(path: Path) -> str | None:
    if os.name != "nt":
        return None
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    version.VerQueryValueW.restype = wintypes.BOOL
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
        return None
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 16:
        return None
    fields = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    if fields[0] != 0xFEEF04BD:
        return None
    ms, ls = fields[2], fields[3]
    return f"{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}"


def detect_version() -> str | None:
    for process in weixin_processes():
        try:
            result = file_version(Path(process.exe()))
            if result:
                return result
        except (OSError, psutil.Error):
            continue
    return None

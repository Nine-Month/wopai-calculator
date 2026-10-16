"""Best-effort Windows acrylic / blur; silent no-op elsewhere."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


def _set_accent(hwnd: int, accent_state: int, opacity_byte: int = 230) -> bool:
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32

        class ACCENTPOLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINCOMPATTRDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(ACCENTPOLICY)),
                ("SizeOfData", ctypes.c_size_t),
            ]

        accent = ACCENTPOLICY()
        accent.AccentState = int(accent_state)
        accent.AccentFlags = 2
        # ABGR — alpha in high byte
        accent.GradientColor = (int(opacity_byte) << 24) | 0x001C1C1F
        data = WINCOMPATTRDATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        ok = user32.SetWindowCompositionAttribute(int(hwnd), ctypes.byref(data))
        return bool(ok)
    except Exception:
        return False


def try_enable_acrylic(hwnd: int, opacity_byte: int = 180) -> bool:
    """ACCENT_ENABLE_ACRYLICBLURBEHIND = 4."""
    return _set_accent(hwnd, 4, opacity_byte)


def try_disable_acrylic(hwnd: int) -> bool:
    """ACCENT_DISABLED = 0 — solid, cheap to move for DWM."""
    return _set_accent(hwnd, 0, 255)


def try_solid_accent(hwnd: int, opacity_byte: int = 255) -> bool:
    """ACCENT_ENABLE_GRADIENT = 1 — opaque tint, no blur."""
    return _set_accent(hwnd, 1, opacity_byte)

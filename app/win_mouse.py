"""Windows mouse simulation via SendInput / SetCursorPos (logical screen coords)."""
from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes
from typing import Iterable

log = logging.getLogger("pf.mouse")

# --- Win32 constants ---
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
SW_RESTORE = 9

user32 = None
if sys.platform == "win32":
    user32 = ctypes.windll.user32


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _ensure_win() -> None:
    if user32 is None:
        raise RuntimeError("鼠标拖放仅支持 Windows")


def get_cursor_pos() -> tuple[int, int]:
    _ensure_win()
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def set_cursor_pos(x: int, y: int) -> None:
    _ensure_win()
    user32.SetCursorPos(int(x), int(y))


def _send_mouse(flags: int, dx: int = 0, dy: int = 0) -> None:
    _ensure_win()
    extra = ctypes.c_ulong(0)
    mi = MOUSEINPUT(dx, dy, 0, flags, 0, ctypes.pointer(extra))
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = mi
    n = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if n != 1:
        raise RuntimeError(f"SendInput failed (ret={n})")


def left_down() -> None:
    _send_mouse(MOUSEEVENTF_LEFTDOWN)


def left_up() -> None:
    _send_mouse(MOUSEEVENTF_LEFTUP)


def click(x: int, y: int, *, settle_ms: float = 0.04) -> None:
    set_cursor_pos(x, y)
    time.sleep(settle_ms)
    left_down()
    time.sleep(0.03)
    left_up()


def move_smooth(x0: int, y0: int, x1: int, y1: int, *, steps: int = 18, step_ms: float = 0.012) -> None:
    steps = max(2, int(steps))
    for i in range(1, steps + 1):
        t = i / steps
        x = int(round(x0 + (x1 - x0) * t))
        y = int(round(y0 + (y1 - y0) * t))
        set_cursor_pos(x, y)
        time.sleep(step_ms)


def drag(
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    steps: int = 22,
    step_ms: float = 0.014,
    hold_ms: float = 0.05,
    settle_ms: float = 0.05,
) -> None:
    """Press at (x0,y0), drag to (x1,y1), release. Uses logical screen coords."""
    set_cursor_pos(x0, y0)
    time.sleep(settle_ms)
    left_down()
    time.sleep(hold_ms)
    move_smooth(x0, y0, x1, y1, steps=steps, step_ms=step_ms)
    time.sleep(hold_ms)
    left_up()
    time.sleep(settle_ms)


def bring_window_to_front(title_substrings: Iterable[str]) -> bool:
    """Best-effort: find a visible top-level window whose title contains any substring."""
    _ensure_win()
    needles = [s.lower() for s in title_substrings if s]
    if not needles:
        return False
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        low = title.lower()
        if any(n in low for n in needles):
            found.append((hwnd, title))
        return True

    user32.EnumWindows(_enum, 0)
    if not found:
        log.info("bring_front: no window matching %s", needles)
        return False
    hwnd, title = found[0]
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        log.info("bring_front: %s", title[:80])
        time.sleep(0.15)
        return True
    except Exception:
        log.exception("bring_front failed")
        return False


def execute_moves(
    moves: list[dict],
    *,
    between_ms: float = 0.06,
    progress=None,
) -> tuple[bool, str]:
    """
    Execute a list of mouse actions.
    Each move: {action:'drag'|'click', from:{x,y}, to:{x,y}?} or {x,y} for click.
    """
    if not moves:
        return False, "没有可执行的拖放步骤"
    try:
        _ensure_win()
    except Exception as e:
        return False, str(e)

    bring_window_to_front(
        [
            "pacificfront",
            "pacific front",
            "world of warships",
            "太平洋前线",
            "chrome",
            "edge",
            "msedge",
        ]
    )

    done = 0
    for i, m in enumerate(moves):
        if not isinstance(m, dict):
            continue
        action = str(m.get("action") or "drag").lower()
        try:
            if action == "click":
                pt = m.get("at") or m.get("from") or m
                x = int(pt.get("x"))
                y = int(pt.get("y"))
                if progress:
                    progress(f"点击清空 {i+1}/{len(moves)}")
                click(x, y)
            else:
                fr = m.get("from") or {}
                to = m.get("to") or {}
                x0, y0 = int(fr["x"]), int(fr["y"])
                x1, y1 = int(to["x"]), int(to["y"])
                name = m.get("name") or m.get("code") or ""
                if progress:
                    progress(f"拖放 {i+1}/{len(moves)}" + (f" · {name}" if name else ""))
                drag(x0, y0, x1, y1)
            done += 1
            time.sleep(between_ms)
        except Exception as e:
            log.exception("move failed i=%s", i)
            return False, f"第 {i+1} 步失败: {e}（已完成 {done}）"
    return True, f"已完成 {done} 步鼠标操作"

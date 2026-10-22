"""Twelfth pass: the third-party transparent overlays.

A recon agent found a top-level window with class

    Cua.AgentCursorOverlay   'Cua.AgentCursorOverlay.default'
    exstyle = 0x08080A0
    style   = 0x94000000

Decoding that:
    exstyle 0x08080A0 = WS_EX_NOACTIVATE | WS_EX_LAYERED
                        | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT
    style   0x94000000 = WS_POPUP | WS_VISIBLE | WS_CLIPSIBLING

That is a transparent, layered, click-through, always-visible overlay
window -- exactly the shape of thing that gets promoted to a hardware
overlay plane and drawn OVER the display's cursor plane.

Its owner is the trycua cua-driver (the computer-use driver behind the
Windows-MCP / Codex setup). There is also a full-screen
CodexComputerUseSwiftOverlay ("ChatGPT is using your computer").

This measures both: geometry, visibility, owner, and -- crucially --
whether the pointer position at sample time falls inside them.

Read-only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_VISIBLE = 0x10000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
DWMWA_CLOAKED = 14


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_long
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.GetShellWindow.argtypes = []
user32.GetShellWindow.restype = wt.HWND
user32.GetDesktopWindow.argtypes = []
user32.GetDesktopWindow.restype = wt.HWND

try:
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    dwmapi.DwmGetWindowAttribute.argtypes = [
        wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD
    ]
    dwmapi.DwmGetWindowAttribute.restype = wt.LONG
except OSError:
    dwmapi = None


def class_of(h) -> str:
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def title_of(h) -> str:
    b = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(h, b, 512)
    return b.value


def rect_of(h):
    r = RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def pid_of(h) -> int:
    p = wt.DWORD(0)
    user32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def exstyle_of(h) -> int:
    return user32.GetWindowLongPtrW(h, GWL_EXSTYLE)


def style_of(h) -> int:
    return user32.GetWindowLongPtrW(h, GWL_STYLE)


def is_cloaked(h) -> bool:
    if dwmapi is None:
        return False
    v = wt.DWORD(0)
    hr = dwmapi.DwmGetWindowAttribute(h, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
    return hr == 0 and v.value != 0


def describe(h) -> None:
    r = rect_of(h)
    st = style_of(h)
    ex = exstyle_of(h)
    pid = pid_of(h)
    vis = bool(user32.IsWindowVisible(h))
    flags = []
    for bit, name in (
        (WS_EX_LAYERED, "LAYERED"),
        (WS_EX_TRANSPARENT, "TRANSPARENT"),
        (WS_EX_TOOLWINDOW, "TOOLWINDOW"),
        (WS_EX_NOACTIVATE, "NOACTIVATE"),
        (WS_EX_TOPMOST, "TOPMOST"),
    ):
        if ex & bit:
            flags.append(name)
    print(f"  hwnd 0x{h:X}  class='{class_of(h)}'  title='{title_of(h)}'")
    print(f"    rect      : {r}  ({r[2]-r[0]}x{r[3]-r[1]})")
    print(f"    pid       : {pid}")
    print(f"    visible   : {vis}   WS_VISIBLE={'set' if st & WS_VISIBLE else 'not set'}")
    print(f"    cloaked   : {is_cloaked(h)}")
    print(f"    exstyle   : 0x{ex:08X}  [{', '.join(flags)}]")
    print(f"    style     : 0x{st:08X}")


def main() -> int:
    pos = POINT()
    user32.GetCursorPos(ctypes.byref(pos))
    print(f"Pointer currently at ({pos.x}, {pos.y})")
    print()

    print("=" * 78)
    print("A. EVERY TOP-LEVEL WINDOW WITH A LAYERED / TRANSPARENT / OVERLAY STYLE")
    print("=" * 78)
    hits = []

    def cb(h, _l):
        ex = exstyle_of(h)
        st = style_of(h)
        cls = class_of(h)
        interesting = (
            (ex & (WS_EX_LAYERED | WS_EX_TRANSPARENT))
            or "Overlay" in cls
            or "Cursor" in cls
            or "cua" in cls.lower()
            or "CodexComputerUse" in cls
        )
        if interesting:
            hits.append(h)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    for h in hits:
        describe(h)
        print()

    print("=" * 78)
    print("B. WINDOWS OWNED BY THE COMPUTER-USE DRIVERS")
    print("=" * 78)
    driver_windows = []

    def cb2(h, _l):
        pid = pid_of(h)
        try:
            import subprocess

            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
        except Exception:
            out = ""
        low = out.lower()
        if "cua-driver" in low or "codex-computer-use" in low:
            driver_windows.append((h, out.strip()))
        return True

    user32.EnumWindows(EnumWindowsProc(cb2), 0)
    if not driver_windows:
        print("  (no top-level windows owned by cua-driver / codex-computer-use)")
    for h, who in driver_windows:
        describe(h)
        print(f"    owner     : {who}")
        print()

    print("=" * 78)
    print("C. IS THE POINTER UNDER ANY OVERLAY RIGHT NOW?")
    print("=" * 78)
    for h in hits:
        r = rect_of(h)
        inside = r[0] <= pos.x < r[2] and r[1] <= pos.y < r[3]
        print(
            f"  0x{h:X} '{class_of(h)}' {r} -> pointer {'INSIDE' if inside else 'outside'}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Third pass: the two decisive questions.

1. Is the system using a HARDWARE cursor or a SOFTWARE cursor?
   SM_CURSORS (13) reports the number of hardware cursors the display
   driver supports. If it is 0, Windows composites the pointer into the
   framebuffer (software cursor) - and then any app that repaints the
   region under the pointer ERASES it, which is exactly the "pointer
   vanishes over some surfaces" symptom.

2. What is under the pointer right now, and does the window chain
   contain anything that swallows the cursor?

Read-only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)

SM_CURSORS = 13
SM_REMOTESESSION = 0x1000


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class CURSORINFO(ctypes.Structure):
    # The real Win32 CURSORINFO is {DWORD cbSize; DWORD flags; HCURSOR
    # hCursor; POINT ptScreenPos;} -- there is no hScreen field. On x64 that
    # is 4+4+8+8 = 24 bytes. Passing 32 makes the call fail with
    # ERROR_INVALID_PARAMETER (87).
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hCursor", wt.HANDLE),
        ("ptScreenPos", POINT),
    ]


def get_cursor_info_variants() -> None:
    print("  -- variant 1: struct argtypes --")
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetCursorInfo.restype = wt.BOOL
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    ret = user32.GetCursorInfo(ctypes.byref(info))
    print(f"     ret={ret} flags=0x{info.flags:08x} pos=({info.ptScreenPos.x},{info.ptScreenPos.y})")
    if ret:
        print(f"     SHOWING={bool(info.flags & 1)} SUPPRESSED={bool(info.flags & 2)}")
        return

    print("  -- variant 2: raw buffer --")
    buf = ctypes.create_string_buffer(32)
    ctypes.memmove(buf, ctypes.c_ulong(32), 4)
    user32.GetCursorInfo.argtypes = [ctypes.c_void_p]
    user32.GetCursorInfo.restype = wt.BOOL
    ret = user32.GetCursorInfo(buf)
    flags = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong))[1]
    print(f"     ret={ret} flags=0x{flags:08x}")

    print("  -- variant 3: via ctypes.windll --")
    import ctypes as c

    i2 = CURSORINFO()
    i2.cbSize = ctypes.sizeof(CURSORINFO)
    try:
        ret = c.windll.user32.GetCursorInfo(c.byref(i2))
        print(f"     ret={ret} flags=0x{i2.flags:08x} pos=({i2.ptScreenPos.x},{i2.ptScreenPos.y})")
        if ret:
            print(f"     SHOWING={bool(i2.flags & 1)} SUPPRESSED={bool(i2.flags & 2)}")
            print(f"     hCursor={i2.hCursor}")
    except Exception as exc:
        print(f"     error: {exc}")

    print(f"  -- sizeof(CURSORINFO) = {ctypes.sizeof(CURSORINFO)} (must be 24) --")


def main() -> int:
    print("=" * 72)
    print("A. HARDWARE CURSOR SUPPORT  (the decisive one)")
    print("=" * 72)
    n = user32.GetSystemMetrics(SM_CURSORS)
    print(f"  SM_CURSORS (13) = {n}")
    print(f"  SM_REMOTESESSION = {user32.GetSystemMetrics(SM_REMOTESESSION)}")
    if n == 0:
        print("  => 0 hardware cursors: Windows composites the pointer into the")
        print("     framebuffer (SOFTWARE cursor). A software cursor is erased by")
        print("     anything that repaints the region under it.")
    else:
        print(f"  => {n} hardware cursors available: the pointer lives on a separate")
        print("     hardware plane and is composited by the display driver.")

    print()
    print("=" * 72)
    print("B. CURSOR INFO")
    print("=" * 72)
    get_cursor_info_variants()

    print()
    print("=" * 72)
    print("C. WHAT IS UNDER THE POINTER RIGHT NOW")
    print("=" * 72)
    pos = POINT()
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wt.BOOL
    user32.GetCursorPos(ctypes.byref(pos))
    print(f"  cursor at ({pos.x}, {pos.y})")

    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]

    hwnd = user32.WindowFromPoint(pos)
    if not hwnd:
        print("  WindowFromPoint returned nothing (pointer over the desktop)")
    else:
        GA_ROOT = 2
        root = user32.GetAncestor(hwnd, GA_ROOT)
        for label, h in (("leaf", hwnd), ("root", root)):
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(h, cls, 256)
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, title, 256)
            ex = user32.GetWindowLongW(h, -20)
            print(f"  {label:<5} hwnd=0x{h:X} class='{cls.value}' title='{title.value[:50]}'")
            print(f"        exstyle=0x{ex & 0xFFFFFFFF:08X}")

    print()
    print("=" * 72)
    print("D. FULL-SCREEN / GPU SURFACES (these force software cursor mode)")
    print("=" * 72)
    import subprocess
    import json

    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match "
        "'(chrome|msedge|Code|Game|steam|obs|teams|zoom|Codex|cua-driver|"
        "QAAgent|Acer|LiveWallpaper|Wallpaper)' } | "
        "Select-Object ProcessId,Name,ExecutablePath | ConvertTo-Json -Depth 2"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        data = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        data = []
    if isinstance(data, dict):
        data = [data]
    for row in data:
        print(f"  pid={row.get('ProcessId'):<7} {row.get('Name')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

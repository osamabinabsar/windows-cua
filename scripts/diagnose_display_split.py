"""Which display does each affected window actually live on?

dxdiag reports MPO capability PER OUTPUT, and on this machine the two
outputs differ:

    DISPLAY1  B27T-7 LED   DisplayPort  1920x1080   MPO MaxPlanes: 1  Not Supported
    DISPLAY2  HP P221      HDMI         1920x1080   MPO MaxPlanes: 4  MPO3

so an overlay plane can only exist on the SECONDARY.  The decisive
glyph-match sample in the doc was taken at (1685,1) - the PRIMARY.

This script answers the question that decides whether MPO can explain
the symptom at all: for every visible top-level window, which display
does it sit on, and where is the pointer right now?

It reports WINDOW CLASSES and GEOMETRY ONLY.  It never prints window
titles, file names, or any document content.

Read-only: no input is synthesised, nothing is captured, nothing is
modified.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wt.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.POINTER(RECT),
                                        ctypes.c_void_p, ctypes.c_void_p]
user32.EnumDisplayMonitors.restype = wt.BOOL
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFOEX)]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.EnumWindows.restype = wt.BOOL
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorInfo.argtypes = [ctypes.c_void_p]
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND

MONITORINFOF_PRIMARY = 1
GA_ROOT = 2

# Classes worth reporting - the surfaces the user named, plus the
# containers they live in.  Anything else is skipped.
INTERESTING = (
    "CabinetWClass", "ExploreWClass", "ShellTabWindowClass",
    "Chrome_WidgetWin_1", "ApplicationFrameWindow",
)


class Display:
    def __init__(self, hmon, mi):
        self.hmon = hmon
        self.rect = mi.rcMonitor
        self.primary = bool(mi.dwFlags & MONITORINFOF_PRIMARY)
        self.device = mi.szDevice

    @property
    def label(self) -> str:
        return "PRIMARY" if self.primary else "secondary"

    def contains(self, x, y) -> bool:
        r = self.rect
        return r.left <= x < r.right and r.top <= y < r.bottom

    def __str__(self) -> str:
        r = self.rect
        return (f"{self.device} [{self.label}] "
                f"({r.left},{r.top})-({r.right},{r.bottom}) "
                f"{r.right - r.left}x{r.bottom - r.top}")


def collect_displays():
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC,
                         ctypes.POINTER(RECT), ctypes.c_void_p)
    def cb(hmon, hdc, lprc, ldata):
        mi = MONITORINFOEX()
        mi.cbSize = ctypes.sizeof(MONITORINFOEX)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            out.append(Display(hmon, mi))
        return True

    user32.EnumDisplayMonitors(None, None, cb, None)
    return out


def class_of(h) -> str:
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def enumerate_windows(displays):
    rows = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, ctypes.c_void_p)
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = class_of(hwnd)
        if cls not in INTERESTING:
            return True
        r = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            return True
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        # Which display(s) does the window's centre land on, and how much
        # of the window's area is on each display?
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        centre_on = [d for d in displays if d.contains(cx, cy)]
        rows.append({
            "cls": cls, "hwnd": hwnd, "pid": pid.value,
            "rect": (r.left, r.top, r.right, r.bottom),
            "area": w * h,
            "centre": (cx, cy),
            "centre_display": centre_on[0].label if centre_on else "off-screen",
        })
        return True

    user32.EnumWindows(cb, None)
    return rows


def main() -> int:
    displays = collect_displays()
    print("=" * 74)
    print("DISPLAYS")
    print("=" * 74)
    for d in displays:
        print(f"  {d}")

    rows = enumerate_windows(displays)
    rows.sort(key=lambda r: -r["area"])

    print()
    print("=" * 74)
    print("VISIBLE TOP-LEVEL WINDOWS OF INTEREST  (class + geometry only)")
    print("=" * 74)
    for r in rows:
        print(f"  {r['cls']:<22} pid={r['pid']:<6} "
              f"rect={r['rect']}  centre={r['centre']} -> {r['centre_display']}")

    pos = POINT()
    if user32.GetCursorPos(ctypes.byref(pos)):
        here = [d for d in displays if d.contains(pos.x, pos.y)]
        top = user32.WindowFromPoint(pos)
        root = user32.GetAncestor(top, GA_ROOT) if top else 0
        print()
        print(f"  POINTER NOW at {pos.x},{pos.y} -> "
              f"{here[0].label if here else 'off-screen'}")
        if root:
            print(f"  under window class: {class_of(root)}")

    print()
    print("  NOTE: MPO is reported PER OUTPUT by dxdiag.")
    print("  On this machine only the secondary output has an overlay plane")
    print("  (MPO MaxPlanes: 4); the primary reports MaxPlanes: 1 /")
    print("  'Not Supported'.  A symptom on the PRIMARY display therefore")
    print("  cannot be explained by an overlay plane being scanned out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

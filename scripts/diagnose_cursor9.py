"""Ninth pass: the visible/hidden split should follow SURFACE TYPE.

Claim under test:
  HIDDEN over  XAML islands (Explorer nav pane, search results) and
               Chromium/Electron renderers (VS Code explorer, chat app)
  VISIBLE over classic DirectUI content drawn into the parent's own
               surface (the Explorer file list)

If true, the split is not about apps or regions but about how each
region is composited -- and the pointer, which lives on the display's
hardware cursor plane, is occluded by surfaces promoted to an overlay
plane.

This enumerates Explorer's child windows and labels each region.

Read-only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
EnumChildProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)] if False else None  # placeholder


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND


def class_of(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def title_of(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, b, 256)
    return b.value


def rect_of(h):
    r = RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def pid_of(h):
    p = wt.DWORD(0)
    user32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def surface_kind(cls: str) -> str:
    """How is this window composited?"""
    if "Xaml" in cls or cls.startswith("Xaml"):
        return "XAML island -- own DirectComposition surface, OVERLAY-ELIGIBLE"
    if cls in ("Chrome_RenderWidgetHostHWND",):
        return "Chromium renderer -- flip-model surface, OVERLAY-ELIGIBLE"
    if cls in ("CASCADIA_HOSTING_WINDOW_CLASS",):
        return "Terminal -- own surface, OVERLAY-ELIGIBLE"
    if cls in ("DirectUIHWND", "SHELLDLL_DefView", "SysListView32", "SysTreeView32"):
        return "classic DirectUI/GDI -- drawn into the parent surface, NOT promoted"
    return ""


def dump(root, label, depth=0, max_depth=4):
    kids = []

    def cb(c, _l):
        kids.append(c)
        return True

    user32.EnumChildWindows(root, EnumChildProc(cb), 0)
    for c in kids:
        r = rect_of(c)
        w, h = r[2] - r[0], r[3] - r[1]
        if w <= 0 or h <= 0:
            continue
        vis = bool(user32.IsWindowVisible(c))
        if not vis:
            continue
        cls = class_of(c)
        kind = surface_kind(cls)
        print(
            f"  {'  ' * depth}[{w:>5}x{h:<5}] ({r[0]:>6},{r[1]:>6})-({r[2]:>6},{r[3]:>6}) "
            f"'{cls}'"
        )
        if kind:
            print(f"  {'  ' * depth}     -> {kind}")


def main() -> int:
    print("=" * 74)
    print("A. EXPLORER FILE WINDOWS: WHICH SURFACE IS EACH REGION?")
    print("=" * 74)
    found = []

    def cb(h, _l):
        cls = class_of(h)
        if cls in ("CabinetWClass", "ExploreWClass") and user32.IsWindowVisible(h):
            r = rect_of(h)
            if (r[2] - r[0]) > 400 and (r[3] - r[1]) > 250:
                found.append(h)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    print(f"  ({len(found)} Explorer file windows visible)")
    for h in found:
        r = rect_of(h)
        print()
        print(f"  EXPLORER 0x{h:X} '{title_of(h)}' at {r}")
        dump(h, "explorer")

    print()
    print("=" * 74)
    print("B. VS CODE / ELECTRON / CHROME RENDERER SURFACES")
    print("=" * 74)
    tops = []

    def cb2(h, _l):
        if user32.IsWindowVisible(h):
            cls = class_of(h)
            if cls in ("Chrome_WidgetWin_1", "CASCADIA_HOSTING_WINDOW_CLASS"):
                r = rect_of(h)
                if (r[2] - r[0]) > 300:
                    tops.append(h)
        return True

    user32.EnumWindows(EnumWindowsProc(cb2), 0)
    for h in tops:
        r = rect_of(h)
        cls = class_of(h)
        print()
        print(f"  {cls} 0x{h:X} '{title_of(h)}' at {r}")
        dump(h, "app", max_depth=3)

    print()
    print("=" * 74)
    print("C. WHAT IS UNDER THE POINTER RIGHT NOW")
    print("=" * 74)
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wt.BOOL
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND

    pos = POINT()
    user32.GetCursorPos(ctypes.byref(pos))
    print(f"  pointer at ({pos.x}, {pos.y})")
    hwnd = user32.WindowFromPoint(pos)
    while hwnd:
        r = rect_of(hwnd)
        cls = class_of(hwnd)
        kind = surface_kind(cls)
        print(f"    0x{hwnd:X} ({r[0]},{r[1]})-({r[2]},{r[3]}) '{cls}' pid={pid_of(hwnd)}")
        if kind:
            print(f"        -> {kind}")
        parent = user32.GetAncestor(hwnd, 1)
        if parent == hwnd:
            break
        hwnd = parent

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

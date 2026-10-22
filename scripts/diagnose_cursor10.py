"""Tenth pass: the visible/hidden split, proved by geometry.

The user's report, restated as a testable prediction:

  In ONE Explorer window, the pointer IS visible over the file list
  (classic DirectUI, drawn into the parent surface) and is NOT visible
  over the navigation pane (a XAML island, its own DirectComposition
  surface).

  In VS Code the pointer is NOT visible over the file explorer
  (a Chromium renderer, a flip-model surface).

  In Windows Search the pointer is NOT visible over the app list
  (a XAML island).

So the split should follow SURFACE TYPE, not app and not region.

This does not need the mouse to be anywhere in particular. It asks
Windows: "if the pointer were at THIS pixel, which window would be
under it?" -- via WindowFromPoint at fixed coordinates inside the
affected windows. That gives the surface class of the invisible region
and of the visible region, in the same window, in one shot.

Read-only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


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
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(POINT)]
user32.ClientToScreen.restype = wt.BOOL


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


def surface_kind(cls: str) -> str:
    """How is this HWND composited, and can it be promoted to a
    hardware overlay plane (which would draw over the cursor plane)?"""
    c = cls.lower()
    if "xaml" in c:
        return "XAML ISLAND -- own DirectComposition surface, OVERLAY-ELIGIBLE"
    if c == "chrome_renderwidgethosthwnd":
        return "CHROMIUM RENDERER -- flip-model surface, OVERLAY-ELIGIBLE"
    if c == "cascadia_hosting_window_class":
        return "TERMINAL -- own surface, OVERLAY-ELIGIBLE"
    if c in ("directuihwnd", "shelldll_defview", "syslistview32", "systreeview32"):
        return "classic DirectUI/GDI -- drawn into the PARENT surface, NOT promoted"
    if c in ("progman", "workerw"):
        return "desktop"
    if c == "shell_docobjectview":
        return "Explorer shell host (DirectUI into parent)"
    return ""


def chain_at(x: int, y: int, label: str) -> None:
    """Print the window chain under a screen point, and classify each."""
    print(f"  {label}  ({x},{y})")
    h = user32.WindowFromPoint(POINT(x, y))
    if not h:
        print("      <no window>")
        return
    seen = set()
    depth = 0
    while h and h not in seen:
        seen.add(h)
        r = rect_of(h)
        cls = class_of(h)
        kind = surface_kind(cls)
        print(
            f"      {'  ' * depth}0x{h:X} ({r[0]},{r[1]})-({r[2]},{r[3]}) "
            f"'{cls}' pid={pid_of(h)}"
        )
        if kind:
            print(f"      {'  ' * depth}      -> {kind}")
        depth += 1
        parent = user32.GetAncestor(h, 1)
        if parent == h:
            break
        h = parent


def main() -> int:
    print("=" * 78)
    print("A. EXPLORER: NAV PANE (pointer INVISIBLE) vs FILE LIST (pointer VISIBLE)")
    print("=" * 78)
    explorers = []

    def cb(h, _l):
        if class_of(h) in ("CabinetWClass", "ExploreWClass"):
            explorers.append(h)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    shown = 0
    for h in explorers:
        vis = bool(user32.IsWindowVisible(h))
        icon = bool(user32.IsIconic(h))
        r = rect_of(h)
        w, ht = r[2] - r[0], r[3] - r[1]
        print(
            f"  Explorer 0x{h:X} '{title_of(h)}' at {r} "
            f"visible={int(vis)} minimised={int(icon)}"
        )
        if not vis or w < 400 or ht < 250:
            print("      (skipping: not on screen)")
            continue
        shown += 1
        # Nav pane: ~ 40-200px from the left edge of the client area.
        nav_x = r[0] + 110
        list_x = r[0] + int(w * 0.65)
        mid_y = r[1] + int(ht * 0.5)
        print()
        chain_at(nav_x, mid_y, "NAV PANE  (user reports: pointer INVISIBLE)")
        print()
        chain_at(list_x, mid_y, "FILE LIST (user reports: pointer VISIBLE)")
    if shown == 0:
        print("  (no Explorer file window currently on screen)")

    print()
    print("=" * 78)
    print("B. VS CODE: FILE EXPLORER (pointer INVISIBLE)")
    print("=" * 78)
    codes = []

    def cb2(h, _l):
        if class_of(h) == "Chrome_WidgetWin_1" and user32.IsWindowVisible(h):
            codes.append(h)
        return True

    user32.EnumWindows(EnumWindowsProc(cb2), 0)
    found_code = False
    for h in codes:
        t = title_of(h)
        r = rect_of(h)
        w = r[2] - r[0]
        if w < 400:
            continue
        label = "VS CODE" if "Visual Studio Code" in t else "chromium/electron app"
        if "Visual Studio Code" not in t:
            continue
        found_code = True
        print(f"  {label} 0x{h:X} '{t}' at {r}")
        # The VS Code activity bar + explorer sits at the far left.
        chain_at(r[0] + 120, r[1] + 200, "EXPLORER PANE (user reports: pointer INVISIBLE)")
        print()
        chain_at(r[0] + int(w * 0.6), r[1] + 200, "EDITOR AREA")
    if not found_code:
        print("  (VS Code not currently visible)")

    print()
    print("=" * 78)
    print("C. WINDOWS SEARCH RESULTS LIST (user reports: pointer INVISIBLE)")
    print("=" * 78)
    # The search flyout is a XAML island hosted by ShellExperienceHost /
    # Windows.UI.Core.CoreWindow or a SearchHost window.
    search_wins = []

    def cb3(h, _l):
        cls = class_of(h)
        if user32.IsWindowVisible(h) and (
            "Search" in cls or "Xaml" in cls or "SearchHost" in cls
        ):
            r = rect_of(h)
            if (r[2] - r[0]) > 150 and (r[3] - r[1]) > 100:
                search_wins.append(h)
        return True

    user32.EnumWindows(EnumWindowsProc(cb3), 0)
    if not search_wins:
        print("  (no search flyout currently open)")
    for h in search_wins:
        r = rect_of(h)
        print(f"  0x{h:X} '{class_of(h)}' '{title_of(h)}' at {r}")
        chain_at(r[0] + int((r[2] - r[0]) * 0.5), r[1] + int((r[3] - r[1]) * 0.6),
                 "RESULTS LIST (user reports: pointer INVISIBLE)")

    print()
    print("=" * 78)
    print("D. WHERE THE POINTER IS RIGHT NOW")
    print("=" * 78)
    pos = POINT()
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wt.BOOL
    user32.GetCursorPos(ctypes.byref(pos))
    chain_at(pos.x, pos.y, "CURRENT POINTER POSITION")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

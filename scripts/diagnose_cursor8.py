"""Eighth pass: what KIND of surface is each affected region?

The hypothesis under test: the pointer lives on the display's hardware
cursor plane (confirmed: GetCursorInfo says SHOWING with a normal
arrow, yet the framebuffer at the cursor shows no arrow). A pointer on
its own plane is occluded by anything composited ABOVE that plane --
i.e. a hardware overlay plane, which DirectComposition surfaces can be
promoted to.

If that is right, the visible/hidden split should follow SURFACE TYPE,
not app or region:
  - XAML island windows (XamlExplorerHostIslandWindow) are separate
    DirectComposition surfaces -> eligible for promotion.
  - Chromium/Electron renderers (Chrome_RenderWidgetHostHWND) are
    DirectComposition flip-model surfaces -> eligible for promotion.
  - Classic DirectUI/GDI lists drawn into the parent surface are not.

So: enumerate Explorer's child windows and classify each region, and
do the same for VS Code.

Read-only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_EXSTYLE = -20
GWL_STYLE = -16


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
EnumChildProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.IsWindowEnabled.argtypes = [wt.HWND]


def class_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def title_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def rect_of(hwnd):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def pid_of(hwnd) -> int:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


# Surface classification: does this window have its own composition
# surface? XAML islands and Chromium renderers do; classic DirectUI
# children do not.
XAML_ISLAND = "XamlExplorerHostIslandWindow"
CHROMIUM = "Chrome_RenderWidgetHostHWND"
DIRECTUI = "DirectUIHWND"
SHELL = "Shell_DocObjectView"


def classify(cls: str) -> str:
    if cls.startswith("Xaml") or "Xaml" in cls:
        return "XAML-ISLAND (own DirectComposition surface, promotion-eligible)"
    if cls == CHROMIUM:
        return "CHROMIUM RENDERER (flip-model surface, promotion-eligible)"
    if cls == DIRECTUI:
        return "DirectUI (drawn into parent surface, NOT separately promoted)"
    if cls == "Progman" or cls == "WorkerW":
        return "desktop"
    if cls == SHELL:
        return "Explorer shell host"
    return ""


def dump_tree(root_hwnd, label: str, max_depth: int = 3) -> None:
    print()
    print("=" * 72)
    print(f"{label}  (root hwnd=0x{root_hwnd:X}, class='{class_of(root_hwnd)}')")
    print("=" * 72)
    lvl = [(root_hwnd, 0)]

    def walk(hwnd, depth):
        if depth > max_depth:
            return
        children = []

        def cb(child, _l):
            children.append(child)
            return True

        user32.EnumChildWindows(hwnd, EnumChildProc(cb), 0)
        for c in children:
            cls = class_of(c)
            r = rect_of(c)
            vis = bool(user32.IsWindowVisible(c))
            kind = classify(cls)
            width = r[2] - r[0]
            height = r[3] - r[1]
            if width <= 0 or height <= 0:
                continue
            print(
                f"  {'  ' * depth}[{width:>5}x{height:<5}] "
                f"({r[0]:>6},{r[1]:>6}) vis={int(vis)} "
                f"class='{cls}'"
            )
            if kind:
                print(f"  {'  ' * depth}      -> {kind}")
            walk(c, depth + 1)

    walk(root_hwnd, 0)


def find_top_level(class_contains: str, title_contains: str | None = None):
    found = []

    def cb(hwnd, _l):
        if class_contains.lower() in class_of(hwnd).lower():
            if title_contains is None or title_contains.lower() in title_of(hwnd).lower():
                found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def main() -> int:
    print("=" * 72)
    print("A. EXPLORER WINDOW TREE")
    print("=" * 72)
    explorers = find_top_level("CabinetWClass")
    if not explorers:
        explorers = find_top_level("ExploreWClass")
    for h in explorers:
        if not user32.IsWindowVisible(h):
            continue
        r = rect_of(h)
        w, ht = r[2] - r[0], r[3] - r[1]
        if w < 300 or ht < 200:
            continue
        dump_tree(h, f"EXPLORER FILE WINDOW '{title_of(h)}'")

    print()
    print("=" * 72)
    print("B. WHICH SURFACE IS UNDER THE POINTER RIGHT NOW")
    print("=" * 72)
    user32.WindowFromPoint.argtypes = [POINT]
    user32.WindowFromPoint.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND

    pos = POINT()
    user32.GetCursorPos(ctypes.byref(pos))
    print(f"  cursor at ({pos.x}, {pos.y})")
    hwnd = user32.WindowFromPoint(pos)
    chain = []
    while hwnd:
        chain.append(hwnd)
        parent = user32.GetAncestor(hwnd, 1)  # GA_PARENT
        if parent == hwnd:
            break
        hwnd = parent
    for h in chain:
        r = rect_of(h)
        cls = class_of(h)
        kind = classify(cls)
        print(f"    0x{h:X} ({r[0]},{r[1]})-({r[2]},{r[3]}) '{cls}' pid={pid_of(h)}")
        if kind:
            print(f"        -> {kind}")

    print()
    print("=" * 72)
    print("C. VS CODE: IS ITS EXPLORER A SEPARATE RENDERER SURFACE?")
    print("=" * 72)
    codes = find_top_level("Chrome_WidgetWin_1", "Visual Studio Code")
    for h in codes:
        if not user32.IsWindowVisible(h):
            continue
        r = rect_of(h)
        if (r[2] - r[0]) < 400:
            continue
        print(f"  VS Code window 0x{h:X} at {r}")
        dump_tree(h, "VS CODE", max_depth=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fourteenth pass: static geometric hit-test of the affected windows.

The live census needs the mouse to move. This does not: for a window
whose stored rectangle is still meaningful, walk the child-window tree
and find, for each sample point, the DEEPEST child whose rectangle
contains the point -- i.e. the window that would actually paint there.

Sample points are derived from the census itself (the x-range of the
nav-pane control vs the x-range of the file-list island) rather than
guessed, and each point is reported with the full ancestor chain so
every WNDCLASS.hCursor on the path is visible.

The key discriminator: WS_EX_NOREDIRECTIONBITMAP (0x200000) marks a
window that redirects nothing to the screen -- i.e. one that owns its
own DirectComposition surface (a XAML island / host bridge). A window
without it is drawn into its parent's surface.

Read-only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)

GCLP_HCURSOR = -12
GWL_EXSTYLE = -20
IDC_ARROW_HANDLE = 0x10003
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
EnumChildProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_long
user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_long
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]


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


def exstyle(h) -> int:
    return user32.GetWindowLongPtrW(h, GWL_EXSTYLE) & 0xFFFFFFFF


def class_cursor(h) -> int:
    return user32.GetClassLongPtrW(h, GCLP_HCURSOR)


def cursor_name(h: int) -> str:
    if h == 0:
        return "NULL"
    if h == IDC_ARROW_HANDLE:
        return "ARROW"
    return f"0x{h:X}"


def children_of(h) -> list:
    out = []

    def cb(c, _l):
        out.append(c)
        return True

    user32.EnumChildWindows(h, EnumChildProc(cb), 0)
    return out


def flags_of(h) -> str:
    e = exstyle(h)
    bits = []
    if e & WS_EX_NOREDIRECTIONBITMAP:
        bits.append("NOREDIRECTIONBITMAP(own DComp surface)")
    if e & WS_EX_LAYERED:
        bits.append("LAYERED")
    if e & WS_EX_TRANSPARENT:
        bits.append("TRANSPARENT")
    return ",".join(bits) if bits else "-"


def deepest_painting(root, x, y):
    """Deepest descendant of `root` whose rect contains (x, y).
    Among the candidates pick the one with the smallest area, then the
    deepest in the z-order returned by EnumChildWindows."""
    best = None
    best_area = None
    for c in children_of(root):
        r = rect_of(c)
        if r[0] <= x < r[2] and r[1] <= y < r[3]:
            area = (r[2] - r[0]) * (r[3] - r[1])
            if best_area is None or area < best_area:
                best = c
                best_area = area
    return best


def chain_from(root, leaf):
    """Ancestor chain root -> leaf, using GetParent."""
    chain = []
    cur = leaf
    while cur and len(chain) < 20:
        chain.append(cur)
        cur = user32.GetParent(cur)
    chain.reverse()
    return chain


def census(root, label):
    print(f"\n  {label}  top-level '{class_of(root)}' '{title_of(root)}' {rect_of(root)} pid={pid_of(root)}")
    for c in children_of(root):
        cr = rect_of(c)
        print(
            f"    '{class_of(c)}'  class_cursor={cursor_name(class_cursor(c))}  "
            f"rect={cr}  vis={int(bool(user32.IsWindowVisible(c)))}  ex=0x{exstyle(c):08X} [{flags_of(c)}]"
        )
    return children_of(root)


def hit(root, x, y, what):
    h = deepest_painting(root, x, y)
    if h is None:
        print(f"    ({x},{y}) {what}\n        -> NO child window covers this point")
        return
    chain = chain_from(root, h)
    print(f"    ({x},{y}) {what}")
    for c in chain:
        r = rect_of(c)
        print(
            f"        -> '{class_of(c)}'  class_cursor={cursor_name(class_cursor(c))}  "
            f"rect={r}  ex=0x{exstyle(c):08X} [{flags_of(c)}]"
        )


def explorer_windows():
    out = []

    def cb(h, _l):
        if class_of(h) in ("CabinetWClass", "ExploreWClass"):
            out.append(h)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def chrome_windows():
    out = []

    def cb(h, _l):
        if class_of(h) == "Chrome_WidgetWin_1":
            out.append(h)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def main() -> int:
    print("=" * 78)
    print("A. EXPLORER: census + hit test")
    print("=" * 78)
    for h in explorer_windows():
        kids = census(h, "EXPLORER")
        # derive sample points from the census: the nav-pane control's
        # x-range vs the file-list island's x-range.
        nav = [k for k in kids if class_of(k) == "NamespaceTreeControl"]
        isl = [k for k in kids if class_of(k) == "FileExplorer_FolderViewIsland"]
        for k in nav:
            r = rect_of(k)
            x = (r[0] + r[2]) // 2
            y = (r[1] + r[3]) // 2
            hit(h, x, y, "NAV PANE  (pointer INVISIBLE)")
        for k in isl:
            r = rect_of(k)
            x = (r[0] + r[2]) // 2
            y = (r[1] + r[3]) // 2
            hit(h, x, y, "FILE LIST (pointer VISIBLE)")

    print()
    print("=" * 78)
    print("B. ELECTRON / CHROMIUM (VS Code and friends): census + hit test")
    print("=" * 78)
    for h in chrome_windows():
        t = title_of(h)
        kids = census(h, "CHROMIUM")
        rw = [k for k in kids if class_of(k) == "Chrome_RenderWidgetHostHWND"]
        for k in rw:
            r = rect_of(k)
            x = (r[0] + r[2]) // 2
            y = (r[1] + r[3]) // 2
            hit(h, x, y, "RENDER WIDGET")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

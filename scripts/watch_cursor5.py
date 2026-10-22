"""Watcher 5: chase the one region we never sampled - the Explorer nav pane.

Watcher 4 proved the OS pointer is healthy and lives on the adapter's
hardware cursor plane.  But every one of its 401 samples was over the
taskbar, a terminal or a Chromium app - the Explorer NAV PANE was never
sampled, and that is where the user says the pointer vanishes.

The nav pane is the only affected region whose class tree contains a
window with a NULL WNDCLASS.hCursor ('NamespaceTreeControl').  MSDN is
explicit about that: "If this member is NULL, the system has no cursor
for the class, and the cursor does not appear when the mouse is over a
window of this class."  That is a completely different mechanism from
display-plane occlusion, and it would explain the nav pane but not the
Chromium apps - so it has to be tested, not assumed.

This watcher records, for every sample:
  * the full ancestor chain under the pointer, and the WNDCLASS.hCursor
    of EVERY window in that chain (not just the leaf)
  * the live GetCursorInfo state (hCursor + flags)
  * whether the arrow glyph is composited into the screen image
  * whether the pointer is inside an Explorer window, and if so which
    column (nav pane vs file list) the hit-test says

It prints a summary of every distinct (window chain, live cursor)
combination it saw, with counts, so that a single pass over the nav
pane and the file list settles the question.

Read-only: no input is synthesised, no screen pixels are printed, no
personal data is read.

Usage:  python watch_cursor5.py [seconds]
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
import cursorframe  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

GCLP_HCURSOR = -12
CWP_ALL = 0x00000001
CWP_SKIPINVISIBLE = 0x00000002
GA_ROOT = 2
BOX = 64

EXPLORER_CLASSES = ("CabinetWClass", "ExploreWClass", "ShellTabWindowClass")
NAVPANE_CLASSES = ("NamespaceTreeControl", "SysTreeView32")


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wt.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


MONITORINFOF_PRIMARY = 1


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("flags", wt.DWORD),
        ("hCursor", wt.HANDLE), ("ptScreenPos", POINT),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
        ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.POINTER(RECT),
                                        ctypes.c_void_p, ctypes.c_void_p]
user32.EnumDisplayMonitors.restype = wt.BOOL
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFOEX)]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_long
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.ChildWindowFromPointEx.argtypes = [wt.HWND, POINT, wt.UINT]
user32.ChildWindowFromPointEx.restype = wt.HWND
user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(POINT)]
user32.ScreenToClient.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = wt.BOOL
user32.GetIconInfo.argtypes = [wt.HANDLE, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wt.BOOL
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [
    wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
    ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD,
]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.BitBlt.argtypes = [
    wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD,
]
gdi32.BitBlt.restype = wt.BOOL
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]


def class_of(h) -> str:
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


# --- displays -------------------------------------------------------------
# MPO is reported PER OUTPUT.  On this machine the primary DisplayPort
# output reports "MPO Caps: Not Supported" (no overlay plane at all)
# while the secondary HDMI output reports "MPO MaxPlanes: 4" (MPO3).
# Which display the pointer is on therefore decides whether the
# overlay-plane mechanism can even apply to that surface, so every
# sample records it.

_DISPLAYS: list[tuple[str, int, int, int, int]] = []


def _collect_displays() -> None:
    if _DISPLAYS:
        return

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC,
                         ctypes.POINTER(RECT), ctypes.c_void_p)
    def cb(hmon, hdc, lprc, ldata):
        mi = MONITORINFOEX()
        mi.cbSize = ctypes.sizeof(MONITORINFOEX)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            _DISPLAYS.append((
                "PRIMARY" if mi.dwFlags & MONITORINFOF_PRIMARY else "secondary",
                r.left, r.top, r.right, r.bottom,
            ))
        return True

    user32.EnumDisplayMonitors(None, None, cb, None)
    if not _DISPLAYS:                      # fall back to the virtual screen
        _DISPLAYS.append(("unknown", 0, 0, 1920, 1080))


def display_at(x: int, y: int) -> str:
    _collect_displays()
    for label, l, t, r, b in _DISPLAYS:
        if l <= x < r and t <= y < b:
            return label
    return "off-screen"


def pid_of(h) -> int:
    p = wt.DWORD(0)
    user32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def class_cursor(h) -> int:
    return user32.GetClassLongPtrW(h, GCLP_HCURSOR)


def cursor_name(h: int) -> str:
    if h == 0:
        return "NULL"
    if h == 0x7F00:
        return "ARROW"
    if h == 0x7F01:
        return "IBEAM"
    if h == 0x7F09:
        return "HAND"
    return f"0x{h:X}"


class Screen:
    def __init__(self):
        self.dc = user32.GetDC(None)
        self.mem = gdi32.CreateCompatibleDC(self.dc)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = BOX
        bi.bmiHeader.biHeight = -BOX
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        self.bits = ctypes.c_void_p()
        self.bmp = gdi32.CreateDIBSection(
            self.dc, ctypes.byref(bi), 0, ctypes.byref(self.bits), None, 0
        )
        self.old = gdi32.SelectObject(self.mem, self.bmp)

    def grab(self, x, y):
        src = user32.GetDC(None)
        try:
            gdi32.BitBlt(self.mem, 0, 0, BOX, BOX, src,
                         x - BOX // 2, y - BOX // 2, 0x00CC0020 | 0x40000000)
            return ctypes.string_at(self.bits, BOX * BOX * 4)
        finally:
            user32.ReleaseDC(None, src)

    def close(self):
        try:
            gdi32.SelectObject(self.mem, self.old)
            gdi32.DeleteObject(self.bmp)
            gdi32.DeleteDC(self.mem)
            user32.ReleaseDC(None, self.dc)
        except Exception:
            pass


def chain_at(x, y):
    """(root, [hwnds from root down to the deepest child under the point])."""
    top = user32.WindowFromPoint(POINT(x, y))
    if not top:
        return None, []
    root = user32.GetAncestor(top, GA_ROOT) or top
    chain = [root]
    cur = root
    for _ in range(12):
        p = POINT(x, y)
        if not user32.ScreenToClient(cur, ctypes.byref(p)):
            break
        child = user32.ChildWindowFromPointEx(cur, p, CWP_ALL | CWP_SKIPINVISIBLE)
        if not child or child == cur:
            break
        chain.append(child)
        cur = child
    return root, chain


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 900
    print(f"Watching for {seconds}s. Hover the EXPLORER NAV PANE (left column)")
    print("and the FILE LIST (right column) - and any other affected area.")
    print(flush=True)

    screen = Screen()
    seen: dict[tuple, dict] = {}
    end = time.time() + seconds
    n = 0
    last = None
    try:
        while time.time() < end:
            pos = POINT()
            if not user32.GetCursorPos(ctypes.byref(pos)):
                time.sleep(0.03)
                continue
            if last == (pos.x, pos.y):
                time.sleep(0.03)
                continue
            last = (pos.x, pos.y)
            ci = CURSORINFO()
            ci.cbSize = ctypes.sizeof(CURSORINFO)
            if not user32.GetCursorInfo(ctypes.byref(ci)):
                time.sleep(0.03)
                continue
            root, chain = chain_at(pos.x, pos.y)
            if root is None:
                time.sleep(0.03)
                continue

            names = [class_of(h) for h in chain]
            cursors = [class_cursor(h) for h in chain]
            in_explorer = any(nm in EXPLORER_CLASSES for nm in names)
            in_navpane = any(nm in NAVPANE_CLASSES for nm in names)
            disp = display_at(pos.x, pos.y)
            key = (
                tuple(names),
                tuple(cursor_name(c) for c in cursors),
                cursor_name(ci.hCursor),
                in_explorer,
                disp,
            )
            rec = seen.get(key)
            if rec is None:
                ii = ICONINFO()
                user32.GetIconInfo(ci.hCursor, ctypes.byref(ii))
                shape, sw, sh = cursorframe.cursor_shape(ci.hCursor)
                dr = lr = cdr = clr = 0.0
                if shape:
                    o, dt, dm, lt, lm = cursorframe.match_score(
                        shape, sw, sh, screen.grab(pos.x, pos.y), BOX,
                        ii.xHotspot, ii.yHotspot)
                    dr = (dm / dt) if dt else 0.0
                    lr = (lm / lt) if lt else 0.0
                    cdt = clt = 0.0
                    for dx, dy in ((0, -BOX), (0, BOX), (-BOX, 0)):
                        o2, dt2, dm2, lt2, lm2 = cursorframe.match_score(
                            shape, sw, sh, screen.grab(pos.x + dx, pos.y + dy),
                            BOX, ii.xHotspot, ii.yHotspot)
                        cdt = max(cdt, (dm2 / dt2) if dt2 else 0.0)
                        clt = max(clt, (lm2 / lt2) if lt2 else 0.0)
                rec = {
                    "n": 0, "chain": names, "cursors": [cursor_name(c) for c in cursors],
                    "explorer": in_explorer, "navpane": in_navpane,
                    "disp": disp,
                    "pid": pid_of(root), "hcursor": cursor_name(ci.hCursor),
                    "flags": ci.flags, "dr": dr, "lr": lr, "cdr": cdt, "clr": clt,
                    "pos": [],
                }
                seen[key] = rec
                print(
                    f"  [new] {'EXPLORER ' if in_explorer else ''}"
                    f"{'NAVPANE ' if in_navpane else ''}"
                    f"[{disp}] "
                    f"{' > '.join(names)}   live={rec['hcursor']} "
                    f"flags=0x{ci.flags:08X}  dark {dr:.2f}/ctrl {cdr:.2f} "
                    f"light {lr:.2f}/ctrl {clr:.2f}",
                    flush=True,
                )
            rec["n"] += 1
            if len(rec["pos"]) < 6:
                rec["pos"].append((pos.x, pos.y))
            n += 1
            time.sleep(0.03)
    finally:
        screen.close()

    print(f"\n{n} samples over {len(seen)} distinct chains.\n")
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for rec in sorted(seen.values(), key=lambda r: -r["n"]):
        nulls = [nm for nm, cu in zip(rec["chain"], rec["cursors"]) if cu == "NULL"]
        print()
        print(f"  {rec['n']:>5} samples   {'EXPLORER ' if rec['explorer'] else ''}"
              f"{'NAVPANE ' if rec['navpane'] else ''}pid={rec['pid']}")
        print(f"     display      : {rec['disp']}"
              + ("   (no MPO plane here - the overlay mechanism cannot apply)"
                 if rec['disp'] == 'PRIMARY' else
                 "   (MPO-capable output)" if rec['disp'] == 'secondary' else ""))
        print(f"     chain        : {' > '.join(rec['chain'])}")
        print(f"     class cursors: {' > '.join(rec['cursors'])}")
        if nulls:
            print(f"     NULL-CURSOR classes in chain: {', '.join(nulls)}")
        print(f"     live cursor  : {rec['hcursor']}  flags=0x{rec['flags']:08X} "
              f"(SHOWING={int(bool(rec['flags'] & 1))} SUPPRESSED={int(bool(rec['flags'] & 2))})")
        print(f"     glyph in image?: dark {rec['dr']:.2f} (ctrl {rec['cdr']:.2f})  "
              f"light {rec['lr']:.2f} (ctrl {rec['clr']:.2f})")
        print(f"     positions    : {rec['pos']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

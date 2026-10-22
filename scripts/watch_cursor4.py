"""Watcher 4: per-surface hardware-vs-software cursor determination.

For every distinct surface the pointer visits, this records:

  * the deepest visible child window and the WNDCLASS.hCursor of the
    whole ancestor chain
  * the live GetCursorInfo state
  * whether the arrow glyph is COMPOSITED INTO the screen image at the
    pointer, tested by matching the glyph's own bitmap against the
    screen and comparing the dark-outline and light-fill match rates
    with control samples taken nearby.

The last one is the discriminator:

  * glyph present  -> the pointer is a software cursor that DWM draws
    into the screen surface; if it is invisible, something covers it
    in the DWM composition order.
  * glyph absent   -> the pointer is on the adapter's HARDWARE CURSOR
    PLANE; the only way it can be hidden is a display plane scanned
    out above the cursor plane.

Read-only. No input is synthesised. Only counts and rates are printed;
no screen pixels are ever emitted.

Usage:  python watch_cursor4.py [seconds]
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


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


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
user32.IsWindowVisible.argtypes = [wt.HWND]
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


def deepest_at(x, y):
    top = user32.WindowFromPoint(POINT(x, y))
    if not top:
        return None, []
    root = user32.GetAncestor(top, GA_ROOT)
    if not root:
        root = top
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


def glyph_match(screen, shape, sw, sh, hotx, hoty, x, y):
    """(dark_rate, light_rate, dark_ctrl, light_ctrl) at (x, y)."""
    o, dt, dm, lt, lm = cursorframe.match_score(
        shape, sw, sh, screen.grab(x, y), BOX, hotx, hoty
    )
    cdt = clt = 0
    for dx, dy in ((0, -BOX), (0, BOX)):
        o2, dt2, dm2, lt2, lm2 = cursorframe.match_score(
            shape, sw, sh, screen.grab(x + dx, y + dy), BOX, hotx, hoty
        )
        cdt = max(cdt, (dm2 / dt2) if dt2 else 0.0)
        clt = max(clt, (lm2 / lt2) if lt2 else 0.0)
    return ((dm / dt) if dt else 0.0, (lm / lt) if lt else 0.0, cdt, clt)


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    print(f"Watching for {seconds}s. Move the pointer over the areas where")
    print("the arrow disappears (Explorer nav pane, VS Code tree, search results)")
    print("and over areas where it works (Explorer file list, Terminal).")
    print(flush=True)

    screen = Screen()
    seen: dict[tuple, dict] = {}
    end = time.time() + seconds
    n = 0
    last_pos = None
    try:
        while time.time() < end:
            pos = POINT()
            if not user32.GetCursorPos(ctypes.byref(pos)):
                time.sleep(0.03)
                continue
            if last_pos == (pos.x, pos.y):
                time.sleep(0.03)
                continue
            last_pos = (pos.x, pos.y)
            ci = CURSORINFO()
            ci.cbSize = ctypes.sizeof(CURSORINFO)
            if not user32.GetCursorInfo(ctypes.byref(ci)):
                time.sleep(0.03)
                continue
            root, chain = deepest_at(pos.x, pos.y)
            if root is None:
                time.sleep(0.03)
                continue
            leaf = chain[-1]
            ii = ICONINFO()
            user32.GetIconInfo(ci.hCursor, ctypes.byref(ii))
            shape, sw, sh = cursorframe.cursor_shape(ci.hCursor)
            if not shape:
                time.sleep(0.03)
                continue
            dr, lr, cdr, clr = glyph_match(
                screen, shape, sw, sh, ii.xHotspot, ii.yHotspot, pos.x, pos.y
            )
            key = (class_of(leaf), class_cursor(leaf), ci.hCursor)
            rec = seen.setdefault(
                key,
                {
                    "n": 0, "root": class_of(root), "pid": pid_of(root),
                    "leaf": class_of(leaf), "leaf_cursor": class_cursor(leaf),
                    "chain": [class_of(h) for h in chain],
                    "hcursor": ci.hCursor, "flags": ci.flags,
                    "dr": dr, "lr": lr, "cdr": cdr, "clr": clr,
                    "pos": [], "hw": 0, "inc": 0, "new": True,
                },
            )
            rec["n"] += 1
            # A control that scores the same as the pointer means the
            # background is uniform there, and the test cannot decide
            # anything.  Only a clear difference is evidence.
            if abs(dr - cdr) < 0.15 and abs(lr - clr) < 0.15:
                rec["inc"] += 1
            elif dr > 0.6 and lr > 0.6 and dr > cdr + 0.2 and lr > clr + 0.2:
                rec["hw"] += 1
            if len(rec["pos"]) < 8:
                rec["pos"].append((pos.x, pos.y))
            if rec["new"]:
                rec["new"] = False
                print(
                    f"  [new] {class_of(leaf):<40} pid={pid_of(root):<6} "
                    f"class_cursor={cursor_name(class_cursor(leaf)):<6} "
                    f"dark {dr:.2f}/ctrl {cdr:.2f}  light {lr:.2f}/ctrl {clr:.2f}  "
                    f"at {rec['pos'][-1]}",
                    flush=True,
                )
            n += 1
            time.sleep(0.03)
    finally:
        screen.close()

    print(f"\n{n} samples over {len(seen)} distinct surfaces.\n")
    print("=" * 78)
    print("PER-SURFACE RESULTS")
    print("=" * 78)
    for key, rec in sorted(seen.items(), key=lambda kv: -kv[1]["n"]):
        print()
        print(f"  {rec['n']:>5} samples  top-level='{rec['root']}' pid={rec['pid']}")
        print(f"     leaf            : '{rec['leaf']}'  class cursor = {cursor_name(rec['leaf_cursor'])}")
        print(f"     chain           : {' -> '.join(rec['chain'])}")
        print(
            f"     live cursor     : {cursor_name(rec['hcursor'])}  flags=0x{rec['flags']:08X} "
            f"(SHOWING={int(bool(rec['flags'] & 1))} SUPPRESSED={int(bool(rec['flags'] & 2))})"
        )
        print(
            f"     glyph in image? : dark {rec['dr']:.2f} (ctrl {rec['cdr']:.2f})  "
            f"light {rec['lr']:.2f} (ctrl {rec['clr']:.2f})  "
            f"-> {'SOFTWARE cursor' if rec['hw'] else ('inconclusive' if rec['inc'] else 'HARDWARE cursor plane')}"
        )
        print(f"     positions       : {rec['pos']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

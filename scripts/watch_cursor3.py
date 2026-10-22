"""Watcher 3: the decider.

The static census established the structure of the affected windows but
could not tell us the mechanism, because two independent dimensions
(class cursor, WS_EX_NOREDIRECTIONBITMAP) each cut across the
visible/invisible boundary.

The dimension that decides it is: **is the arrow composited into the
framebuffer where the pointer is, or is it on the adapter's hardware
cursor plane?**

  * Arrow IN the framebuffer  -> the pointer is a software cursor that
    DWM (or the app) draws. If it is missing on screen, something
    covers it in the DWM composition order -> a DWM z-order bug.
  * Arrow NOT in the framebuffer -> the pointer is on the hardware
    cursor plane, and the only way it can be hidden is a display
    plane scanned out ABOVE the cursor plane -> a driver/plane-ordering
    bug (Multi-Plane Overlay).

This watcher samples, at 25 Hz:

  * the pointer position
  * the topmost window at that point (WindowFromPoint -> real z-order)
  * the deepest visible child, and the WNDCLASS.hCursor of the whole
    ancestor chain
  * the live GetCursorInfo state, and the AND/XOR mask population of
    the live cursor, so a BLANK cursor can be told apart from a real
    one
  * a 32x32 read of the screen DC centred on the pointer, reduced to
    NUMBERS ONLY (the frame-to-frame difference and the count of
    high-contrast pixels). No screen content is ever printed.

It then reports one row per distinct combination, so the affected
surfaces can be compared with the working ones.

Read-only. No input is synthesised. No image data leaves this process.

Usage:  python watch_cursor3.py [seconds]
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

GCLP_HCURSOR = -12
CWP_ALL = 0x00000001
CWP_SKIPINVISIBLE = 0x00000002
GA_ROOT = 2
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0

EnumChildProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


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
user32.GetWindowDC.argtypes = [wt.HWND]
user32.GetWindowDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.GetIconInfo.argtypes = [wt.HANDLE, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wt.BOOL
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

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
gdi32.GetDIBits.argtypes = [
    wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFO), wt.UINT,
]
gdi32.GetDIBits.restype = wt.BOOL

BOX = 40  # side of the sampled square, centred on the pointer


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


def mask_population(hcursor) -> tuple[int, int]:
    """(and_set_pixels, xor_set_pixels) of a cursor's bitmaps.

    A cursor that an application substituted to hide the pointer has
    both masks empty.  Returns (-1, -1) if the bitmaps cannot be read.
    """
    if not hcursor:
        return (-1, -1)
    ii = ICONINFO()
    if not user32.GetIconInfo(hcursor, ctypes.byref(ii)):
        return (-1, -1)
    out = []
    for bmp in (ii.hbmMask, ii.hbmColor):
        if not bmp:
            out.append(0)
            continue
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = 32
        bi.bmiHeader.biHeight = 32
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(32 * 32 * 4)
        hdc = user32.GetDC(None)
        try:
            ok = gdi32.GetDIBits(
                hdc, bmp, 0, 32, buf, ctypes.byref(bi), DIB_RGB_COLORS
            )
        finally:
            user32.ReleaseDC(None, hdc)
        if not ok:
            out.append(-1)
            continue
        raw = bytes(buf)
        n = 0
        for i in range(0, 32 * 32 * 4, 4):
            px = raw[i:i + 4]
            if px != b"\x00\x00\x00\x00":
                n += 1
        out.append(n)
    return (out[0], out[1])


class ScreenSampler:
    """Reads a BOX x BOX square of the composited screen near the
    pointer.  Only aggregate numbers are returned; pixels never leave."""

    def __init__(self):
        self.dc = user32.GetDC(None)
        self.mem = gdi32.CreateCompatibleDC(self.dc)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = BOX
        bi.bmiHeader.biHeight = -BOX  # top-down
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        self.bits = ctypes.c_void_p()
        self.bmp = gdi32.CreateDIBSection(
            self.dc, ctypes.byref(bi), DIB_RGB_COLORS,
            ctypes.byref(self.bits), None, 0,
        )
        self.old = gdi32.SelectObject(self.mem, self.bmp)
        self.bi = bi

    def read(self, x: int, y: int) -> bytes | None:
        src = user32.GetDC(None)
        try:
            ok = gdi32.BitBlt(
                self.mem, 0, 0, BOX, BOX, src,
                x - BOX // 2, y - BOX // 2, SRCCOPY | CAPTUREBLT,
            )
        finally:
            user32.ReleaseDC(None, src)
        if not ok:
            return None
        n = BOX * BOX * 4
        return ctypes.string_at(self.bits, n)

    def close(self):
        try:
            gdi32.SelectObject(self.mem, self.old)
            gdi32.DeleteObject(self.bmp)
            gdi32.DeleteDC(self.mem)
            user32.ReleaseDC(None, self.dc)
        except Exception:
            pass


def arrow_likelihood(pix: bytes) -> tuple[int, int]:
    """(changed_pixels, contrast_pixels) for the sampled square.

    `changed` = pixels that differ from the box's dominant colour, i.e.
    the amount of local detail.  A hardware cursor contributes nothing
    to this.  A composited software cursor adds a compact, very high
    contrast blob in the middle of the box.
    """
    if pix is None:
        return (-1, -1)
    px = []
    for i in range(0, len(pix), 4):
        b, g, r = pix[i], pix[i + 1], pix[i + 2]
        px.append((r, g, b))
    counts = {}
    for p in px:
        counts[p] = counts.get(p, 0) + 1
    dominant = max(counts, key=counts.get)
    changed = 0
    contrast = 0
    for p in px:
        d = abs(p[0] - dominant[0]) + abs(p[1] - dominant[1]) + abs(p[2] - dominant[2])
        if d > 24:
            changed += 1
        if d > 500:
            contrast += 1
    return (changed, contrast)


def deepest_at(x: int, y: int):
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


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 900
    print(f"Watching for {seconds}s at 25 Hz.")
    print("Please move the pointer over:")
    print("  * the Explorer NAVIGATION PANE  (arrow disappears)")
    print("  * the Explorer FILE LIST        (arrow visible)")
    print("  * the VS Code file explorer     (arrow disappears)")
    print("  * the Windows search results    (arrow disappears)")
    print()
    print("Legend for 'fb' (framebuffer): a software cursor is COMPOSITED")
    print("into the screen image and appears as a high-contrast blob in")
    print("the 40x40 box around the pointer.  A hardware-plane cursor")
    print("never appears there.  'contrast' is that blob's pixel count.")
    print()

    sampler = ScreenSampler()
    seen: dict[tuple, dict] = {}
    end = time.time() + seconds
    n = 0
    try:
        while time.time() < end:
            pos = POINT()
            if not user32.GetCursorPos(ctypes.byref(pos)):
                time.sleep(0.04)
                continue
            info = CURSORINFO()
            info.cbSize = ctypes.sizeof(CURSORINFO)
            if not user32.GetCursorInfo(ctypes.byref(info)):
                time.sleep(0.04)
                continue
            root, chain = deepest_at(pos.x, pos.y)
            if root is None:
                time.sleep(0.04)
                continue
            leaf = chain[-1]
            and_px, xor_px = mask_population(info.hCursor)
            pix = sampler.read(pos.x, pos.y)
            changed, contrast = arrow_likelihood(pix)

            key = (class_of(leaf), class_cursor(leaf), info.hCursor, info.flags, and_px, xor_px)
            rec = seen.setdefault(
                key,
                {
                    "n": 0, "root": class_of(root), "pid": pid_of(root),
                    "leaf": class_of(leaf), "leaf_cursor": class_cursor(leaf),
                    "chain": [class_of(h) for h in chain],
                    "hcursor": info.hCursor, "flags": info.flags,
                    "and": and_px, "xor": xor_px,
                    "changed": changed, "contrast": contrast,
                    "pos": [], "detail": None,
                },
            )
            rec["n"] += 1
            if rec["detail"] is None:
                rec["detail"] = (changed, contrast)
            if len(rec["pos"]) < 6:
                rec["pos"].append((pos.x, pos.y))
            n += 1
            time.sleep(0.04)
    finally:
        sampler.close()

    print(f"{n} samples, {len(seen)} distinct combinations.")
    print()
    print("=" * 78)
    print("RESULTS")
    print("=" * 78)
    for key, rec in sorted(seen.items(), key=lambda kv: -kv[1]["n"]):
        changed, contrast = rec["detail"]
        software = "SOFTWARE (arrow in image)" if contrast > 25 else "HARDWARE PLANE"
        print()
        print(f"  {rec['n']:>6} samples  top-level='{rec['root']}' pid={rec['pid']}")
        print(f"     leaf            : '{rec['leaf']}'  class cursor = {cursor_name(rec['leaf_cursor'])}")
        print(f"     chain           : {' -> '.join(rec['chain'])}")
        print(
            f"     live cursor     : {cursor_name(rec['hcursor'])}  "
            f"flags=0x{rec['flags']:08X} (SHOWING={int(bool(rec['flags'] & 1))} "
            f"SUPPRESSED={int(bool(rec['flags'] & 2))})  "
            f"and={rec['and']} xor={rec['xor']} px"
        )
        print(
            f"     framebuffer @ptr: changed={changed} contrast={contrast}"
            f"  ->  pointer appears to be on the {software}"
        )
        print(f"     positions       : {rec['pos']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

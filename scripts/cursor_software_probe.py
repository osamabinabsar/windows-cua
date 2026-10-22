"""A/B the pointer: is it COVERED, or is the hardware cursor plane BROKEN?

**SUPERSEDED - use scripts/cursor_is_composited.py instead.**
This script is kept because it is the first attempt and because its
failure mode is instructive, but its calibration is VOID.  It draws a
solid magenta cursor with SetCursor() and looks for it in a screen
grab; it finds 0 pixels and concludes "the reader cannot see software
cursors".  That conclusion is wrong, and the reason is the thing this
script never checked:

    SetCursor() sets the cursor shape for the windows OWNED BY THE
    CALLING THREAD.  A console script owns no window at the pointer's
    position, so the magenta cursor was NEVER DRAWN.  "0 magenta
    pixels" measured nothing at all.

cursor_is_composited.py checks the handle (GetCursorInfo still returns
the system arrow after SetCursor) and replaces the calibration with a
spatial offset sweep, which needs no input synthesis and is valid.

The rest of this file is the original A/B design, kept for reference.

---

Two faults look identical from a framebuffer capture, because a GDI
screen read never contains the hardware cursor:

  A. something is composited OVER the pointer   -> the pointer is there
     but hidden
  B. the hardware cursor plane is not being updated for that region
     -> the pointer is never drawn there

They need different fixes, so they must be separated.  The separation:

Windows draws a NON-SYSTEM cursor in software, into the composited
image, instead of on the hardware cursor plane.  (Raymond Chen, "The
Case of the Disappearing Cursor" - the same feature is why mouse
trails work: a hardware cursor cannot leave a trail behind it.)

So: build a custom cursor with the SAME artwork as the live arrow
(GetIconInfo -> CreateIconIndirect over its own hbmColor/hbmMask),
SetCursor() it, and read the screen again.

  the arrow now appears  -> nothing was covering the pointer; the fault
                            is the hardware cursor plane           (B)
  the arrow still absent -> something really is drawn over it     (A)

IMPORTANT - the instrument is calibrated first, because "the arrow did
not appear" is only meaningful if the reader can see a software cursor
at all.  A solid magenta 32x32 square is drawn in software and looked
for in the same grab:

  magenta found    -> the reader can see software cursors; the arrow
                      result is meaningful
  magenta not found-> the reader CANNOT see software cursors, so the
                      whole framebuffer route is blind and only a
                      by-eye test can decide

  ...but ONLY if SetCursor() actually took effect.  THIS SCRIPT DOES
  NOT CHECK, and on this machine it does not.  See cursor_is_composited.py.

What this script does to the system: it sets a custom cursor for a
moment and puts the original back.  No clicks, no keys, no scrolling.
The only pointer movement is a one-pixel jiggle and its inverse, so the
OS redraws - the pointer ends at its exact original coordinates.
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

BOX = 64
MAGENTA = (255, 0, 255)


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


user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = wt.BOOL
user32.GetIconInfo.argtypes = [wt.HANDLE, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wt.BOOL
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wt.HANDLE
user32.SetCursor.argtypes = [wt.HANDLE]
user32.SetCursor.restype = wt.HANDLE
user32.DestroyCursor.argtypes = [wt.HANDLE]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wt.BOOL
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

    def grab(self, x, y) -> bytes:
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


def jiggle(x, y):
    user32.SetCursorPos(x + 1, y)
    time.sleep(0.05)
    user32.SetCursorPos(x, y)
    time.sleep(0.15)


def count_magenta(buf: bytes) -> int:
    n = 0
    for i in range(0, len(buf), 4):
        b, g, r = buf[i], buf[i + 1], buf[i + 2]
        if r > 200 and g < 60 and b > 200:
            n += 1
    return n


def make_magenta_cursor(size: int = 32) -> wt.HANDLE:
    """A solid magenta square - impossible to confuse with the wallpaper."""
    dc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(dc)
    try:
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = size
        bi.bmiHeader.biHeight = -size
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 24
        bi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        colour = gdi32.CreateDIBSection(dc, ctypes.byref(bi), 0,
                                        ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(mem, colour)
        row = bytes(MAGENTA) * size
        for y in range(size):
            ctypes.memmove(bits.value + y * size * 3, row, size * 3)

        mbi = BITMAPINFO()
        mbi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        mbi.bmiHeader.biWidth = size
        mbi.bmiHeader.biHeight = -size
        mbi.bmiHeader.biPlanes = 1
        mbi.bmiHeader.biBitCount = 1
        mbi.bmiHeader.biCompression = 0
        mbits = ctypes.c_void_p()
        mask = gdi32.CreateDIBSection(dc, ctypes.byref(mbi), 0,
                                      ctypes.byref(mbits), None, 0)
        ctypes.memset(mbits.value, 0, ((size + 31) // 32) * 4 * size)

        ii = ICONINFO()
        ii.fIcon = False
        ii.xHotspot = size // 2
        ii.yHotspot = size // 2
        ii.hbmMask = mask
        ii.hbmColor = colour
        h = user32.CreateIconIndirect(ctypes.byref(ii))
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(colour)
        gdi32.DeleteObject(mask)
        gdi32.DeleteDC(mem)
        return h
    finally:
        user32.ReleaseDC(None, dc)


def make_copy_of(live: wt.HANDLE, ii: ICONINFO) -> wt.HANDLE:
    """Same artwork, but a NON-SYSTEM cursor - so Windows draws it in
    software instead of on the hardware cursor plane."""
    copy_ii = ICONINFO()
    copy_ii.fIcon = False
    copy_ii.xHotspot = ii.xHotspot
    copy_ii.yHotspot = ii.yHotspot
    copy_ii.hbmMask = ii.hbmMask
    copy_ii.hbmColor = ii.hbmColor
    return user32.CreateIconIndirect(ctypes.byref(copy_ii))


def probe(screen, shape, sw, sh, hotx, hoty, x, y):
    _, dt, dm, lt, lm = cursorframe.match_score(
        shape, sw, sh, screen.grab(x, y), BOX, hotx, hoty)
    cdt = clt = 0.0
    for dx, dy in ((0, -BOX), (0, BOX), (-BOX, 0)):
        _, dt2, dm2, lt2, lm2 = cursorframe.match_score(
            shape, sw, sh, screen.grab(x + dx, y + dy), BOX, hotx, hoty)
        cdt = max(cdt, (dm2 / dt2) if dt2 else 0.0)
        clt = max(clt, (lm2 / lt2) if lt2 else 0.0)
    return ((dm / dt) if dt else 0.0), ((lm / lt) if lt else 0.0), cdt, clt


def main() -> int:
    pos = POINT()
    if not user32.GetCursorPos(ctypes.byref(pos)):
        print("cannot read the pointer position")
        return 1
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)):
        print("cannot read the cursor")
        return 1
    ii = ICONINFO()
    if not user32.GetIconInfo(ci.hCursor, ctypes.byref(ii)):
        print("cannot read the cursor icon info")
        return 1
    shape, sw, sh = cursorframe.cursor_shape(ci.hCursor)
    if not shape:
        print("no cursor shape")
        return 1

    original = ci.hCursor
    screen = Screen()
    magenta = arrow_soft = None
    try:
        print(f"pointer at ({pos.x},{pos.y})   flags=0x{ci.flags:08X} "
              f"(SHOWING={int(bool(ci.flags & 1))} "
              f"SUPPRESSED={int(bool(ci.flags & 2))})")
        print()

        # --- baseline: the hardware cursor -------------------------------
        jiggle(pos.x, pos.y)
        base = probe(screen, shape, sw, sh, ii.xHotspot, ii.yHotspot, pos.x, pos.y)
        print("  [1] HARDWARE cursor (as-is)         "
              f"dark {base[0]:.2f}/ctrl {base[2]:.2f}   "
              f"light {base[1]:.2f}/ctrl {base[3]:.2f}")

        # --- calibration: can the reader see a software cursor? ---------
        # SetCursor only affects windows OWNED BY THE CALLING THREAD, so
        # the handle must be checked afterwards - otherwise "0 magenta
        # pixels" means "SetCursor was ignored", not "the reader is blind".
        magenta = make_magenta_cursor()
        user32.SetCursor(magenta)
        jiggle(pos.x, pos.y)
        magenta_px = count_magenta(screen.grab(pos.x, pos.y))
        ci2 = CURSORINFO()
        ci2.cbSize = ctypes.sizeof(CURSORINFO)
        user32.GetCursorInfo(ctypes.byref(ci2))
        took = (ci2.hCursor == magenta)
        print(f"  [2] CALIBRATION: software magenta square -> "
              f"{magenta_px} px visible in the grab")
        print(f"      SetCursor took effect? "
              f"{'YES' if took else 'NO - the cursor under the pointer is unchanged'}"
              f"  (live handle 0x{ci2.hCursor:X}, ours 0x{magenta:X})")
        can_see_software = magenta_px > 20
        calibration_valid = took

        if can_see_software:
            # --- the real test: the arrow, drawn in software -------------
            arrow_soft = make_copy_of(original, ii)
            user32.SetCursor(arrow_soft)
            jiggle(pos.x, pos.y)
            soft = probe(screen, shape, sw, sh, ii.xHotspot, ii.yHotspot, pos.x, pos.y)
            print("  [3] SOFTWARE arrow (same artwork)     "
                  f"dark {soft[0]:.2f}/ctrl {soft[2]:.2f}   "
                  f"light {soft[1]:.2f}/ctrl {soft[3]:.2f}")
        else:
            soft = None
    finally:
        user32.SetCursor(original)
        time.sleep(0.1)
        jiggle(pos.x, pos.y)
        after = probe(screen, shape, sw, sh, ii.xHotspot, ii.yHotspot, pos.x, pos.y)
        print("  [4] restored to the original cursor   "
              f"dark {after[0]:.2f}/ctrl {after[2]:.2f}   "
              f"light {after[1]:.2f}/ctrl {after[3]:.2f}")
        for h in (magenta, arrow_soft):
            if h:
                user32.DestroyCursor(h)
        screen.close()

    print()
    print("=" * 74)
    if not calibration_valid:
        print("  THE CALIBRATION IS VOID: SetCursor was ignored (the window")
        print("  under the pointer belongs to another thread), so the magenta")
        print("  cursor was never drawn and '0 magenta pixels' proves nothing")
        print("  either way.  Run scripts/cursor_is_composited.py instead - it")
        print("  uses a spatial offset sweep that needs no input synthesis.")
    elif not can_see_software:
        print("  The screen reader CANNOT see a software-drawn cursor (the")
        print("  magenta square was invisible in the grab).  So the absence of")
        print("  the arrow glyph from a framebuffer read proves nothing about")
        print("  whether the pointer is covered - the framebuffer route is blind")
        print("  here, and only a BY-EYE test can decide.")
    elif soft[1] > 0.20 and soft[1] > soft[3] + 0.10:
        print("  The arrow IS drawn when the OS draws it in software.")
        print("  Nothing is covering the pointer: the fault is the HARDWARE")
        print("  CURSOR PLANE for this region            ->  mechanism B.")
    else:
        print("  The arrow is NOT drawn even as a software cursor, yet the")
        print("  reader CAN see software cursors.  Something really is")
        print("  composited OVER the pointer               ->  mechanism A.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

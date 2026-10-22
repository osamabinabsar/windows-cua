"""Is the pointer COMPOSITED into the screen image, or on the hardware
cursor plane?  And is the earlier magenta calibration valid?

WHY THIS SCRIPT EXISTS
----------------------
`cursor_software_probe.py` tried to answer that by drawing a solid
magenta cursor and looking for it in a screen grab.  It found 0 magenta
pixels and concluded "the screen reader is blind to cursors".

That conclusion is VOID, and this script says so out loud:

    SetCursor() sets the cursor shape for the WINDOWS OWNED BY THE
    CALLING THREAD.  A console script owns no window at the pointer's
    position, so the magenta cursor was never drawn.  "0 magenta
    pixels" therefore measured nothing.

The script below checks that directly (does GetCursorInfo report our
handle afterwards?) and then replaces the calibration with a test that
needs no input synthesis at all.

THE TEST THAT WORKS - SPATIAL OFFSET DISCRIMINATION
----------------------------------------------------
The arrow's outline is ONE pixel wide.  So if the arrow really is in
the framebuffer, matching the cursor's own bitmap against the screen at
the pointer's exact position matches far better than matching it 2, 3,
4 or 8 pixels away - a one-pixel-wide feature cannot survive a
two-pixel shift.

    offset 0 dominates, on BOTH the dark and the light half
        -> the arrow IS composited into the image.  If it is invisible
           to the eye, something is drawn OVER it (mechanism A or C).
    every offset scores about the same
        -> the scores are the BACKGROUND, not the arrow.  The pointer
           is on the hardware cursor plane and is not in the image.

The control positions in the older scripts (64 px away) do NOT work on
their own: a uniformly dark background matches the arrow's dark outline
perfectly, which is exactly what produced the earlier "false proof".

WHAT THIS SCRIPT TOUCHES
------------------------
  * reads the screen into a 64x64 crop around the pointer and reports
    ONLY counts and match ratios - never any screen content;
  * calls SetCursor() once with a synthetic handle and restores the
    original immediately, purely to test whether SetCursor takes effect
    at all from a foreign thread (it does not - that is the point);
  * moves the pointer by ONE pixel and back, so the OS redraws it, and
    ends at the exact original coordinates.

No clicks, no keys, no scrolling, no window is moved or activated.
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


def score(shape, sw, sh, crop, hotx, hoty, dx=0, dy=0):
    _, dt, dm, lt, lm = cursorframe.match_score(
        shape, sw, sh, crop, BOX, hotx, hoty, dx, dy)
    return ((dm / dt) if dt else 0.0), ((lm / lt) if lt else 0.0)


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
    magenta = None
    try:
        print(f"pointer at ({pos.x},{pos.y})   flags=0x{ci.flags:08X} "
              f"(SHOWING={int(bool(ci.flags & 1))} "
              f"SUPPRESSED={int(bool(ci.flags & 2))})")
        print(f"live cursor handle 0x{original:X}")
        print()

        # --- 1. spatial offset discrimination ----------------------------
        print("  [1] SPATIAL OFFSET SWEEP  (match the arrow's own bitmaps")
        print("      against the screen, shifted by N pixels)")
        jiggle(pos.x, pos.y)
        crop = screen.grab(pos.x, pos.y)
        offsets = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1),
                   (2, 0), (-2, 0), (0, 2), (0, -2),
                   (4, 0), (-4, 0), (0, 4), (0, -4),
                   (8, 0), (-8, 0), (0, 8), (0, -8)]
        rows = []
        for (dx, dy) in offsets:
            d, l = score(shape, sw, sh, crop, ii.xHotspot, ii.yHotspot, dx, dy)
            rows.append((dx, dy, d, l))
            tag = "  <-- exact position" if (dx, dy) == (0, 0) else ""
            print(f"      offset ({dx:+3d},{dy:+3d})  dark {d:.2f}  light {l:.2f}{tag}")
        best_d = max(r[2] for r in rows if (r[0], r[1]) != (0, 0))
        best_l = max(r[3] for r in rows if (r[0], r[1]) != (0, 0))
        d0, l0 = score(shape, sw, sh, crop, ii.xHotspot, ii.yHotspot, 0, 0)
        print()
        print(f"      exact position : dark {d0:.2f}  light {l0:.2f}")
        print(f"      best off-centre : dark {best_d:.2f}  light {best_l:.2f}")
        if d0 > best_d + 0.10 and l0 > best_l + 0.10:
            print("      => the arrow IS composited into the image "
                  "(offset 0 dominates on BOTH halves)")
            composited = True
        else:
            print("      => the arrow is NOT in the image; every offset "
                  "scores the same,")
            print("         i.e. these are the BACKGROUND, not the arrow")
            composited = False

        # --- 2. was the magenta calibration valid? -----------------------
        print()
        magenta = make_magenta_cursor()
        returned = user32.SetCursor(magenta)
        jiggle(pos.x, pos.y)
        ci2 = CURSORINFO()
        ci2.cbSize = ctypes.sizeof(CURSORINFO)
        user32.GetCursorInfo(ctypes.byref(ci2))
        magenta_px = count_magenta(screen.grab(pos.x, pos.y))
        print("  [2] VALIDITY OF THE OLD MAGENTA CALIBRATION")
        print(f"      SetCursor() returned      : 0x{returned:X}"
              f"   (0 = FAILED, the cursor was not set)")
        print(f"      GetCursorInfo().hCursor   : 0x{ci2.hCursor:X}"
              f"   (our magenta handle is 0x{magenta:X})")
        took = (ci2.hCursor == magenta)
        print(f"      magenta pixels in the grab: {magenta_px}")
        if not took:
            print("      => SetCursor was IGNORED: the window under the pointer")
            print("         belongs to another thread, so our cursor was never")
            print("         drawn.  The old '0 magenta pixels' result was VOID -")
            print("         it did not prove the reader is blind to cursors.")
        else:
            print("      => SetCursor took effect on the window under the pointer.")
            if magenta_px == 0:
                print("         ...and yet no magenta was visible: the reader IS")
                print("         blind to software-drawn cursors.")
            else:
                print("         ...and magenta WAS visible: the reader can see")
                print("         software cursors, so the arrow's absence is real.")
    finally:
        user32.SetCursor(original)
        try:
            gdi32.DeleteObject(magenta)
        except Exception:
            pass
        screen.close()

    print()
    print("=" * 74)
    if composited:
        print("  VERDICT: the arrow IS in the framebuffer at the pointer.")
        print("  If it is invisible to the eye, something is drawn OVER it")
        print("  (mechanism A) or the app suppresses it (mechanism C).")
    else:
        print("  VERDICT: the pointer is NOT in the framebuffer - it lives on")
        print("  the adapter's hardware cursor plane, as Raymond Chen's")
        print("  'The Case of the Disappearing Cursor' describes:")
        print("    'the pixels of the mouse cursor do not actually exist in")
        print("     the frame buffer because the hardware is responsible for")
        print("     performing the overlay'.")
        print("  So NO screen read - screenshot, PrintScreen, GDI BitBlt - can")
        print("  ever show the arrow.  Only the hardware cursor plane and your")
        print("  eyes can.  Any claim of the form 'the arrow is missing from")
        print("  the capture, therefore something is covering it' is unsound.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

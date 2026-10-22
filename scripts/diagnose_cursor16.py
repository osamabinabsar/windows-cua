"""Sixteenth pass: is the pointer composited into the image, or is it on
the hardware cursor plane?

Runs the cursor-framebuffer template match at the pointer's current
position, and also at a control position a little way away from the
pointer, to separate "the glyph is in the image" from "there is a
high-contrast thing near the pointer".

Read-only. Only counts and ratios are printed; no screen pixels.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
import cursorframe  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

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


def grab(x, y):
    """Read a BOX x BOX region of the composited screen."""
    dc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(dc)
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = BOX
    bi.bmiHeader.biHeight = -BOX
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(
        dc, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0
    )
    old = gdi32.SelectObject(mem, bmp)
    try:
        gdi32.BitBlt(mem, 0, 0, BOX, BOX, dc, x - BOX // 2, y - BOX // 2,
                     0x00CC0020 | 0x40000000)
        data = ctypes.string_at(bits, BOX * BOX * 4)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, dc)
    return data


def main() -> int:
    pos = POINT()
    user32.GetCursorPos(ctypes.byref(pos))
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    user32.GetCursorInfo(ctypes.byref(ci))
    ii = ICONINFO()
    user32.GetIconInfo(ci.hCursor, ctypes.byref(ii))

    print("=" * 78)
    print("POINTER AT", (pos.x, pos.y))
    print("=" * 78)
    print(f"  hCursor        : 0x{ci.hCursor:X}")
    print(f"  flags          : 0x{ci.flags:08X}  (SHOWING={int(bool(ci.flags & 1))}"
          f" SUPPRESSED={int(bool(ci.flags & 2))})")
    print(f"  hotspot        : ({ii.xHotspot}, {ii.yHotspot})")
    print(f"  hbmColor       : {'present' if ii.hbmColor else 'NULL (monochrome cursor)'}")
    print(f"  hbmMask        : {'present' if ii.hbmMask else 'NULL'}")

    shape, sw, sh = cursorframe.cursor_shape(ci.hCursor)
    opaque = sum(1 for p in shape if p[5] != 0)
    print(f"  glyph          : {sw}x{sh}, {opaque} opaque pixels")

    print()
    print("=" * 78)
    print("TEMPLATE MATCH: is the glyph present in the composited screen?")
    print("=" * 78)

    crop = grab(pos.x, pos.y)
    o, dt, dm, lt, lm = cursorframe.match_score(
        shape, sw, sh, crop, BOX, ii.xHotspot, ii.yHotspot
    )
    print(f"  AT the pointer ({pos.x},{pos.y})      : dark {dm}/{dt}   light {lm}/{lt}")

    ctrl = []
    for dx, dy in ((0, -BOX), (0, BOX), (-BOX, 0), (BOX, 0)):
        c2 = grab(pos.x + dx, pos.y + dy)
        r = cursorframe.match_score(shape, sw, sh, c2, BOX, ii.xHotspot, ii.yHotspot)
        ctrl.append(r)
        print(f"  control {dx:+4},{dy:+4}                : dark {r[2]}/{r[1]}   light {r[4]}/{r[3]}")

    def rate(num, den):
        return (num / den) if den else 0.0

    dr = rate(dm, dt)
    lr = rate(lm, lt)
    cdr = max((rate(r[2], r[1]) for r in ctrl), default=0.0)
    clr = max((rate(r[4], r[3]) for r in ctrl), default=0.0)

    print()
    print(f"  dark-pixel match rate : pointer {dr:.3f}  best control {cdr:.3f}")
    print(f"  light-pixel match rate: pointer {lr:.3f}  best control {clr:.3f}")
    print()
    if dr > 0.6 and lr > 0.6 and dr > cdr + 0.2 and lr > clr + 0.2:
        print("  VERDICT: the glyph IS in the composited image.")
        print("           The pointer is a SOFTWARE cursor drawn by DWM into the")
        print("           screen surface. If it is invisible on screen, something")
        print("           is covering it in the DWM composition order.")
    else:
        print("  VERDICT: the glyph is NOT in the composited image.")
        print("           The pointer is on the adapter's HARDWARE CURSOR PLANE,")
        print("           so it can only be hidden by a display plane scanned")
        print("           out ABOVE the cursor plane (an overlay-plane ordering")
        print("           problem in the display driver).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

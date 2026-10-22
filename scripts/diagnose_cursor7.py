"""Seventh pass: is the pointer in the framebuffer, or on a hardware plane?

This is the decisive experiment.

GetCursorInfo says the pointer is SHOWING, with a normal arrow. Two
different things can then be true:

  A. The pointer is COMPOSITED INTO THE FRAMEBUFFER. The pixels of the
     arrow are part of the desktop image. Reading those pixels back
     shows the arrow. In this case, anything that repaints the region
     under the pointer ERASES the arrow -- the pointer is genuinely
     gone from the image, and the app that painted there hid it.

  B. The pointer is on a separate HARDWARE CURSOR PLANE, composited by
     the display driver above the framebuffer. Reading the framebuffer
     at the cursor position shows only the app's pixels, with no arrow.
     In this case the pointer is still on screen, but an app's surface
     promoted to a hardware OVERLAY PLANE is drawn ABOVE the cursor
     plane, and the pointer is occluded by the app.

GetPixel on the screen DC reads the framebuffer, so it distinguishes A
from B directly. Sampling a small grid around the cursor prints it as
ASCII.

Read-only: it only reads pixels.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hCursor", wt.HANDLE),
        ("ptScreenPos", POINT),
    ]


def main() -> int:
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wt.BOOL
    user32.GetDC.argtypes = [wt.HWND]
    user32.GetDC.restype = wt.HDC
    gdi32.GetPixel.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.GetPixel.restype = wt.DWORD

    pos = POINT()
    user32.GetCursorPos(ctypes.byref(pos))
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetCursorInfo.restype = wt.BOOL
    user32.GetCursorInfo(ctypes.byref(info))

    print(f"  cursor at ({pos.x}, {pos.y})  flags=0x{info.flags:08X}")
    hdc = user32.GetDC(None)

    print()
    print("  Framebuffer sample, 28x28 around the cursor:")
    print("  ('#' = dark, '.' = light, '?' = anything else)")

    grid = []
    for dy in range(-14, 14):
        row = []
        for dx in range(-14, 14):
            px = gdi32.GetPixel(hdc, pos.x + dx, pos.y + dy)
            if px == 0xFFFFFFFF:
                row.append("?")
                continue
            r = px & 0xFF
            g = (px >> 8) & 0xFF
            b = (px >> 16) & 0xFF
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            row.append("#" if lum < 100 else ".")
        grid.append(row)

    for row in grid:
        print("    " + "".join(row))

    # A standard arrow cursor is a black-outlined white arrow, so it shows
    # up as a small dark-outline shape on a light background (or the
    # reverse on dark backgrounds). Count the transitions.
    print()
    dark = sum(1 for row in grid for c in row if c == "#")
    light = sum(1 for row in grid for c in row if c == ".")
    print(f"  dark pixels in the 28x28 sample : {dark}")
    print(f"  light pixels in the 28x28 sample: {light}")

    # Is the darkest cluster arrow-shaped? A crude proxy: the arrow tip is
    # at the hotspot, which for IDC_ARROW is the top-left pixel of the
    # 32x32 image, i.e. offset (0,0) from the cursor position.
    def px(dx, dy):
        v = gdi32.GetPixel(hdc, pos.x + dx, pos.y + dy)
        if v == 0xFFFFFFFF:
            return None
        r = v & 0xFF
        g = (v >> 8) & 0xFF
        b = (v >> 16) & 0xFF
        return 0.299 * r + 0.587 * g + 0.114 * b

    print()
    print("  Luminance profile along the arrow's diagonal (hotspot is at 0,0):")
    line = []
    for i in range(0, 14):
        v = px(i, i)
        line.append("?" if v is None else f"{int(v):3d}")
    print("    " + " ".join(line))

    print()
    print("  Reading: if the 28x28 sample shows NO arrow shape while")
    print("  GetCursorInfo says SHOWING, the pointer is on a HARDWARE CURSOR")
    print("  PLANE and is being occluded by a hardware overlay plane.")
    print("  If the sample DOES show an arrow, the pointer is IN the")
    print("  framebuffer, and something is repainting over it.")

    user32.ReleaseDC(None, hdc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Cursor-in-framebuffer test.

Decides, at the pointer's exact position, whether the arrow is
COMPOSITED INTO the screen image or lives on the adapter's hardware
cursor plane.

Method: read the live cursor's own bitmaps, rebuild the shape it should
draw (a 32x32 glyph), and look for that shape in a small read of the
screen device context.

  * If the shape is present in the screen pixels  -> the pointer is a
    SOFTWARE cursor that DWM composites into the image. If it is then
    invisible to the eye, something is covering it in the DWM
    composition order.
  * If the shape is absent  -> the pointer is on the HARDWARE cursor
    plane, and the only way it can be hidden is a display plane
    scanned out above the cursor plane.

Nothing here prints screen content: only counts and match scores.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

DIB_RGB_COLORS = 0
BI_RGB = 0
SM_CXSCREEN = 0


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


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wt.DWORD), ("bmWidth", ctypes.c_long),
        ("bmHeight", ctypes.c_long), ("bmWidthBytes", ctypes.c_long),
        ("bmPlanes", wt.WORD), ("bmBitsPixel", wt.WORD),
        ("bmBits", wt.LPVOID),
    ]


user32.GetIconInfo.argtypes = [wt.HANDLE, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wt.BOOL
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]

gdi32.GetObjectW.argtypes = [wt.HGDIOBJ, ctypes.c_int, ctypes.c_void_p]
gdi32.GetObjectW.restype = wt.DWORD
gdi32.GetDIBits.argtypes = [
    wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFO), wt.UINT,
]
gdi32.GetDIBits.restype = wt.BOOL


def _bitmap_size(hbm) -> tuple[int, int, int]:
    bmp = BITMAP()
    gdi32.GetObjectW(hbm, ctypes.sizeof(BITMAP), ctypes.byref(bmp))
    return (bmp.bmWidth, bmp.bmHeight, bmp.bmBitsPixel)


def _read_dib(hdc, hbm, bpp: int, width: int, height: int) -> bytes | None:
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = width
    bi.bmiHeader.biHeight = -height  # top-down
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = bpp
    bi.bmiHeader.biCompression = BI_RGB
    stride = ((width * bpp + 31) // 32) * 4
    buf = ctypes.create_string_buffer(stride * height)
    if not gdi32.GetDIBits(
        hdc, hbm, 0, height, buf, ctypes.byref(bi), DIB_RGB_COLORS
    ):
        return None
    return bytes(buf)


def cursor_shape(hcursor) -> tuple[list[tuple[int, int, int, int]], int, int]:
    """Rebuild the cursor glyph.

    Returns (pixels, width, height) where each pixel is
    (x, y, b, g, r, a) -- a is 0 for transparent.

    Modern (32bpp) cursors: hbmColor is an ARGB DIB whose alpha channel
    already carries the transparency, so the alpha alone gives the
    shape.  The AND mask is only consulted when that yields nothing.

    Legacy monochrome cursors: hbmColor is NULL and hbmMask holds the
    AND mask in its top half and the XOR mask in its bottom half.
    """
    ii = ICONINFO()
    if not user32.GetIconInfo(hcursor, ctypes.byref(ii)):
        return ([], 0, 0)

    hdc = user32.GetDC(None)
    try:
        if ii.hbmColor:
            w, h, bpp = _bitmap_size(ii.hbmColor)
            raw = _read_dib(hdc, ii.hbmColor, 32, w, h)
            if raw is None:
                return ([], 0, 0)
            px = []
            for i in range(w * h):
                b, g, r, a = raw[i * 4], raw[i * 4 + 1], raw[i * 4 + 2], raw[i * 4 + 3]
                px.append((i % w, i // w, b, g, r, a))
            if any(p[5] for p in px):
                return (px, w, h)
            # alpha is flat: fall back to the AND mask
            if ii.hbmMask:
                mw, mh, _ = _bitmap_size(ii.hbmMask)
                mraw = _read_dib(hdc, ii.hbmMask, 1, mw, mh)
                if mraw is not None:
                    stride = ((mw + 31) // 32) * 4
                    half = mh // 2 if mh > h else mh
                    out = []
                    for (x, y, b, g, r, a) in px:
                        if y < half:
                            byte = mraw[y * stride + (x >> 3)]
                            bit = (byte >> (7 - (x & 7))) & 1
                            if bit:
                                # cursor convention: mask bit 1 = transparent
                                out.append((x, y, b, g, r, 0))
                                continue
                        out.append((x, y, b, g, r, 255 if a == 0 else a))
                    px = out
            return (px, w, h)

        # legacy monochrome
        if ii.hbmMask:
            w, h, _ = _bitmap_size(ii.hbmMask)
            raw = _read_dib(hdc, ii.hbmMask, 1, w, h)
            if raw is None:
                return ([], 0, 0)
            stride = ((w + 31) // 32) * 4
            half = h // 2
            px = []
            for y in range(half):
                for x in range(w):
                    and_b = raw[y * stride + (x >> 3)]
                    and_bit = (and_b >> (7 - (x & 7))) & 1
                    xor_b = raw[(y + half) * stride + (x >> 3)]
                    xor_bit = (xor_b >> (7 - (x & 7))) & 1
                    if and_bit:
                        if xor_bit:
                            px.append((x, y, 0, 0, 0, 255))
                        else:
                            px.append((x, y, 255, 255, 255, 255))
                    else:
                        px.append((x, y, 0, 0, 0, 0))
            return (px, w, half)
    finally:
        user32.ReleaseDC(None, hdc)
    return ([], 0, 0)


def match_score(shape, sw, sh, crop: bytes, box: int, hotx: int, hoty: int,
                dx: int = 0, dy: int = 0):
    """Match the cursor glyph against a box x box screen crop.

    The crop was taken centred on the pointer, so the cursor's hotspot
    sits at (box // 2, box // 2) in crop coordinates.  dx/dy shift the
    glyph inside the crop, which is how a caller tests whether a match
    is really the glyph or just the background: the arrow's outline is
    ONE pixel wide, so a real match at (0,0) collapses at a 2-pixel
    shift, while a background match does not care where you look.

    The score is split into the glyph's DARK pixels and its LIGHT
    pixels.  A background cannot fake both: a light background matches
    the light half of the arrow and a dark background matches the dark
    half, but a real, composited arrow matches BOTH halves at once.
    That difference is what distinguishes "the arrow is in the image"
    from "the arrow is on the hardware cursor plane".

    Returns (opaque, dark_total, dark_matched, light_total, light_matched).
    """
    if not shape or not crop:
        return (0, 0, 0, 0, 0)
    cx = box // 2 - hotx + dx
    cy = box // 2 - hoty + dy
    opaque = dark_total = dark_matched = light_total = light_matched = 0
    for (x, y, b, g, r, a) in shape:
        if a == 0:
            continue
        opaque += 1
        sx = cx + x
        sy = cy + y
        if sx < 0 or sy < 0 or sx >= box or sy >= box:
            continue
        i = (sy * box + sx) * 4
        sb, sg, sr = crop[i], crop[i + 1], crop[i + 2]
        lum = sr + sg + sb
        if r < 90 and g < 90 and b < 90:
            dark_total += 1
            if lum < 150:
                dark_matched += 1
        elif r > 190 and g > 190 and b > 190:
            light_total += 1
            if lum > 560:
                light_matched += 1
    return (opaque, dark_total, dark_matched, light_total, light_matched)

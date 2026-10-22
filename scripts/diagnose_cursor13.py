"""Thirteenth pass: grab the composited framebuffer, fast, and look for
the agent overlay's painted cursor.

BitBlt from the screen DC into a 32bpp DIB is orders of magnitude
faster than per-pixel GetPixel, and with numpy it can be diffed
frame-to-frame.

Three questions:
  1. Is there an ARROW-SHAPED SPRITE anywhere in the composited image?
     The Cua.AgentCursorOverlay exists precisely to draw the agent's
     synthetic cursor. If it is painting into the DWM composition, that
     sprite is in the framebuffer.
  2. What does the framebuffer actually contain at the pointer's
     position -- the app's pixels, or something else?
  3. Does anything change between frames (an animated/blinking agent
     cursor, a moving highlight)?

Note: the hardware cursor is NEVER in this image -- GetPixel/BitBlt on
the screen DC read the DWM-composited surface, which excludes the
display's cursor plane. So a missing arrow here is expected and says
nothing on its own; the search for an EXTRA sprite is the point.

Read-only: it only reads pixels.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [
    wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT, ctypes.POINTER(ctypes.c_void_p),
    wt.HANDLE, wt.DWORD,
]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.BitBlt.argtypes = [
    wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD,
]
gdi32.BitBlt.restype = wt.BOOL
gdi32.GetDIBits.argtypes = [
    wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFOHEADER), wt.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]


def grab(width: int, height: int) -> np.ndarray:
    """Return the virtual screen as an (height, width, 3) uint8 array."""
    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # negative = top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(
        screen_dc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
    )
    old = gdi32.SelectObject(mem_dc, hbmp)
    gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, 0, 0, SRCCOPY | CAPTUREBLT)
    gdi32.SelectObject(mem_dc, old)

    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(
        mem_dc, hbmp, 0, height, buf, ctypes.byref(bmi.bmiHeader), 0
    )
    arr = np.frombuffer(buf.raw, dtype=np.uint8).reshape(height, width, 4)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(None, screen_dc)
    return arr[:, :, :3].copy()


def ascii_crop(img: np.ndarray, cx: int, cy: int, half: int = 16) -> None:
    h, w, _ = img.shape
    x0, y0 = max(0, cx - half), max(0, cy - half)
    x1, y1 = min(w, cx + half), min(h, cy + half)
    patch = img[y0:y1, x0:x1]
    lum = (
        0.299 * patch[:, :, 0] + 0.587 * patch[:, :, 1] + 0.114 * patch[:, :, 2]
    )
    print(f"    framebuffer {x1-x0}x{y1-y0} at ({x0},{y0})")
    for row in lum:
        print("      " + "".join("#" if v < 70 else ("+" if v < 160 else ".") for v in row))


def find_sprites(img: np.ndarray) -> list[tuple[int, int, int]]:
    """Look for compact, very bright clusters -- the shape a drawn
    white arrow would have. Report (x, y, area)."""
    lum = (
        0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]
    ).astype(np.float32)
    mask = lum > 235
    # crude 8-connected component labelling
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    ys, xs = np.nonzero(mask)
    for sy, sx in zip(ys, xs):
        if seen[sy, sx]:
            continue
        stack = [(sy, sx)]
        seen[sy, sx] = True
        cells = []
        while stack:
            y, x = stack.pop()
            cells.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if 20 <= len(cells) <= 4000:
            cy = sum(c[0] for c in cells) / len(cells)
            cx = sum(c[1] for c in cells) / len(cells)
            out.append((int(cx), int(cy), len(cells)))
    return out


def main() -> int:
    vw = user32.GetSystemMetrics(78)  # SM_XVIRTUALSCREEN
    vh = user32.GetSystemMetrics(79)  # SM_YVIRTUALSCREEN
    vx = user32.GetSystemMetrics(76)
    vy = user32.GetSystemMetrics(77)
    print(f"virtual screen: origin ({vx},{vy}) size {vw}x{vh}")

    pos = POINT()
    user32.GetCursorPos(ctypes.byref(pos))
    print(f"pointer at ({pos.x}, {pos.y})")

    print()
    print("grabbing frame 1 ...")
    f1 = grab(vw, vh)
    print("grabbing frame 2 (2 s later) ...")
    import time

    time.sleep(2.0)
    f2 = grab(vw, vh)

    print()
    print("=" * 74)
    print("A. FRAMEBUFFER AT THE POINTER")
    print("=" * 74)
    ascii_crop(f1, pos.x - vx, pos.y - vy)

    print()
    print("=" * 74)
    print("B. WHAT CHANGED BETWEEN THE TWO FRAMES")
    print("=" * 74)
    d = np.abs(f1.astype(np.int16) - f2.astype(np.int16)).sum(axis=2)
    changed = d > 12
    n = int(changed.sum())
    print(f"  pixels changed: {n}  ({100.0*n/(vw*vh):.4f}% of the screen)")
    if n:
        ys, xs = np.nonzero(changed)
        print(f"  changed bounding box (screen coords): "
              f"({xs.min()+vx},{ys.min()+vy}) - ({xs.max()+vx},{ys.max()+vy})")
        # cluster roughly by taking the densest 40x40 window
        k = 40
        dens = np.zeros_like(changed, dtype=np.int32)
        integral = changed.astype(np.int32)
        integral = np.pad(integral, ((1, 0), (1, 0)))
        cum = integral.cumsum(0).cumsum(1)
        best = (0, 0, 0)
        for y in range(0, vh - k, 20):
            for x in range(0, vw - k, 20):
                s = (
                    cum[y + k, x + k] - cum[y, x + k] - cum[y + k, x] + cum[y, x]
                )
                if s > best[0]:
                    best = (int(s), x, y)
        print(f"  densest {k}x{k} region: ({best[1]+vx},{best[2]+vy}) with {best[0]} changed px")
        print("  -> an animating/agent-drawn element lives here:")
        ascii_crop(f1, best[1] + k // 2 - vx, best[2] + k // 2 - vy, half=24)

    print()
    print("=" * 74)
    print("C. BRIGHT SPRITES IN THE COMPOSITED IMAGE (candidate drawn cursors)")
    print("=" * 74)
    sprites = find_sprites(f1)
    sprites = [s for s in sprites if s[2] >= 40]
    sprites.sort(key=lambda s: -s[2])
    print(f"  {len(sprites)} bright clusters with area >= 40px")
    for x, y, a in sprites[:25]:
        print(f"    ({x+vx:>5},{y+vy:>5})  area={a}")
    if sprites:
        print()
        print("  crop of the largest:")
        x, y, a = sprites[0]
        ascii_crop(f1, x - vx, y - vy, half=20)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

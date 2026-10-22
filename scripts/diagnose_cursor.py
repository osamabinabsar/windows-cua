"""Read-only diagnosis of an invisible mouse pointer.

Two questions:
  1. Is the pointer genuinely "not showing" per the OS, or is the OS
     drawing a cursor that is entirely transparent?
  2. Is there an invisible/overlaid top-level window sitting over the
     pointer, which can occlude the hardware cursor plane?

Nothing here modifies anything.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)


def _declare() -> None:
    """64-bit safe prototypes: without these, HANDLE/pointer args truncate."""
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetCursorInfo.restype = wt.BOOL
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.LoadCursorFromFileW.argtypes = [wt.LPCWSTR]
    user32.LoadCursorFromFileW.restype = wt.HANDLE
    user32.GetIconInfo.argtypes = [wt.HANDLE, ctypes.POINTER(ICONINFO)]
    user32.GetIconInfo.restype = wt.BOOL
    user32.GetDC.argtypes = [wt.HWND]
    user32.GetDC.restype = wt.HDC
    gdi32.GetDIBits.argtypes = [
        wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
        ctypes.c_void_p, ctypes.POINTER(BITMAPINFOHEADER), wt.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int

# -- CURSORINFO -------------------------------------------------------------

CURSOR_SHOWING = 0x00000001
CURSOR_SUPPRESSED = 0x00000002


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hCursor", wt.HANDLE),
        ("hScreen", wt.HANDLE),
        ("ptScreenPos", POINT),
    ]


def live_cursor_state() -> None:
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(info)):
        print("  GetCursorInfo failed:", ctypes.get_last_error())
        return
    flags = []
    if info.flags & CURSOR_SHOWING:
        flags.append("CURSOR_SHOWING")
    if info.flags & CURSOR_SUPPRESSED:
        flags.append("CURSOR_SUPPRESSED")
    print(f"  flags      : 0x{info.flags:08x}  {' '.join(flags) or '<neither>'}")
    print(f"  position   : ({info.ptScreenPos.x}, {info.ptScreenPos.y})")
    print(f"  hCursor    : {info.hCursor}")
    if not info.flags & CURSOR_SHOWING:
        print("  => the OS reports the pointer as NOT showing")
    if info.flags & CURSOR_SUPPRESSED:
        print("  => the pointer is SUPPRESSED (an app asked for it to be hidden)")


# -- is the current cursor image actually blank? -----------------------------

class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wt.BOOL),
        ("xHotspot", wt.DWORD),
        ("yHotspot", wt.DWORD),
        ("hbmMask", wt.HBITMAP),
        ("hbmColor", wt.HBITMAP),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


def cursor_is_blank(path: str) -> str:
    """Load a .cur/.ani and report whether it draws anything at all."""
    h = user32.LoadCursorFromFileW(path)
    if not h:
        return f"<LoadCursorFromFile failed: {ctypes.get_last_error()}>"

    ii = ICONINFO()
    if not user32.GetIconInfo(h, ctypes.byref(ii)):
        return f"<GetIconInfo failed: {ctypes.get_last_error()}>"

    # The AND mask holds the shape; the XOR mask holds the colour. A cursor
    # is visible when either mask has a set bit. A fully zero mask pair is
    # a completely transparent cursor.
    bih = BITMAPINFOHEADER()
    bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bih.biWidth = 32
    bih.biHeight = -32  # negative: top-down
    bih.biPlanes = 1
    bih.biBitCount = 32

    for label, hbm in (("AND", ii.hbmMask), ("XOR", ii.hbmColor)):
        if not hbm:
            continue
        buf = ctypes.create_string_buffer(32 * 32 * 4)
        got = gdi32.GetDIBits(
            user32.GetDC(None), hbm, 0, 32, buf, ctypes.byref(bih), 0
        )
        if not got:
            continue
        data = buf.raw
        nonzero = sum(1 for i in range(0, len(data), 4) if data[i : i + 4] != b"\x00\x00\x00\x00")
        if label == "AND":
            and_nonzero = nonzero
        else:
            xor_nonzero = nonzero

    if and_nonzero == 0 and xor_nonzero == 0:
        return "*** BLANK: both masks are entirely zero -> invisible cursor ***"
    return f"visible (AND mask {and_nonzero} px, XOR mask {xor_nonzero} px)"


# -- window enumeration ------------------------------------------------------

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000

GWL_EXSTYLE = -20
GWL_STYLE = -16

DWMWA_CLOAKED = 14
DWMWA_EXTENDED_FRAME_BOUNDS = 9


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def enumerate_windows() -> list[dict]:
    found: list[dict] = []

    def callback(hwnd, _lparam):
        try:
            user32.GetWindowTextLengthW(hwnd)
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value

            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)
            cls = cls_buf.value

            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

            rect = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))

            cloaked = ctypes.c_int(0)
            dwmapi.DwmGetWindowAttribute(
                wt.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            )

            found.append(
                {
                    "hwnd": hwnd,
                    "title": title,
                    "class": cls,
                    "style": style,
                    "ex": ex,
                    "rect": (rect.left, rect.top, rect.right, rect.bottom),
                    "visible": bool(user32.IsWindowVisible(hwnd)),
                    "cloaked": bool(cloaked.value),
                }
            )
        except Exception:
            pass
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found


def main() -> int:
    _declare()
    print("=" * 72)
    print("1. LIVE CURSOR STATE")
    print("=" * 72)
    live_cursor_state()

    print()
    print("=" * 72)
    print("2. IS THE ACTIVE CURSOR IMAGE BLANK?")
    print("=" * 72)
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as key:
        for name in ("Arrow", "Help", "AppStarting", "Wait", "IBeam", "NWPen", "No"):
            try:
                path, _ = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                continue
            if not path:
                continue
            print(f"  {name:<12} {path}")
            print(f"  {'':<12} -> {cursor_is_blank(path)}")

    print()
    print("=" * 72)
    print("3. TOP-LEVEL WINDOWS: INVISIBLE / OVERLAY CANDIDATES")
    print("=" * 72)
    sw, sh = screen_size()
    print(f"  screen: {sw}x{sh}")
    print()

    wins = enumerate_windows()
    overlays = []
    for w in wins:
        ex = w["ex"]
        left, top, right, bottom = w["rect"]
        w_area = max(0, right - left) * max(0, bottom - top)
        screen_area = sw * sh
        big = w_area > screen_area * 0.10

        layered = bool(ex & WS_EX_LAYERED)
        transparent = bool(ex & WS_EX_TRANSPARENT)
        noactivate = bool(ex & WS_EX_NOACTIVATE)
        tool = bool(ex & WS_EX_TOOLWINDOW)
        topmost = bool(ex & WS_EX_TOPMOST)

        if w["visible"] and not w["cloaked"]:
            continue

        interesting = layered or transparent or big or topmost or noactivate
        if not interesting:
            continue

        overlays.append((w, layered, transparent, noactivate, tool, topmost, big))

    if not overlays:
        print("  (no invisible, layered, transparent or oversized top-level windows)")
    for w, layered, transparent, noactivate, tool, topmost, big in overlays:
        left, top, right, bottom = w["rect"]
        flags = []
        if layered:
            flags.append("LAYERED")
        if transparent:
            flags.append("TRANSPARENT")
        if noactivate:
            flags.append("NOACTIVATE")
        if tool:
            flags.append("TOOLWINDOW")
        if topmost:
            flags.append("TOPMOST")
        if big:
            flags.append("LARGE")
        print(
            f"  hwnd=0x{w['hwnd']:X} visible={w['visible']} cloaked={w['cloaked']} "
            f"rect=({left},{top})-({right},{bottom})"
        )
        print(f"      class='{w['class']}' title='{w['title'][:60]}' {' '.join(flags)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

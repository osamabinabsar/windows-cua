"""Sixth pass: identify the LIVE cursor, and per-display cursor plane.

The key question for hypothesis "an app set a custom (blank) cursor":
GetCursorInfo reports CURSOR_SHOWING, so the OS thinks the pointer is
visible. If the cursor currently SET is a blank one, it is invisible
while still being "showing".

Also fixes the DISPLAYCONFIG_MODE_INFO union size so the per-display
hardware cursor plane can finally be read.

Read-only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

IDC_ARROW = 32512
IDC_IBEAM = 32513
IDC_WAIT = 32514
IDC_CROSS = 32515
IDC_UPARROW = 32516
IDC_SIZENWSE = 32642
IDC_SIZENESW = 32643
IDC_SIZEWE = 32644
IDC_SIZENS = 32645
IDC_SIZEALL = 32646
IDC_HAND = 32649
IDC_APPSTARTING = 32650
IDC_HELP = 32651
IDC_PIN = 32671
IDC_PERSON = 32673

STANDARD = {
    IDC_ARROW: "IDC_ARROW",
    IDC_IBEAM: "IDC_IBEAM",
    IDC_WAIT: "IDC_WAIT",
    IDC_CROSS: "IDC_CROSS",
    IDC_UPARROW: "IDC_UPARROW",
    IDC_SIZENWSE: "IDC_SIZENWSE",
    IDC_SIZENESW: "IDC_SIZENESW",
    IDC_SIZEWE: "IDC_SIZEWE",
    IDC_SIZENS: "IDC_SIZENS",
    IDC_SIZEALL: "IDC_SIZEALL",
    IDC_HAND: "IDC_HAND",
    IDC_APPSTARTING: "IDC_APPSTARTING",
    IDC_HELP: "IDC_HELP",
}


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hCursor", wt.HANDLE),
        ("ptScreenPos", POINT),
    ]


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


def mask_population(hcursor) -> tuple[int, int]:
    """Return (AND mask set pixels, XOR mask set pixels) for a cursor."""
    ii = ICONINFO()
    user32.GetIconInfo.argtypes = [wt.HANDLE, ctypes.POINTER(ICONINFO)]
    user32.GetIconInfo.restype = wt.BOOL
    if not user32.GetIconInfo(hcursor, ctypes.byref(ii)):
        return (-1, -1)

    bih = BITMAPINFOHEADER()
    bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bih.biWidth = 32
    bih.biHeight = -32
    bih.biPlanes = 1
    bih.biBitCount = 32

    user32.GetDC.argtypes = [wt.HWND]
    user32.GetDC.restype = wt.HDC
    gdi32.GetDIBits.argtypes = [
        wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
        ctypes.POINTER(BITMAPINFOHEADER), wt.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int

    counts = []
    for hbm in (ii.hbmMask, ii.hbmColor):
        if not hbm:
            counts.append(0)
            continue
        buf = ctypes.create_string_buffer(32 * 32 * 4)
        if not gdi32.GetDIBits(
            user32.GetDC(None), hbm, 0, 32, buf, ctypes.byref(bih), 0
        ):
            counts.append(0)
            continue
        data = buf.raw
        counts.append(
            sum(1 for i in range(0, len(data), 4) if data[i : i + 4] != b"\x00\x00\x00\x00")
        )
    return counts[0], counts[1]


def live_cursor() -> None:
    print("=" * 72)
    print("A. IDENTITY AND VISIBILITY OF THE LIVE CURSOR")
    print("=" * 72)
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetCursorInfo.restype = wt.BOOL
    user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
    user32.LoadCursorW.restype = wt.HANDLE
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wt.BOOL

    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(info)):
        print(f"  GetCursorInfo failed: {ctypes.get_last_error()}")
        return
    print(f"  flags     : 0x{info.flags:08X}  SHOWING={bool(info.flags & 1)} SUPPRESSED={bool(info.flags & 2)}")
    print(f"  hCursor   : 0x{info.hCursor:X}")
    print(f"  position  : ({info.ptScreenPos.x}, {info.ptScreenPos.y})")

    matched = None
    for idc, name in STANDARD.items():
        h = user32.LoadCursorW(None, idc)
        if h and h == info.hCursor:
            matched = name
            break
    if matched:
        print(f"  identity  : {matched} (a standard system cursor)")
    else:
        print("  identity  : NOT a standard system cursor -> an app has set a custom one")

    and_px, xor_px = mask_population(info.hCursor)
    print(f"  mask pixels: AND={and_px} XOR={xor_px}")
    if and_px == 0 and xor_px == 0:
        print("  => THE LIVE CURSOR IMAGE IS COMPLETELY BLANK.")
        print("     Windows reports it as SHOWING, but it draws nothing.")
        print("     THIS IS THE INVISIBLE POINTER.")
    else:
        print("  => the live cursor image does draw something")

    print()
    print("  Standard cursor images for comparison:")
    for idc, name in list(STANDARD.items())[:6]:
        h = user32.LoadCursorW(None, idc)
        a, x = mask_population(h)
        print(f"    {name:<16} AND={a:<6} XOR={x}")


# -- display config, with the union sized correctly --------------------------

class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("size", wt.DWORD), ("Luid", LUID), ("id", wt.DWORD)]


class DISPLAYCONFIG_TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("flags", wt.DWORD),
        ("outputTechnology", wt.DWORD),
        ("edidManufactureId", wt.DWORD),
        ("edidProductCodeId", wt.DWORD),
        ("connectorInstance", wt.DWORD),
        ("wcharMonitorFriendlyDeviceName", wt.WCHAR * 64),
        ("wcharMonitorDevicePath", wt.WCHAR * 128),
    ]


class DISPLAYCONFIG_RATIONAL(ctypes.Structure):
    # DISPLAYCONFIG_RATIONAL is {UINT64 Numerator; UINT64 Denominator} --
    # 16 bytes, not 8. Sizing it with DWORDs makes the whole
    # DISPLAYCONFIG_MODE_INFO too small, and QueryDisplayConfig rejects
    # the array with ERROR_INVALID_PARAMETER (87).
    _fields_ = [("Numerator", ctypes.c_uint64), ("Denominator", ctypes.c_uint64)]


class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", wt.DWORD), ("modeInfoIdx", wt.DWORD), ("statusFlags", wt.DWORD)]


class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID), ("id", wt.DWORD), ("modeInfoIdx", wt.DWORD),
        ("outputTechnology", wt.DWORD), ("rotation", wt.DWORD), ("scaling", wt.DWORD),
        ("refreshRate", DISPLAYCONFIG_RATIONAL), ("scanLineOrdering", wt.DWORD),
        ("targetAvailable", wt.BOOL), ("statusFlags", wt.DWORD),
    ]


class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO), ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO), ("flags", wt.DWORD)]


class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
    # DISPLAYCONFIG_MODE_INFO is {DWORD infoType; DWORD id; LUID
    # adapterId; union}. The union's largest member is
    # DISPLAYCONFIG_TARGET_MODE_INFO, whose DISPLAYCONFIG_VIDEO_SIGNAL_INFO
    # holds a UINT64 pixelRate plus four 16-byte RATIONALs plus two UINTs
    # = 80 bytes. So the union is 80 bytes and the struct is 96.
    # The API validates the element size, so a wrong union size is
    # rejected with ERROR_INVALID_PARAMETER (87).
    _fields_ = [("infoType", wt.DWORD), ("id", wt.DWORD), ("adapterId", LUID), ("modeInfo", wt.DWORD * 20)]


class DXGK_CURSORPLANE_INFO(ctypes.Structure):
    _fields_ = [("Version", wt.DWORD), ("CursorPlaneIndex", wt.DWORD), ("CursorPlaneBitmask", wt.DWORD)]


def display_planes() -> None:
    print()
    print("=" * 72)
    print("B. PER-DISPLAY HARDWARE CURSOR PLANE")
    print("=" * 72)
    # The real signature is:
    #   LONG QueryDisplayConfig(UINT32 Flags,
    #                          DISPLAYCONFIG_PATH_INFO *pPathInfoArray,
    #                          DISPLAYCONFIG_MODE_INFO *pModeInfoArray,
    #                          UINT32 *pPathInfoArraySize,
    #                          UINT32 *pModeInfoArraySize);
    # The two size pointers come LAST. Passing them second is what made
    # the call fail with ERROR_INVALID_PARAMETER (87).
    user32.QueryDisplayConfig.argtypes = [
        wt.DWORD,
        ctypes.POINTER(DISPLAYCONFIG_PATH_INFO),
        ctypes.POINTER(DISPLAYCONFIG_MODE_INFO),
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
    ]
    user32.QueryDisplayConfig.restype = wt.LONG
    user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER)]
    user32.DisplayConfigGetDeviceInfo.restype = wt.LONG

    n_paths = ctypes.c_uint(0)
    n_modes = ctypes.c_uint(0)
    ret = user32.QueryDisplayConfig(
        0x00000002, None, None, ctypes.byref(n_paths), ctypes.byref(n_modes)
    )
    print(f"  QueryDisplayConfig -> {ret}, paths={n_paths.value}, modes={n_modes.value}")
    if ret != 0 or not n_paths.value:
        return

    paths = (DISPLAYCONFIG_PATH_INFO * n_paths.value)()
    modes = (DISPLAYCONFIG_MODE_INFO * max(n_modes.value, 1))()
    ret2 = user32.QueryDisplayConfig(
        0x00000002, ctypes.byref(paths), ctypes.byref(modes),
        ctypes.byref(n_paths), ctypes.byref(n_modes),
    )
    print(f"  fetch -> {ret2}")
    if ret2 != 0:
        return

    try:
        dwgdi = ctypes.WinDLL("dwgdi32", use_last_error=True)
        dwgdi.DxgkGetCursorPlaneInfo.argtypes = [LUID, wt.DWORD, ctypes.POINTER(DXGK_CURSORPLANE_INFO)]
        dwgdi.DxgkGetCursorPlaneInfo.restype = wt.LONG
    except OSError:
        print("  dwgdi32 unavailable")
        return

    for i in range(n_paths.value):
        tgt = paths[i].targetInfo
        name = DISPLAYCONFIG_TARGET_DEVICE_NAME()
        name.header.type = 2
        name.header.size = ctypes.sizeof(DISPLAYCONFIG_TARGET_DEVICE_NAME)
        name.header.Luid = tgt.adapterId
        name.header.id = tgt.id
        got = user32.DisplayConfigGetDeviceInfo(ctypes.byref(name.header))
        monitor = name.wcharMonitorFriendlyDeviceName if got == 0 else "<unknown>"

        print()
        print(f"  PATH {i} monitor='{monitor}' attached={bool(tgt.targetAvailable)}")
        info = DXGK_CURSORPLANE_INFO()
        r = dwgdi.DxgkGetCursorPlaneInfo(tgt.adapterId, tgt.id, ctypes.byref(info))
        print(f"    DxgkGetCursorPlaneInfo -> {r}")
        if r == 0:
            print(f"      CursorPlaneIndex   : {info.CursorPlaneIndex}")
            print(f"      CursorPlaneBitmask : 0x{info.CursorPlaneBitmask:08X}")
            print(
                "      => NO hardware cursor plane on this display: the pointer is"
                if info.CursorPlaneBitmask == 0
                else "      => hardware cursor plane present on this display"
            )


def main() -> int:
    live_cursor()
    display_planes()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

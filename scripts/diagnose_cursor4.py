"""Fourth pass: per-display hardware cursor plane support.

The pointer is drawn on a separate "cursor plane" by the display driver.
Whether a given display can show that plane is a per-display property,
and it is the one mechanism that can make the pointer visible over some
surfaces and invisible over others on a multi-display desktop.

QueryDisplayConfig -> path -> adapter -> DxgkGetCursorPlaneInfo reports
exactly that per display.

Read-only.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes as wt

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
ctypes.WinDLL("ntdll")

# -- display config constants ------------------------------------------------

QDC_ONLY_ACTIVE_PATHS = 0x00000002
QDC_VIRTUAL_MODE_AWARE = 0x00000010

DISPLAYCONFIG_PATH_INFO_TYPE = 2

TARGET_DEVICE_NAME = 2
SOURCE_DEVICE_NAME = 1
DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2
DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1

# DxgkCursorPlaneInfo
DXGK_CURSORPLANE_TYPE_SURFACE = 0
DXGK_CURSORPLANE_TYPE_HW = 1


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", wt.DWORD),
        ("size", wt.DWORD),
        ("Luid", LUID),
        ("id", wt.DWORD),
    ]


class DISPLAYCONFIG_SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("viewGdiDeviceName", wt.WCHAR * 32),
        ("outputTechnology", wt.DWORD),
    ]


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


class DISPLAYCONFIG_VIDEO_OUTPUT_TECHNOLOGY_DISPLAYCONSTANT(ctypes.Structure):
    _fields_ = [("header", DISPLAYCONFIG_DEVICE_INFO_HEADER), ("d", wt.DWORD)]


class DISPLAYCONFIG_RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wt.DWORD), ("Denominator", wt.DWORD)]


class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", wt.DWORD),
        ("modeInfoIdx", wt.DWORD),
        ("statusFlags", wt.DWORD),
    ]


class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", wt.DWORD),
        ("modeInfoIdx", wt.DWORD),
        ("outputTechnology", wt.DWORD),
        ("rotation", wt.DWORD),
        ("scaling", wt.DWORD),
        ("refreshRate", DISPLAYCONFIG_RATIONAL),
        ("scanLineOrdering", wt.DWORD),
        ("targetAvailable", wt.BOOL),
        ("statusFlags", wt.DWORD),
    ]


class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO),
        ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO),
        ("flags", wt.DWORD),
    ]


class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
    _fields_ = [
        ("infoType", wt.DWORD),
        ("id", wt.DWORD),
        ("adapterId", LUID),
        ("targetInfo", wt.DWORD * 48),
    ]


class DISPLAYCONFIG_DEVICE_INFO_LIST(ctypes.Structure):
    _fields_ = [("numDisplayDevices", wt.DWORD)]


# -- DxgkGetCursorPlaneInfo --------------------------------------------------

class DXGK_CURSORPLANE_INFO(ctypes.Structure):
    _fields_ = [
        ("Version", wt.DWORD),
        ("CursorPlaneIndex", wt.DWORD),
        ("CursorPlaneBitmask", wt.DWORD),
    ]


def main() -> int:
    print("=" * 72)
    print("A. ENUMERATE DISPLAY PATHS")
    print("=" * 72)

    user32.QueryDisplayConfig.argtypes = [
        wt.DWORD,
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(DISPLAYCONFIG_PATH_INFO),
        ctypes.POINTER(DISPLAYCONFIG_MODE_INFO),
    ]
    user32.QueryDisplayConfig.restype = wt.LONG
    user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER)]
    user32.DisplayConfigGetDeviceInfo.restype = wt.LONG

    flags = QDC_ONLY_ACTIVE_PATHS | QDC_VIRTUAL_MODE_AWARE
    path_count = ctypes.c_uint(0)
    mode_count = ctypes.c_uint(0)

    ret = user32.QueryDisplayConfig(
        flags, ctypes.byref(path_count), ctypes.byref(mode_count), None, None
    )
    print(f"  QueryDisplayConfig (size query) -> {ret}, {path_count.value} paths")
    if ret != 0:
        print("  (cannot enumerate; falling back to GDI metrics)")

    if ret == 0 and path_count.value:
        paths = (DISPLAYCONFIG_PATH_INFO * path_count.value)()
        modes = (DISPLAYCONFIG_MODE_INFO * max(mode_count.value, 1))()
        ret2 = user32.QueryDisplayConfig(
            flags, ctypes.byref(path_count), ctypes.byref(mode_count),
            ctypes.byref(paths), ctypes.byref(modes),
        )
        print(f"  QueryDisplayConfig (fetch) -> {ret2}")
        for i in range(path_count.value):
            path = paths[i]
            src_luid = path.sourceInfo.adapterId
            tgt_luid = path.targetInfo.adapterId

            tgt_name = DISPLAYCONFIG_TARGET_DEVICE_NAME()
            tgt_name.header.type = TARGET_DEVICE_NAME
            tgt_name.header.size = ctypes.sizeof(DISPLAYCONFIG_TARGET_DEVICE_NAME)
            tgt_name.header.Luid = tgt_luid
            tgt_name.header.id = path.targetInfo.id
            got = gdi32.DisplayConfigGetDeviceInfo(
                ctypes.byref(tgt_name.header)
            )

            monitor = tgt_name.wcharMonitorFriendlyDeviceName if got == 0 else "<unknown>"

            print()
            print(f"  PATH {i}")
            print(f"    source LUID: {src_luid.LowPart:08X}:{src_luid.HighPart:08X}")
            print(f"    target LUID: {tgt_luid.LowPart:08X}:{tgt_luid.HighPart:08X}")
            print(f"    monitor    : {monitor}")
            print(f"    technology : {path.targetInfo.outputTechnology}")
            print(f"    available  : {bool(path.targetInfo.targetAvailable)}")
            print(f"    rotation   : {path.targetInfo.rotation}")

            # DxgkGetCursorPlaneInfo for this target.
            handle = wt.HANDLE(0)
            try:
                dwgdi = ctypes.WinDLL("dwgdi32", use_last_error=True)
                dwgdi.DxgkGetCursorPlaneInfo.argtypes = [
                    LUID, wt.DWORD, ctypes.POINTER(DXGK_CURSORPLANE_INFO),
                ]
                dwgdi.DxgkGetCursorPlaneInfo.restype = wt.LONG
                info = DXGK_CURSORPLANE_INFO()
                r = dwgdi.DxgkGetCursorPlaneInfo(tgt_luid, path.targetInfo.id, ctypes.byref(info))
                print(f"    DxgkGetCursorPlaneInfo -> {r} (0 = OK)")
                if r == 0:
                    print(f"      cursor plane index : {info.CursorPlaneIndex}")
                    print(f"      cursor plane bitmask: 0x{info.CursorPlaneBitmask:08X}")
                    if info.CursorPlaneBitmask == 0:
                        print("      => NO HARDWARE CURSOR PLANE on this display:")
                        print("         the pointer is composited into the framebuffer")
                        print("         (software cursor) on THIS display only")
                    else:
                        print("      => hardware cursor plane present on this display")
            except OSError as exc:
                print(f"    DxgkGetCursorPlaneInfo unavailable: {exc}")

    print()
    print("=" * 72)
    print("B. GDI DISPLAY METRICS (per-display enumeration)")
    print("=" * 72)
    class DISPLAY_DEVICE(ctypes.Structure):
        _fields_ = [
            ("cb", wt.DWORD),
            ("DeviceName", wt.WCHAR * 32),
            ("DeviceString", wt.WCHAR * 128),
            ("StateFlags", wt.DWORD),
            ("DeviceID", wt.WCHAR * 128),
            ("DeviceKey", wt.WCHAR * 128),
        ]

    user32.EnumDisplayDevicesW.argtypes = [
        wt.LPCWSTR, wt.DWORD, ctypes.POINTER(DISPLAY_DEVICE), wt.DWORD,
    ]
    user32.EnumDisplayDevicesW.restype = wt.BOOL

    dev = DISPLAY_DEVICE()
    dev.cb = ctypes.sizeof(DISPLAY_DEVICE)
    i = 0
    while user32.EnumDisplayDevicesW(None, i, ctypes.byref(dev), 0):
        print(f"  display {i}: '{dev.DeviceName}' '{dev.DeviceString}' flags=0x{dev.StateFlags:X}")
        print(f"    id: {dev.DeviceID}")
        i += 1
        dev.cb = ctypes.sizeof(DISPLAY_DEVICE)

    print()
    print(f"  virtual screen: {user32.GetSystemMetrics(76)}x{user32.GetSystemMetrics(77)}"
          f"  origin=({user32.GetSystemMetrics(76 - 4)},{user32.GetSystemMetrics(77 - 4)})")
    print(f"  primary screen: {user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

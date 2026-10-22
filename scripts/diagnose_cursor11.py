"""Eleventh pass: HDR state and the per-display hardware cursor plane.

Both were blocked earlier by a mis-sized DISPLAYCONFIG_MODE_INFO.
The correction:

  DISPLAYCONFIG_RATIONAL is {UINT64 Numerator; UINT64 Denominator} --
  16 bytes, NOT 8. With DWORD members the union came out too small
  and QueryDisplayConfig rejected the array with ERROR_INVALID_PARAMETER
  (87), which is why every per-display query came back empty.

  The union's largest member is DISPLAYCONFIG_TARGET_MODE_INFO, whose
  DISPLAYCONFIG_VIDEO_SIGNAL_INFO is {UINT64 pixelRate; 4 x RATIONAL
  (16 bytes each); UINT videoStandard; UINT scanLineOrdering} = 80 bytes.
  So: union = 80 bytes (DWORD x 20), struct = 96 bytes.

Why HDR matters here: a widely-reported Windows 11 24H2+ class of bug
is "the mouse cursor is invisible over hardware-accelerated regions",
and the top-voted Microsoft Q&A fix for it is "turn HDR off". HDR moves
the display onto a different (10/12-bit) output path, and the pointer
compositing path changes with it.

Read-only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)

QDC_ONLY_ACTIVE_PATHS = 0x00000002
GET_ADVANCED_COLOR_INFO = 9
GET_ADVANCED_COLOR_INFO_2 = 10
GET_TARGET_NAME = 2


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", ctypes.c_uint64), ("Denominator", ctypes.c_uint64)]


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


class DISPLAYCONFIG_ADVANCED_COLOR_INFO(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("value", wt.DWORD),
        ("ColorEncoding", wt.DWORD),
        ("BitsPerColorChannel", wt.DWORD),
    ]


class DISPLAYCONFIG_ADVANCED_COLOR_INFO_2(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("value", wt.DWORD),
        ("ColorEncoding", wt.DWORD),
        ("BitsPerColorChannel", wt.DWORD),
        ("WindowsAdvancedColorEnabled", wt.DWORD),
    ]


class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", wt.DWORD), ("modeInfoIdx", wt.DWORD), ("statusFlags", wt.DWORD)]


class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID), ("id", wt.DWORD), ("modeInfoIdx", wt.DWORD),
        ("outputTechnology", wt.DWORD), ("rotation", wt.DWORD), ("scaling", wt.DWORD),
        ("refreshRate", RATIONAL), ("scanLineOrdering", wt.DWORD),
        ("targetAvailable", wt.BOOL), ("statusFlags", wt.DWORD),
    ]


class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO),
        ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO),
        ("flags", wt.DWORD),
    ]


class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
    _fields_ = [("infoType", wt.DWORD), ("id", wt.DWORD), ("adapterId", LUID), ("modeInfo", wt.DWORD * 20)]


class DXGK_CURSORPLANE_INFO(ctypes.Structure):
    _fields_ = [("Version", wt.DWORD), ("CursorPlaneIndex", wt.DWORD), ("CursorPlaneBitmask", wt.DWORD)]


def main() -> int:
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
    # QueryDisplayConfig rejects NULL arrays with ERROR_INVALID_PARAMETER
    # (87) -- the counts must come from GetDisplayConfigBufferSizes, and
    # QDC_ALL_PATHS (0) is rejected on this system too, so use
    # QDC_ONLY_ACTIVE_PATHS.
    user32.GetDisplayConfigBufferSizes.argtypes = [
        wt.DWORD, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)
    ]
    user32.GetDisplayConfigBufferSizes.restype = wt.LONG
    ret = user32.GetDisplayConfigBufferSizes(
        QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), ctypes.byref(n_modes)
    )
    print(
        f"GetDisplayConfigBufferSizes -> {ret} (0 = OK), "
        f"paths={n_paths.value}, modes={n_modes.value}"
    )
    if ret != 0 or not n_paths.value:
        print("  still failing")
        return 1

    paths = (DISPLAYCONFIG_PATH_INFO * n_paths.value)()
    modes = (DISPLAYCONFIG_MODE_INFO * max(n_modes.value, 1))()
    ret2 = user32.QueryDisplayConfig(
        QDC_ONLY_ACTIVE_PATHS, paths, modes,
        ctypes.byref(n_paths), ctypes.byref(n_modes),
    )
    print(f"fetch -> {ret2}, paths={n_paths.value}, modes={n_modes.value}")
    if ret2 != 0:
        return 1

    try:
        dwgdi = ctypes.WinDLL("dwgdi32", use_last_error=True)
        dwgdi.DxgkGetCursorPlaneInfo.argtypes = [LUID, wt.DWORD, ctypes.POINTER(DXGK_CURSORPLANE_INFO)]
        dwgdi.DxgkGetCursorPlaneInfo.restype = wt.LONG
    except OSError:
        dwgdi = None

    for i in range(n_paths.value):
        tgt = paths[i].targetInfo
        print()
        print(f"--- PATH {i} ---")

        name = DISPLAYCONFIG_TARGET_DEVICE_NAME()
        name.header.type = GET_TARGET_NAME
        name.header.size = ctypes.sizeof(DISPLAYCONFIG_TARGET_DEVICE_NAME)
        name.header.Luid = tgt.adapterId
        name.header.id = tgt.id
        got = user32.DisplayConfigGetDeviceInfo(ctypes.byref(name.header))
        monitor = name.wcharMonitorFriendlyDeviceName if got == 0 else "<unknown>"
        print(f"  monitor           : '{monitor}'  (targetId={tgt.id})")
        print(f"  target available  : {bool(tgt.targetAvailable)}")
        print(f"  output technology : {tgt.outputTechnology}  (rotation={tgt.rotation})")
        print(f"  refresh rate      : {tgt.refreshRate.Numerator}/{tgt.refreshRate.Denominator}")

        # -- HDR / advanced color ------------------------------------------
        for hdr_type, label in (
            (GET_ADVANCED_COLOR_INFO, "ADVANCED_COLOR_INFO"),
            (GET_ADVANCED_COLOR_INFO_2, "ADVANCED_COLOR_INFO_2"),
        ):
            if hdr_type == GET_ADVANCED_COLOR_INFO:
                info = DISPLAYCONFIG_ADVANCED_COLOR_INFO()
            else:
                info = DISPLAYCONFIG_ADVANCED_COLOR_INFO_2()
            info.header.type = hdr_type
            info.header.size = ctypes.sizeof(type(info))
            info.header.Luid = tgt.adapterId
            info.header.id = tgt.id
            r = user32.DisplayConfigGetDeviceInfo(ctypes.byref(info.header))
            if r != 0:
                print(f"  {label:<22}: not available (ret={r})")
                continue
            v = info.value
            supported = bool(v & 0x1)
            enabled = bool(v & 0x2)
            wide_enforced = bool(v & 0x4)
            force_disabled = bool(v & 0x8)
            print(f"  {label:<22}: ret=0")
            print(f"      AdvancedColorSupported   : {supported}")
            print(f"      AdvancedColorEnabled      : {enabled}")
            print(f"      WideColorEnforced         : {wide_enforced}")
            print(f"      AdvancedColorForceDisabled: {force_disabled}")
            if hdr_type == GET_ADVANCED_COLOR_INFO_2:
                print(f"      WindowsAdvancedColorEnabled: {bool(info.WindowsAdvancedColorEnabled)}")
            print(f"      BitsPerColorChannel        : {info.BitsPerColorChannel}")
            if info.BitsPerColorChannel > 8:
                print("      *** HDR IS ACTIVE ON THIS DISPLAY ***")

        # -- hardware cursor plane ----------------------------------------
        if dwgdi is not None:
            cp = DXGK_CURSORPLANE_INFO()
            r = dwgdi.DxgkGetCursorPlaneInfo(tgt.adapterId, tgt.id, ctypes.byref(cp))
            print(f"  DxgkGetCursorPlaneInfo -> {r}")
            if r == 0:
                print(f"      CursorPlaneIndex   : {cp.CursorPlaneIndex}")
                print(f"      CursorPlaneBitmask : 0x{cp.CursorPlaneBitmask:08X}")
                if cp.CursorPlaneBitmask == 0:
                    print("      => NO hardware cursor plane on this display")
                else:
                    print("      => hardware cursor plane PRESENT on this display")
        else:
            print("  dwgdi32 unavailable")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

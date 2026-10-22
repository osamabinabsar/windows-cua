"""Fifth pass: per-display cursor plane, and Chromium GPU feature flags.

Two decisive checks:

1. DxgkGetCursorPlaneInfo for each active display target. If
   CursorPlaneBitmask is 0 on the display you are looking at, that
   display composites the pointer into the framebuffer (software
   cursor), and anything that repaints the region under the pointer
   erases it.

2. Chromium's --gpu-preferences blob, base64-encoded on the command
   line, which lists the enabled GPU features. Flip-model rendering
   (DirectComposition flip) is what makes a window eligible for a
   hardware overlay plane, and a hardware overlay plane is what can be
   composited above the cursor plane.

Read-only.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import json
import subprocess

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

QDC_ONLY_ACTIVE_PATHS = 0x00000002
QDC_VIRTUAL_MODE_AWARE = 0x00000010


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", wt.DWORD),
        ("size", wt.DWORD),
        ("Luid", LUID),
        ("id", wt.DWORD),
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
        ("targetInfo", wt.DWORD * 96),
    ]


class DXGK_CURSORPLANE_INFO(ctypes.Structure):
    _fields_ = [
        ("Version", wt.DWORD),
        ("CursorPlaneIndex", wt.DWORD),
        ("CursorPlaneBitmask", wt.DWORD),
    ]


def display_paths():
    user32.QueryDisplayConfig.argtypes = [
        wt.DWORD,
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(DISPLAYCONFIG_PATH_INFO),
        ctypes.POINTER(DISPLAYCONFIG_MODE_INFO),
    ]
    user32.QueryDisplayConfig.restype = wt.LONG
    user32.DisplayConfigGetDeviceInfo.argtypes = [
        ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER)
    ]
    user32.DisplayConfigGetDeviceInfo.restype = wt.LONG

    for flags in (QDC_ONLY_ACTIVE_PATHS, QDC_ONLY_ACTIVE_PATHS | QDC_VIRTUAL_MODE_AWARE):
        n_paths = ctypes.c_uint(0)
        n_modes = ctypes.c_uint(0)
        ret = user32.QueryDisplayConfig(
            flags, ctypes.byref(n_paths), ctypes.byref(n_modes), None, None
        )
        print(f"  QueryDisplayConfig(flags=0x{flags:X}) -> {ret}, {n_paths.value} paths")
        if ret != 0 or not n_paths.value:
            continue

        paths = (DISPLAYCONFIG_PATH_INFO * n_paths.value)()
        modes = (DISPLAYCONFIG_MODE_INFO * max(n_modes.value, 1))()
        ret2 = user32.QueryDisplayConfig(
            flags,
            ctypes.byref(n_paths),
            ctypes.byref(n_modes),
            ctypes.byref(paths),
            ctypes.byref(modes),
        )
        if ret2 != 0:
            print(f"    fetch failed: {ret2}")
            continue
        return paths, n_paths.value
    return None, 0


def main() -> int:
    print("=" * 72)
    print("A. PER-DISPLAY HARDWARE CURSOR PLANE")
    print("=" * 72)
    paths, count = display_paths()
    if not paths:
        print("  (could not enumerate display paths)")
    else:
        try:
            dwgdi = ctypes.WinDLL("dwgdi32", use_last_error=True)
            dwgdi.DxgkGetCursorPlaneInfo.argtypes = [
                LUID, wt.DWORD, ctypes.POINTER(DXGK_CURSORPLANE_INFO)
            ]
            dwgdi.DxgkGetCursorPlaneInfo.restype = wt.LONG
        except OSError:
            dwgdi = None
            print("  dwgdi32.DxgkGetCursorPlaneInfo unavailable")

        for i in range(count):
            path = paths[i]
            tgt = path.targetInfo

            name = DISPLAYCONFIG_TARGET_DEVICE_NAME()
            name.header.type = 2  # TARGET_DEVICE_NAME
            name.header.size = ctypes.sizeof(DISPLAYCONFIG_TARGET_DEVICE_NAME)
            name.header.Luid = tgt.adapterId
            name.header.id = tgt.id
            got = user32.DisplayConfigGetDeviceInfo(ctypes.byref(name.header))
            monitor = name.wcharMonitorFriendlyDeviceName if got == 0 else "<unknown>"

            print()
            print(f"  PATH {i}: monitor='{monitor}' attached={bool(tgt.targetAvailable)}")
            print(f"    target LUID {tgt.adapterId.LowPart:08X}:{tgt.adapterId.HighPart:08X} id={tgt.id}")

            if dwgdi is None:
                continue
            info = DXGK_CURSORPLANE_INFO()
            r = dwgdi.DxgkGetCursorPlaneInfo(tgt.adapterId, tgt.id, ctypes.byref(info))
            print(f"    DxgkGetCursorPlaneInfo -> {r}")
            if r == 0:
                print(f"      CursorPlaneIndex   : {info.CursorPlaneIndex}")
                print(f"      CursorPlaneBitmask : 0x{info.CursorPlaneBitmask:08X}")
                if info.CursorPlaneBitmask == 0:
                    print("      => NO hardware cursor plane on this display.")
                    print("         The pointer is drawn INTO the framebuffer here.")
                    print("         Anything that repaints under the pointer erases it.")
                else:
                    print("      => hardware cursor plane present (pointer is its own layer)")

    print()
    print("=" * 72)
    print("B. CHROMIUM GPU FLAGS (--gpu-preferences, base64)")
    print("=" * 72)
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match "
        "'^(Code|chrome|msedge)\\.exe$' -and $_.CommandLine -like '*gpu-preferences*' } | "
        "ForEach-Object { "
        "  $m = [regex]::Match($_.CommandLine, '--gpu-preferences=([^\\s\\\"]+)');"
        "  if ($m.Success) {"
        "    $raw = $m.Groups[1].Value;"
        "    $pad = $raw + ('=' * ((4 - ($raw.Length % 4)) % 4));"
        "    $decoded = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($pad));"
        "    [PSCustomObject]@{ Pid=$_.ProcessId; Name=$_.Name; Decoded=$decoded } } } | "
        "ConvertTo-Json -Depth 3"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=60,
    )
    raw = out.stdout.strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = []
        if isinstance(data, dict):
            data = [data]
        for row in data:
            decoded = row.get("Decoded", "")
            print(f"  pid={row.get('Pid')} {row.get('Name')}")
            for chunk in decoded.split(";"):
                chunk = chunk.strip()
                if not chunk:
                    continue
                if chunk.startswith("disabled"):
                    print(f"      {chunk}")
                else:
                    key, _, val = chunk.partition(",")
                    print(f"      {key} = {val}")
    else:
        print("  (no gpu-preferences found)")
    if out.stderr.strip():
        print("  stderr:", out.stderr.strip()[:300])

    print()
    print("=" * 72)
    print("C. DWM COMPOSITION + WINDOW FLAGS OF THE AFFECTED SURFACES")
    print("=" * 72)

    DWMWA_CLOAKED = 14

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long), ("top", ctypes.c_long),
            ("right", ctypes.c_long), ("bottom", ctypes.c_long),
        ]

    EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]

    interesting = []

    def callback(hwnd, _l):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        text = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, text, 256)
        name = cls.value
        if any(
            k in name
            for k in (
                "Xaml", "Chrome_WidgetWin", "Chrome_RenderWidgetHost",
                "ApplicationFrameWindow", "DirectUI", "SysShadow",
                "ForegroundStaging", "CodexComputerUse",
            )
        ):
            cloaked = ctypes.c_int(0)
            dwmapi.DwmGetWindowAttribute(
                wt.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            )
            r = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            interesting.append(
                {
                    "hwnd": hwnd,
                    "class": name,
                    "title": text.value[:40],
                    "visible": bool(user32.IsWindowVisible(hwnd)),
                    "cloaked": bool(cloaked.value),
                    "rect": (r.left, r.top, r.right, r.bottom),
                    "ex": user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF,
                }
            )
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    for w in interesting:
        if not w["visible"] or w["cloaked"]:
            continue
        print(
            f"  hwnd=0x{w['hwnd']:X} class='{w['class']}' "
            f"title='{w['title']}' rect={w['rect']} ex=0x{w['ex']:08X}"
        )
    print(f"  ({len(interesting)} matching windows total, visible ones listed above)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

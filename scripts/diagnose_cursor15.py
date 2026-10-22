"""Fifteenth pass: probe the graphics stack for a cursor-plane API.

The static census overturned the "XAML island hides the pointer" story:

    Explorer nav pane  (pointer INVISIBLE) : SysTreeView32 / CtrlNotifySink
                                            -> no WS_EX_NOREDIRECTIONBITMAP,
                                               drawn into the parent surface
    Explorer file list (pointer VISIBLE)   : FileExplorer_FolderViewIsland ->
                                            Microsoft.UI.Content.DesktopChildSiteBridge
                                            -> WS_EX_NOREDIRECTIONBITMAP,
                                               its own DirectComposition surface

So a promoted DirectComposition surface cannot be the discriminator --
the surface that owns its own DComp surface is the one where the pointer
WORKS.  What is left is the display path: if the pointer lives on the
adapter's hardware cursor plane, the only way it can be hidden is a
display plane scanned out ABOVE the cursor plane.

This script probes the graphics stack for the cursor-plane / overlay
entry points and, for each one that resolves, reports its address.

Read-only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.LoadLibraryW.argtypes = [wt.LPCWSTR]
kernel32.LoadLibraryW.restype = wt.HMODULE
kernel32.GetProcAddress.argtypes = [wt.HMODULE, wt.LPCSTR]
kernel32.GetProcAddress.restype = wt.LPVOID
kernel32.FreeLibrary.argtypes = [wt.HMODULE]
kernel32.FreeLibrary.restype = wt.BOOL

CANDIDATES = [
    # dwmapi
    "DwmGetCursorPlaneInfo",
    "DwmSetCursorPlaneInfo",
    "DwmGetCursorPlane",
    "DwmGetDxSharedParameters",
    "DwmSetDxSharedParameters",
    "DwmGetCompositionAttributeInfo",
    "DwmSetCompositionAttributeInfo",
    "DwmGetSurfaceAttributes",
    "DwmGetDxSurfaceAllocation",
    "DwmRegisterThumbnail",
    "DwmGetWindowAttribute",
    # dwgdi32
    "DxgkGetCursorPlaneInfo",
    "DxgkDdiGetCursorPlaneInfo",
    "DxgkDdiQueryAdapterInfo",
    "DxgkDdiSetPointerPosition",
    "DxgkDdiSetPointerShape",
    "DxgkNotifyInterrupt",
    # user32
    "GetCursorInfo",
    "SetCursor",
    "ShowCursor",
    "SetSystemCursor",
    "GetSystemMetrics",
    "LoadCursorW",
    # gdi32
    "GetPixel",
    "SetCursorPos",
    "CreateCursor",
    "LoadImageW",
    # d3d / dxgi
    "D3D11CreateDevice",
    "CreateDXGIFactory1",
    "SetCursorPosition",
    "GetCursorPos",
]

DLLS = [
    "dwmapi.dll",
    "dwgdi32.dll",
    "gdi32.dll",
    "user32.dll",
    "dxgi.dll",
    "d3d11.dll",
    "ntdll.dll",
    "win32u.dll",
]

print("=" * 78)
print("CURSOR / PLANE / OVERLAY API PROBE")
print("=" * 78)

for dll in DLLS:
    h = kernel32.LoadLibraryW(dll)
    if not h:
        print(f"\n  {dll}: not loadable")
        continue
    try:
        found = []
        for name in CANDIDATES:
            addr = kernel32.GetProcAddress(h, name.encode("ascii"))
            if addr:
                found.append((name, addr))
        print(f"\n  {dll}: {len(found)} of {len(CANDIDATES)} candidates resolve")
        for name, addr in found:
            print(f"      {name:<38} @ 0x{addr:016X}")
    finally:
        kernel32.FreeLibrary(h)

# ---------------------------------------------------------------------
# If DwmGetCursorPlaneInfo exists, call it. The documented shape is
#   BOOL DwmGetCursorPlaneInfo(HMONITOR hMonitor, UINT* pCursorPlaneBitmask)
# but it has never been publicly documented, so try a few signatures
# and keep the ones that do not fault.
# ---------------------------------------------------------------------
print()
print("=" * 78)
print("CALLING THE CURSOR-PLANE API (if present)")
print("=" * 78)

dwm = kernel32.LoadLibraryW("dwmapi.dll")
if dwm:
    for name in ("DwmGetCursorPlaneInfo", "DwmGetCursorPlane"):
        addr = kernel32.GetProcAddress(dwm, name.encode("ascii"))
        print(f"\n  {name} @ 0x{addr:016X}" if addr else f"\n  {name}: absent")
        if not addr:
            continue
        fn = ctypes.WINFUNCTYPE(
            wt.BOOL, wt.HANDLE, ctypes.POINTER(wt.DWORD)
        )(addr)
        for mon in (None, wt.HANDLE(0), wt.HANDLE(-1 & 0xFFFFFFFFFFFFFFFF)):
            try:
                bm = wt.DWORD(0xDEADBEEF)
                rc = fn(mon, ctypes.byref(bm))
                print(f"    monitor={mon!r:>24} -> rc={rc} bitmask=0x{bm.value:08X}")
            except OSError as exc:
                print(f"    monitor={mon!r:>24} -> {exc}")
    kernel32.FreeLibrary(dwm)

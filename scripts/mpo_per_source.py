"""Read the MPO capability of each display OUTPUT, per VidPN source.

Why this exists
---------------
`dxdiag` reports "MPO MaxPlanes" and "MPO Caps" per output, but it is a
rendering of the driver's answer, not the answer itself.  The API-level
statement is `D3DKMTGetMultiPlaneOverlayCaps`, and the key point is that
it is keyed by `hAdapter` **plus `VidPnSourceId`** - i.e. MPO capability
is a property of an *output*, not of an adapter:

    D3DKMT_GET_MULTIPLANE_OVERLAY_CAPS
      https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmthk/ns-d3dkmthk-_d3dkmt_get_multiplane_overlay_caps

    typedef struct _D3DKMT_GET_MULTIPLANE_OVERLAY_CAPS {
      D3DKMT_HANDLE                  hAdapter;
      D3DDDI_VIDEO_PRESENT_SOURCE_ID VidPnSourceId;
      UINT                           MaxPlanes;
      UINT                           MaxRGBPlanes;
      UINT                           MaxYUVPlanes;
      D3DKMT_MULTIPLANE_OVERLAY_CAPS OverlayCaps;
      float                          MaxStretchFactor;
      float                          MaxShrinkFactor;
    } D3DKMT_GET_MULTIPLANE_OVERLAY_CAPS;

"MaxPlanes" counts the primary surface as one of the planes, so a value
of 1 means "the root surface only" - ZERO overlay planes.  That number
is the one that decides whether an overlay plane can exist at all on a
given output, and therefore whether an "overlay plane covers the cursor"
hypothesis is even available there.

This script is READ-ONLY.  It opens a display device context, asks the
kernel for capability numbers, and closes everything again.  It writes
nothing, changes no setting, and touches no window.

Usage:  python mpo_per_source.py
"""

from __future__ import annotations

import ctypes as C
from ctypes import wintypes
import sys

gdi32 = C.WinDLL("gdi32.dll")
user32 = C.WinDLL("user32.dll")
kernel32 = C.WinDLL("kernel32.dll")

HRESULT = C.c_long
DWORD = C.c_ulong
UINT = C.c_uint
HANDLE = C.c_void_p

# D3DKMT_MULTIPLANE_OVERLAY_CAPS is a flat block of UINTs whose layout
# differs between WDK versions; only the leading "Value" word is read
# here, because the bit flags the doc quotes (STRETCH_RGB, BILINEAR,
# HIGH_FILTER, ...) live in that word.  Anything else is printed raw.
CAPS_FLAGS = {
    0x00000001: "RGB",
    0x00000002: "YUV",
    0x00000004: "BILINEAR",
    0x00000008: "HIGH_FILTER",
    0x00000010: "STRETCH_YUV",
    0x00000020: "STRETCH_RGB",
    0x00000040: "IMMEDIATE",
    0x00000080: "HDR",
}


class D3DKMT_MULTIPLANE_OVERLAY_CAPS(C.Structure):
    _fields_ = [("Value", UINT)]


class D3DKMT_GET_MULTIPLANE_OVERLAY_CAPS(C.Structure):
    _fields_ = [
        ("hAdapter", DWORD),
        ("VidPnSourceId", UINT),
        ("MaxPlanes", UINT),
        ("MaxRGBPlanes", UINT),
        ("MaxYUVPlanes", UINT),
        ("OverlayCaps", D3DKMT_MULTIPLANE_OVERLAY_CAPS),
        ("MaxStretchFactor", C.c_float),
        ("MaxShrinkFactor", C.c_float),
    ]


class D3DKMT_OPENADAPTERFROMHDC(C.Structure):
    _fields_ = [("hDC", HANDLE), ("hAdapter", DWORD)]


gdi32.D3DKMTOpenAdapterFromHdc.argtypes = [C.POINTER(D3DKMT_OPENADAPTERFROMHDC)]
gdi32.D3DKMTOpenAdapterFromHdc.restype = HRESULT
gdi32.D3DKMTGetMultiPlaneOverlayCaps.argtypes = [
    C.POINTER(D3DKMT_GET_MULTIPLANE_OVERLAY_CAPS)
]
gdi32.D3DKMTGetMultiPlaneOverlayCaps.restype = HRESULT
gdi32.D3DKMTCloseAdapter.argtypes = [DWORD]
gdi32.D3DKMTCloseAdapter.restype = HRESULT
gdi32.CreateDCW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    C.c_void_p,
]
gdi32.CreateDCW.restype = HANDLE
user32.GetDC.restype = HANDLE
user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.CloseHandle.restype = C.c_int


def caps_for(hadapter: int, src: int):
    c = D3DKMT_GET_MULTIPLANE_OVERLAY_CAPS()
    c.hAdapter = hadapter
    c.VidPnSourceId = src
    hr = gdi32.D3DKMTGetMultiPlaneOverlayCaps(C.byref(c)) & 0xFFFFFFFF
    return hr, c


def describe(src: int, hr: int, c) -> None:
    if hr != 0:
        print(f"  src={src}: query failed, hr=0x{hr:08X}")
        return
    flags = c.OverlayCaps.Value
    named = ", ".join(n for b, n in CAPS_FLAGS.items() if flags & b) or "(none)"
    planes = c.MaxPlanes
    overlay_planes = max(0, planes - 1)  # the primary surface counts as one
    print(
        f"  src={src}: MaxPlanes={planes}  (=> {overlay_planes} overlay plane"
        f"{'s' if overlay_planes != 1 else ''})  "
        f"RGB={c.MaxRGBPlanes} YUV={c.MaxYUVPlanes}  "
        f"caps=0x{flags:08X} [{named}]  "
        f"stretch={c.MaxStretchFactor:.3f} shrink={c.MaxShrinkFactor:.3f}"
    )


def probe_device(dev: str) -> None:
    print(f"--- {dev} ---")
    hdc = gdi32.CreateDCW(dev, None, None, None)
    if not hdc:
        print("  could not open the display device")
        return
    try:
        oa = D3DKMT_OPENADAPTERFROMHDC()
        oa.hDC = hdc
        hr = gdi32.D3DKMTOpenAdapterFromHdc(C.byref(oa)) & 0xFFFFFFFF
        if hr != 0:
            print(f"  D3DKMTOpenAdapterFromHdc failed, hr=0x{hr:08X}")
            return
        try:
            for src in range(0, 4):
                h, c = caps_for(oa.hAdapter, src)
                describe(src, h, c)
        finally:
            gdi32.D3DKMTCloseAdapter(oa.hAdapter)
    finally:
        kernel32.CloseHandle(hdc)


def main() -> int:
    print("MPO capability per VidPN source (read-only capability query)")
    print("=" * 68)
    for dev in ("\\\\.\\DISPLAY1", "\\\\.\\DISPLAY2"):
        probe_device(dev)
    print()
    print("A source with MaxPlanes=1 has NO overlay plane at all, so no")
    print("overlay plane can be scanned out above the cursor on that output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Watcher: correlate WHERE the pointer is with WHAT cursor is set.

The gap this closes: every previous sample of GetCursorInfo was taken
while the pointer happened to be over a window that was NOT one of the
affected ones. So "the cursor is a normal IDC_ARROW" was only ever
proven for the good regions.

This samples, ~20x/second:
    pointer position
    the window class under the pointer
    the CURSORINFO flags  (SHOWING / SUPPRESSED)
    the live hCursor handle
    the AND/XOR mask population of that cursor image (is it blank?)

and prints a compact table of DISTINCT (window class, cursor identity,
mask populations, flags) combinations seen, with the positions where
each was seen.

Interpretation:
  * If over the affected region the cursor handle changes AND the
    image is blank (AND=0 and XOR=0), the pointer is being REPLACED by
    an invisible cursor -> a cursor-resource / app behaviour problem.
  * If over the affected region the cursor stays the same, non-blank
    IDC_ARROW with SHOWING, then Windows believes the pointer is
    visible and the app is not hiding it -> the pointer is being
    OCCLUDED at composition time (hardware cursor plane vs. overlay
    plane), i.e. the MPO / DirectComposition cause.

Read-only. Takes no input, changes nothing.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

IDC_ARROW = 32512
IDC_IBEAM = 32513
IDC_WAIT = 32514
IDC_CROSS = 32515
IDC_UPARROW = 32516
IDC_SIZEALL = 32646
IDC_HAND = 32649
IDC_APPSTARTING = 32650
IDC_HELP = 32651

STANDARD = {
    IDC_ARROW: "IDC_ARROW",
    IDC_IBEAM: "IDC_IBEAM",
    IDC_WAIT: "IDC_WAIT",
    IDC_CROSS: "IDC_CROSS",
    IDC_UPARROW: "IDC_UPARROW",
    32642: "IDC_SIZENWSE",
    32643: "IDC_SIZENESW",
    32644: "IDC_SIZEWE",
    32645: "IDC_SIZENS",
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


user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wt.HANDLE
user32.GetIconInfo.argtypes = [wt.HANDLE, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wt.BOOL
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
gdi32.GetDIBits.argtypes = [
    wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFOHEADER), wt.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int

# Standard cursor handles, resolved once.
STANDARD_HANDLES = {}
for _idc in STANDARD:
    _h = user32.LoadCursorW(None, _idc)
    if _h:
        STANDARD_HANDLES[_h] = STANDARD[_idc]

_hdc = user32.GetDC(None)
_mask_cache: dict[int, tuple[int, int]] = {}


def mask_population(hcursor) -> tuple[int, int]:
    """(AND mask set pixels, XOR mask set pixels) -- 0/0 means blank."""
    if not hcursor:
        return (0, 0)
    key = hcursor
    if key in _mask_cache:
        return _mask_cache[key]
    ii = ICONINFO()
    if not user32.GetIconInfo(hcursor, ctypes.byref(ii)):
        return (-1, -1)
    bih = BITMAPINFOHEADER()
    bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bih.biWidth = 32
    bih.biHeight = -32
    bih.biPlanes = 1
    bih.biBitCount = 32
    counts = []
    for hbm in (ii.hbmMask, ii.hbmColor):
        if not hbm:
            counts.append(0)
            continue
        buf = ctypes.create_string_buffer(32 * 32 * 4)
        if not gdi32.GetDIBits(_hdc, hbm, 0, 32, buf, ctypes.byref(bih), 0):
            counts.append(0)
            continue
        data = buf.raw
        counts.append(
            sum(1 for i in range(0, len(data), 4) if data[i : i + 4] != b"\x00\x00\x00\x00")
        )
    _mask_cache[key] = (counts[0], counts[1])
    return _mask_cache[key]


def class_of(h) -> str:
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def top_window_at(x, y):
    """The top-level (root) window containing the point."""
    h = user32.WindowFromPoint(POINT(x, y))
    seen = set()
    while h and h not in seen:
        seen.add(h)
        parent = user32.GetAncestor(h, 1)  # GA_PARENT
        if parent == h:
            break
        h = parent
    return h


def describe(x, y):
    pos = POINT()
    user32.GetCursorPos(ctypes.byref(pos))
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(info)):
        return None
    root = top_window_at(pos.x, pos.y)
    leaf = user32.WindowFromPoint(POINT(pos.x, pos.y))
    name = STANDARD_HANDLES.get(info.hCursor)
    if name is None:
        name = f"custom(0x{info.hCursor:X})"
    a, x_ = mask_population(info.hCursor)
    return {
        "pos": (pos.x, pos.y),
        "root_class": class_of(root) if root else "?",
        "root_title": "",
        "leaf_class": class_of(leaf) if leaf else "?",
        "cursor": name,
        "hCursor": info.hCursor,
        "flags": info.flags,
        "and_px": a,
        "xor_px": x_,
    }


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    print(f"Watching for {seconds}s. Move the pointer over the regions where")
    print("the arrow disappears (VS Code explorer, Explorer nav pane, search")
    print("list) and over a region where it shows.")
    print()
    seen: dict[tuple, dict] = {}
    end = time.time() + seconds
    n = 0
    while time.time() < end:
        d = describe(0, 0)
        n += 1
        if d:
            key = (d["root_class"], d["leaf_class"], d["hCursor"], d["flags"], d["and_px"], d["xor_px"])
            rec = seen.setdefault(
                key,
                {
                    **d,
                    "count": 0,
                    "positions": [],
                    "root_titles": set(),
                },
            )
            rec["count"] += 1
            if len(rec["positions"]) < 6:
                rec["positions"].append(d["pos"])
        time.sleep(0.05)

    print(f"{n} samples taken.")
    print()
    print("=" * 78)
    print("DISTINCT (window, cursor) COMBINATIONS OBSERVED")
    print("=" * 78)
    for key, rec in sorted(seen.items(), key=lambda kv: -kv[1]["count"]):
        blank = rec["and_px"] == 0 and rec["xor_px"] == 0
        showing = bool(rec["flags"] & 1)
        suppressed = bool(rec["flags"] & 2)
        print()
        print(f"  {rec['count']:>5} samples")
        print(f"     top-level class : {rec['root_class']}")
        print(f"     window at point : {rec['leaf_class']}")
        print(f"     cursor          : {rec['cursor']}  (handle 0x{rec['hCursor']:X})")
        print(
            f"     flags           : 0x{rec['flags']:08X} "
            f"SHOWING={int(showing)} SUPPRESSED={int(suppressed)}"
        )
        print(f"     image pixels    : AND={rec['and_px']} XOR={rec['xor_px']}"
              + ("   <-- BLANK CURSOR" if blank else ""))
        print(f"     sample positions: {rec['positions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

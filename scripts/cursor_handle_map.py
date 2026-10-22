"""Map shared cursor HANDLE values to system cursor names.

The cursors the OS draws are shared objects, so the same handle value
means the same cursor in every process - which is why the watcher could
record `0x10003` and have it be meaningful.  The shared cursor table is
allocated in the order of the system cursor table, so loading the
standard cursors with LoadCursor and printing their handle values
gives the mapping.

This also fixes a gap in cursor_shapes.py: LoadCursor only succeeds for
IDC_ARROW..IDC_UPARROW on this build, so the reference set is small -
but the handle mapping is enough to name the rest.

Usage:  python cursor_handle_map.py [0x10003 ...]
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cursor_shapes as cs  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
user32.LoadCursorW.restype = wt.HANDLE

# The real IDC_* values from WinUser.h.  Note the two groups: the first
# five are 32512..32516, and the rest start at 32640 - NOT 32517..32529.
# Using the obsolete contiguous numbering makes LoadCursor fail with
# ERROR_RESOURCE_TYPE_NOT_FOUND (1814) for every cursor after
# IDC_UPARROW, which is an artefact of the wrong ids, not a property of
# this build.  IDC_SIZE and IDC_ICON are marked obsolete in the header
# and genuinely do not load.
ALL_IDC = [
    ("ARROW", 32512), ("IBEAM", 32513), ("WAIT", 32514), ("CROSS", 32515),
    ("UPARROW", 32516),
    ("SIZE", 32640), ("ICON", 32641),          # obsolete
    ("SIZENWSE", 32642), ("SIZENESW", 32643), ("SIZEWE", 32644),
    ("SIZENS", 32645), ("SIZEALL", 32646), ("NO", 32648), ("HAND", 32649),
    ("APPSTARTING", 32650), ("HELP", 32651),
    ("PIN", 32671), ("PERSON", 32672),
]

# Backstop for a handle that LoadCursor could not resolve, named by eye
# from its rendered glyph instead.  With the correct IDC_HAND (32649) the
# pointing hand loads normally, so this should never be needed.
KNOWN_BY_GLYPH = {
    0x1001F: "HAND (pointing hand - identified from its glyph)",
}


def load_all() -> dict[int, str]:
    out: dict[int, str] = {}
    for name, idc in ALL_IDC:
        h = user32.LoadCursorW(
            None, ctypes.cast(ctypes.c_void_p(idc), wt.LPCWSTR))
        if h:
            out[int(h)] = name
    return out


def main() -> int:
    mapping = load_all()
    print("Shared handle -> system cursor name")
    print("-" * 44)
    for h in sorted(mapping):
        print(f"  0x{h:X}  {mapping[h]}")

    wanted = [int(a, 0) for a in sys.argv[1:]]
    if wanted:
        print()
        for h in wanted:
            name = mapping.get(h)
            if name:
                print(f"0x{h:X}  =  {name}")
                continue
            d = cs.describe(h)
            if not d or not d["geom"]:
                print(f"0x{h:X}  =  not a cursor handle")
                continue
            if h in KNOWN_BY_GLYPH:
                print(f"0x{h:X}  =  {KNOWN_BY_GLYPH[h]}")
            else:
                print(f"0x{h:X}  =  UNMAPPED  ({d['w']}x{d['h']}, "
                      f"opaque={d['opaque']}, hotspot={d['hot']})")
            print(cs.ascii_art(d, max_w=32, max_h=32))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

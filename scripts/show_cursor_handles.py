"""Render a cursor handle as a text picture.

Usage:  python show_cursor_handles.py 0x10003 0x10005 0x1001F
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cursor_shapes as cs  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for arg in sys.argv[1:]:
        h = int(arg, 0)
        d = cs.describe(h)
        if not d:
            print(f"0x{h:X}  ->  not a cursor handle")
            continue
        print(f"=== handle 0x{h:X}   {d['w']}x{d['h']}   "
              f"opaque={d['opaque']}   hotspot={d['hot']}   "
              f"colour={d['colour']} ===")
        print(cs.ascii_art(d))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

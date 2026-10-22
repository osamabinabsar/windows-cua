"""Watcher 2: what is under the pointer, and does that window class
even have a cursor?

The recon phase turned up the only per-surface discriminator anyone
found that lands exactly on the user's list:

  WNDCLASS.hCursor == NULL  ->  "the system has no cursor for the
  class, and the cursor does not appear when the mouse is over a
  window of this class" (MSDN).

    NULL   : NamespaceTreeControl  (Explorer navigation pane)
             Windows.UI.Core.CoreWindow (Windows search / Start)
             Chrome_RenderWidgetHostHWND in Code.exe (VS Code)
    ARROW  : SHELLDLL_DefView, SysListView32, SysTreeView32,
             DUIViewWndClassName, Chrome_RenderWidgetHostHWND in
             Obsidian / Hermes / Freebuff

This watcher records, per sample:
    pointer position
    the DEEPEST visible child window at that point (walked down with
    ChildWindowFromPointEx, not just the top-level)
    that window's class, and its WNDCLASS.hCursor
    the hCursor of every ancestor up to the top-level window
    the live GetCursorInfo state

and then reports the distinct combinations.

That is enough to confirm or kill the class-cursor hypothesis without
synthesising a single mouse event: the user just moves the mouse.

Read-only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)

GCLP_HCURSOR = -12
CWP_ALL = 0x00000001
CWP_SKIPINVISIBLE = 0x00000002
GA_ROOT = 2


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("flags", wt.DWORD),
        ("hCursor", wt.HANDLE), ("ptScreenPos", POINT),
    ]


user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetClassLongPtrW.restype = ctypes.c_long
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.ChildWindowFromPointEx.argtypes = [wt.HWND, POINT, wt.UINT]
user32.ChildWindowFromPointEx.restype = wt.HWND
user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(POINT)]
user32.ScreenToClient.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND


def class_of(h) -> str:
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def title_of(h) -> str:
    b = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, b, 256)
    return b.value


def pid_of(h) -> int:
    p = wt.DWORD(0)
    user32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def class_cursor(h) -> int:
    return user32.GetClassLongPtrW(h, GCLP_HCURSOR)


def deepest_at(x: int, y: int):
    """Walk from the top-level window down to the deepest visible child
    under the point. Returns (root, chain-of-hwnds)."""
    top = user32.WindowFromPoint(POINT(x, y))
    if not top:
        return None, []
    root = user32.GetAncestor(top, GA_ROOT)
    if not root:
        root = top
    chain = [root]
    cur = root
    for _ in range(12):
        p = POINT(x, y)
        if not user32.ScreenToClient(cur, ctypes.byref(p)):
            break
        child = user32.ChildWindowFromPointEx(cur, p, CWP_ALL | CWP_SKIPINVISIBLE)
        if not child or child == cur:
            break
        chain.append(child)
        cur = child
    return root, chain


def sample():
    pos = POINT()
    if not user32.GetCursorPos(ctypes.byref(pos)):
        return None
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(info)):
        return None
    root, chain = deepest_at(pos.x, pos.y)
    if root is None:
        return None
    return {
        "pos": (pos.x, pos.y),
        "chain": chain,
        "root_class": class_of(root),
        "root_cursor": class_cursor(root),
        "root_pid": pid_of(root),
        "live_hcursor": info.hCursor,
        "flags": info.flags,
    }


def cursor_name(h: int) -> str:
    if h == 0:
        return "NULL (no class cursor)"
    if h == 0x10003:
        return "IDC_ARROW"
    if h == 0x10005:
        return "IDC_IBEAM"
    if h == 0x1001F:
        return "IDC_HAND"
    return f"0x{h:X}"


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    print(f"Watching for {seconds}s.")
    print("Please move the pointer over:")
    print("  * the Explorer navigation pane (where the arrow disappears)")
    print("  * the Explorer file list (where the arrow is visible)")
    print("  * the VS Code file explorer (where it disappears)")
    print("  * the Windows search results list (where it disappears)")
    print()
    seen: dict[tuple, dict] = {}
    end = time.time() + seconds
    n = 0
    while time.time() < end:
        d = sample()
        n += 1
        if d:
            leaf = d["chain"][-1]
            key = (d["root_class"], class_of(leaf), class_cursor(leaf), d["live_hcursor"], d["flags"])
            rec = seen.setdefault(
                key,
                {
                    "n": 0,
                    "root_class": d["root_class"],
                    "root_cursor": d["root_cursor"],
                    "root_pid": d["root_pid"],
                    "leaf": leaf,
                    "leaf_class": class_of(leaf),
                    "leaf_cursor": class_cursor(leaf),
                    "live_hcursor": d["live_hcursor"],
                    "flags": d["flags"],
                    "positions": [],
                    "chain_desc": " <- ".join(
                        f"{class_of(h)}[cursor={cursor_name(class_cursor(h))}]" for h in d["chain"]
                    ),
                },
            )
            rec["n"] += 1
            if len(rec["positions"]) < 8:
                rec["positions"].append(d["pos"])
        time.sleep(0.04)

    print(f"{n} samples.")
    print()
    print("=" * 78)
    print("DISTINCT (window, class cursor, live cursor) COMBINATIONS")
    print("=" * 78)
    for key, rec in sorted(seen.items(), key=lambda kv: -kv[1]["n"]):
        print()
        print(f"  {rec['n']:>6} samples   top-level='{rec['root_class']}' pid={rec['root_pid']}")
        print(f"     leaf window      : '{rec['leaf_class']}'  class cursor = {cursor_name(rec['leaf_cursor'])}")
        print(f"     live GetCursorInfo: hCursor={cursor_name(rec['live_hcursor'])} flags=0x{rec['flags']:08X}"
              f" (SHOWING={int(bool(rec['flags'] & 1))} SUPPRESSED={int(bool(rec['flags'] & 2))})")
        print(f"     chain            : {rec['chain_desc']}")
        print(f"     positions        : {rec['positions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

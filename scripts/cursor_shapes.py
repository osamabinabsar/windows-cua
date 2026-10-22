"""Resolve the live mouse cursor to a SHAPE, and decide mechanism C.

This is the instrument that separates mechanism C (the application
suppressing the cursor for a region) from mechanisms A and B (a display
plane or a cursor-plane refresh failure).

WHY A CURSOR-SHAPE READ IS DECISIVE
-----------------------------------
`GetCursorInfo` reports two things that cannot both be true at once:

  * `CURSOR_SHOWING` (0x1) set, and `hCursor` non-NULL
        -> Windows has a cursor to draw at the pointer.  Whatever is
           wrong, the app is NOT suppressing it, so C is excluded for
           that surface and the fault is in the display path (A or B).
  * `hCursor == NULL` and `CURSOR_SHOWING` clear
        -> the OS has NO cursor for the window under the pointer.  The
           application has suppressed it - `SetCursor(NULL)` from a
           `WM_SETCURSOR` handler, a NULL window-class cursor, or (in a
           Chromium/Electron app) CSS `cursor: none`.  That is
           mechanism C, and it is not a display fault at all.

Earlier revisions of the diagnosis document reasoned that "no screen read
can help, because the pointer's pixels are never in the framebuffer" and
concluded that only a by-eye test could separate the mechanisms.  That
was too strong, and it was wrong in a way that mattered: the framebuffer
route is blind, but the CURSOR-STATE route is not.  A cursor can be
missing from the image because the hardware cursor plane draws it, and
it can be missing because the app asked for no cursor at all - and only
the second is visible to `GetCursorInfo`.

MEASURED ON THIS MACHINE
-------------------------
Over a Chromium render widget (Brave, pid 18400) the live cursor was
NULL with `flags = 0x00000000` (CURSOR_SHOWING clear), stably, across
2956 samples over 90 seconds at one position - while 284 samples over
other positions in the same app showed a live arrow.  Same app, same
window, different region: the pointer is suppressed per region, by the
app.

A two-hour census (3919 samples, 22 distinct chains) put the same
app in the same picture: 48 samples of MECHANISM C at six distinct
positions inside Brave, against 1259 samples of a custom cursor and
1161 of a live ARROW at other positions of the *same* render widget.
Every other app in that run - the terminal, the taskbar, the Explorer
window, Hermes, genspark - reported a real cursor handle, i.e. `not C`.

The census is keyed on (PID, window-class chain, display, cursor state).
The PID matters: every Chromium/Electron app on this machine reports
the same class chain, so without it the rows for Brave, VS Code and the
ChatGPT app silently merge into one.

WHAT THIS SCRIPT DOES
---------------------
Reads the live cursor, rebuilds its glyph from its own bitmaps, and
compares it against the standard system cursors loaded from this same
session - so "0x10003" becomes "ARROW" and "0x0" becomes "NO CURSOR".
In watch mode it records the window class chain and the display for
every sample and prints a census keyed by (chain, display, cursor
state), so a short pass over each affected surface settles it.

NOTE ON THE WORD "SUPPRESSED":  the Win32 CURSORINFO flag
CURSOR_SUPPRESSED (0x2) means the pointer is being driven by TOUCH OR
PEN input - it does NOT mean the cursor is hidden.  A hidden cursor
shows up as a NULL hCursor with CURSOR_SHOWING clear, and this script
labels that state "NO CURSOR" so it can never be mistaken for the
flag.  The flag itself is still printed, correctly named, in the
flags=0x... (SHOWING=... SUPPRESSED=...) line.

Read-only: reads the cursor and the standard cursor resources.  No input
is synthesised, no window is activated or moved, no screen pixels are
printed, and no window title or other personal content is read - only
window CLASSES, process image names, and cursor shapes.

Usage:
    python cursor_shapes.py                # resolve the cursor right now
    python cursor_shapes.py --watch 120    # census every surface for 2 min
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cursorframe  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# The standard IDC_* cursors, from winuser.h.
#
# NOTE the two groups.  The first five are 32512..32516; the rest are
# NOT 32517..32529 - they start at 32640.  Using the obsolete contiguous
# numbering makes LoadCursor fail with ERROR_RESOURCE_TYPE_NOT_FOUND
# (1814) for every cursor after IDC_UPARROW, which reads exactly like
# "this build cannot load those cursors" and is not true.
IDC = {
    "ARROW": 32512, "IBEAM": 32513, "WAIT": 32514, "CROSS": 32515,
    "UPARROW": 32516,
    "SIZE": 32640, "ICON": 32641,          # obsolete, do not load
    "SIZENWSE": 32642, "SIZENESW": 32643, "SIZEWE": 32644, "SIZENS": 32645,
    "SIZEALL": 32646, "NO": 32648, "HAND": 32649, "APPSTARTING": 32650,
    "HELP": 32651, "PIN": 32671, "PERSON": 32672,
}

REFERENCE_IDCS = ("ARROW", "IBEAM", "WAIT", "CROSS", "UPARROW", "SIZEALL",
                  "HAND", "NO", "SIZEWE", "SIZENS", "SIZENWSE", "SIZENESW",
                  "APPSTARTING", "HELP")

# Fallback for a cursor this build will not let LoadCursor load, identified
# by eye from its rendered glyph instead.  Key: (width, height, opaque
# pixel count, hotspot).  The pointing hand is the one that matters here -
# it is what a hyperlink reports - and with the correct IDC_HAND (32649)
# it now loads normally, so this entry is only a backstop.  Verify with
# show_cursor_handles.py.
KNOWN_BY_GLYPH = {
    (32, 32, 285, (6, 0)):
        "HAND (pointing hand - identified from its glyph; LoadCursor could "
        "not load IDC_HAND here)",
}

CWP_ALL = 0x00000001
CWP_SKIPINVISIBLE = 0x00000002
GA_ROOT = 2
MONITORINFOF_PRIMARY = 1


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wt.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("flags", wt.DWORD),
        ("hCursor", wt.HANDLE), ("ptScreenPos", POINT),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
        ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP),
    ]


user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.GetIconInfo.argtypes = [wt.HANDLE, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wt.BOOL
user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
user32.LoadCursorW.restype = wt.HANDLE
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.ChildWindowFromPointEx.argtypes = [wt.HWND, POINT, wt.UINT]
user32.ChildWindowFromPointEx.restype = wt.HWND
user32.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(POINT)]
user32.ScreenToClient.restype = wt.BOOL
user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.POINTER(RECT),
                                        ctypes.c_void_p, ctypes.c_void_p]
user32.EnumDisplayMonitors.restype = wt.BOOL
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFOEX)]
user32.GetMonitorInfoW.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL


# --------------------------------------------------------------------------
# window / display context
# --------------------------------------------------------------------------

_DISPLAYS: list[tuple[str, int, int, int, int]] = []


def _collect_displays() -> None:
    if _DISPLAYS:
        return

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC,
                         ctypes.POINTER(RECT), ctypes.c_void_p)
    def cb(hmon, hdc, lprc, ldata):
        mi = MONITORINFOEX()
        mi.cbSize = ctypes.sizeof(MONITORINFOEX)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            _DISPLAYS.append((
                "PRIMARY" if mi.dwFlags & MONITORINFOF_PRIMARY else "secondary",
                r.left, r.top, r.right, r.bottom,
            ))
        return True

    user32.EnumDisplayMonitors(None, None, cb, None)
    if not _DISPLAYS:
        _DISPLAYS.append(("unknown", 0, 0, 1920, 1080))


def display_at(x: int, y: int) -> str:
    _collect_displays()
    for label, l, t, r, b in _DISPLAYS:
        if l <= x < r and t <= y < b:
            return label
    return "off-screen"


def class_of(h) -> str:
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def pid_of(h) -> int:
    p = wt.DWORD(0)
    user32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def process_name(pid: int) -> str:
    """The executable name of a process - no title, no content.

    NOTE: `GetModuleFileNameExW` lives in psapi.dll, NOT kernel32 - an
    earlier revision of this script called it on the kernel32 handle,
    which does not export it, so `process_name()` ALWAYS fell back to
    "pid=NNNN" and never printed a program name.  `QueryFullProcessImageNameW`
    is exported by kernel32 and only needs PROCESS_QUERY_LIMITED_INFORMATION,
    which is the access mask already used below.
    """
    if not pid:
        return "?"
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return f"pid={pid}"
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return f"pid={pid}"
        return os.path.basename(buf.value) or f"pid={pid}"
    except Exception:
        return f"pid={pid}"
    finally:
        kernel32.CloseHandle(h)


def chain_at(x: int, y: int):
    """(root, [hwnds from root down to the deepest child under the point])."""
    top = user32.WindowFromPoint(POINT(x, y))
    if not top:
        return None, []
    root = user32.GetAncestor(top, GA_ROOT) or top
    chain = [root]
    cur = root
    for _ in range(12):
        p = POINT(x, y)
        if not user32.ScreenToClient(cur, ctypes.byref(p)):
            break
        child = user32.ChildWindowFromPointEx(
            cur, p, CWP_ALL | CWP_SKIPINVISIBLE)
        if not child or child == cur:
            break
        chain.append(child)
        cur = child
    return root, chain


# --------------------------------------------------------------------------
# cursor shape resolution
# --------------------------------------------------------------------------

def _makeintresource(i: int):
    """MAKEINTRESOURCE(idc) - a small integer used as a pointer."""
    return ctypes.cast(ctypes.c_void_p(i), wt.LPCWSTR)


def geometry(shape) -> frozenset:
    """The set of opaque pixel positions - the outline of the glyph."""
    return frozenset((x, y) for (x, y, _b, _g, _r, a) in shape if a)


def full(shape) -> frozenset:
    """The set of opaque pixels WITH their colours."""
    return frozenset((x, y, b, g, r) for (x, y, b, g, r, a) in shape if a)


def hotspot_of(hcursor) -> tuple[int, int]:
    ii = ICONINFO()
    if not user32.GetIconInfo(hcursor, ctypes.byref(ii)):
        return (-1, -1)
    return (ii.xHotspot, ii.yHotspot)


def why_unresolved(hcursor) -> str:
    """Why could the glyph not be read?  A handle with an unreadable
    glyph is NOT a blank cursor and NOT a suppressed one - the OS still
    has a cursor for that window, so mechanism C is excluded."""
    ii = ICONINFO()
    if not user32.GetIconInfo(hcursor, ctypes.byref(ii)):
        return (f"GetIconInfo failed (err {ctypes.get_last_error()}) - the "
                "handle is not a cursor this process can describe")
    if not ii.hbmColor and not ii.hbmMask:
        return "the cursor has neither a colour nor a mask bitmap"
    hdc = user32.GetDC(None)
    try:
        if ii.hbmColor:
            w, h, _ = cursorframe._bitmap_size(ii.hbmColor)
            if cursorframe._read_dib(hdc, ii.hbmColor, 32, w, h) is None:
                return ("the colour bitmap could not be read (GetDIBits "
                        "failed) - the cursor exists but its pixels are not "
                        "readable from this process")
            return "the colour bitmap read, but it carried no visible pixels"
        return "the monochrome mask bitmap could not be read"
    finally:
        user32.ReleaseDC(None, hdc)


def describe(hcursor) -> dict | None:
    """Resolve one cursor handle to a shape description."""
    if not hcursor:
        return None
    shape, w, h = cursorframe.cursor_shape(hcursor)
    if not shape:
        return {"name": "UNRESOLVED", "w": 0, "h": 0, "opaque": 0,
                "hot": (-1, -1), "geom": frozenset(), "full": frozenset(),
                "colour": False, "why": why_unresolved(hcursor)}
    opaque = [p for p in shape if p[5]]
    colour = any(p[2] or p[3] or p[4] for p in opaque)
    return {
        "name": None, "w": w, "h": h, "opaque": len(opaque),
        "hot": hotspot_of(hcursor),
        "geom": geometry(shape), "full": full(shape),
        "colour": colour,
    }


def build_reference() -> list[tuple[str, dict]]:
    """Load the standard cursors and describe them."""
    out = []
    for name in REFERENCE_IDCS:
        h = user32.LoadCursorW(None, _makeintresource(IDC[name]))
        if not h:
            continue
        d = describe(h)
        if d and d["geom"]:
            d["name"] = name
            out.append((name, d))
    return out


def name_for(desc: dict, reference: list[tuple[str, dict]]) -> str:
    """Name a live cursor by comparing it with the reference set."""
    if not desc or not desc["geom"]:
        return "BLANK / UNRESOLVED"
    for name, ref in reference:
        if desc["full"] == ref["full"] and desc["hot"] == ref["hot"]:
            return name
    for name, ref in reference:
        if desc["geom"] == ref["geom"] and desc["hot"] == ref["hot"]:
            return f"{name} (same outline, different colours)"
    sig = (desc["w"], desc["h"], desc["opaque"], desc["hot"])
    if sig in KNOWN_BY_GLYPH:
        return KNOWN_BY_GLYPH[sig]
    return "CUSTOM - not a standard system cursor"


def ascii_art(desc: dict, max_w: int = 24, max_h: int = 18) -> str:
    """A text picture of the glyph, so the shape can be judged by eye."""
    if not desc or not desc["geom"]:
        return "  (no glyph)"
    g = desc["geom"]
    xs = [p[0] for p in g]
    ys = [p[1] for p in g]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    lines = []
    for y in range(y0, min(y1, y0 + max_h - 1) + 1):
        row = "".join("#" if (x, y) in g else "." for x in
                      range(x0, min(x1, x0 + max_w - 1) + 1))
        lines.append("  " + row)
    return "\n".join(lines)


def state_of(ci: CURSORINFO, reference) -> tuple[str, dict | None]:
    """(state name, description) for the live cursor.

    The state name is deliberately NOT called "SUPPRESSED": the Win32
    CURSORINFO flag CURSOR_SUPPRESSED (0x2) means the pointer is being
    driven by TOUCH OR PEN input, not that the cursor is hidden.  The
    hidden case is a NULL hCursor with CURSOR_SHOWING clear, and it is
    called "NO CURSOR" here so nobody reads it as the flag.
    """
    if not ci.hCursor:
        return ("NO CURSOR - the OS has NO cursor for this window", None)
    d = describe(ci.hCursor)
    if not d or not d["geom"]:
        return ("UNRESOLVED - a cursor handle, but its glyph could not be "
                "read (this is NOT a blank cursor and NOT a hidden one: "
                "the OS still has a cursor here, so mechanism C is excluded)",
                d)
    return (name_for(d, reference), d)


def live() -> CURSORINFO:
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)):
        raise SystemExit("GetCursorInfo failed")
    return ci


# --------------------------------------------------------------------------

def main() -> int:
    watch = 0
    if "--watch" in sys.argv:
        i = sys.argv.index("--watch")
        # `--watch` with no duration used to raise IndexError (the guard
        # was `len(sys.argv) + 1 > i`, which is true whenever the flag is
        # present at all).  A silent 15-minute default is worse than a
        # clear refusal, so a missing or unusable duration is an error.
        raw = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        try:
            watch = int(raw)
        except ValueError:
            watch = 0
        if watch <= 0:
            print("cursor_shapes.py --watch needs a duration in seconds, "
                  "e.g. --watch 120", file=sys.stderr)
            print("  (the census is printed when the run ends, or on "
                  "Ctrl+C)", file=sys.stderr)
            return 2

    reference = build_reference()
    print("Standard cursors available for comparison: "
          + ", ".join(n for n, _ in reference))
    print()

    def describe_here(ci: CURSORINFO, label: str) -> dict:
        showing = int(bool(ci.flags & 1))
        suppressed = int(bool(ci.flags & 2))
        h = int(ci.hCursor or 0)
        print(f"{label}  handle=0x{h:X}  flags=0x{ci.flags:08X} "
              f"(SHOWING={showing} SUPPRESSED={suppressed})")
        p = POINT()
        user32.GetCursorPos(ctypes.byref(p))
        root, chain = chain_at(p.x, p.y)
        names = [class_of(c) for c in chain]
        print(f"   pointer : ({p.x},{p.y})  display={display_at(p.x, p.y)}")
        print(f"   chain   : {' > '.join(names) if names else '(none)'}"
              + (f"   [{process_name(pid_of(root))}]" if root else ""))
        state, d = state_of(ci, reference)
        print(f"   state   : {state}")
        if d and d["geom"]:
            print(f"   size    : {d['w']}x{d['h']}  opaque pixels: {d['opaque']}"
                  f"  hotspot: {d['hot']}  colour: {d['colour']}")
            print(ascii_art(d))
        elif not ci.hCursor:
            print("   -> MECHANISM C: the application has suppressed the "
                  "cursor for this region.")
        return d or {}

    ci = live()
    describe_here(ci, "LIVE CURSOR NOW")

    if not watch:
        return 0

    print(f"\nWatching for {watch}s. Hover the EXPLORER NAV PANE, the FILE "
          "LIST, the Windows Search list, the VS Code tree, and the chat area.")
    print("Each new (chain, display, cursor state) combination is reported "
          "as it appears.\n")

    census: dict[tuple, dict] = {}
    n = 0
    end = time.time() + watch
    last = None
    try:
        while time.time() < end:
            ci = live()
            p = POINT()
            user32.GetCursorPos(ctypes.byref(p))
            if last == (p.x, p.y):
                time.sleep(0.03)
                continue
            last = (p.x, p.y)
            root, chain = chain_at(p.x, p.y)
            if root is None:
                time.sleep(0.03)
                continue
            names = tuple(class_of(c) for c in chain)
            state, _d = state_of(ci, reference)
            disp = display_at(p.x, p.y)
            pid = pid_of(root)
            # The PID MUST be part of the key.  Every Chromium/Electron app
            # on this machine reports the same class chain
            # (Chrome_WidgetWin_1 > Chrome_RenderWidgetHostHWND), so a key
            # of (chain, display, state) silently MERGES different apps into
            # one row and reports whichever app was seen first.
            key = (pid, names, disp, state)
            rec = census.get(key)
            if rec is None:
                rec = census[key] = {
                    "n": 0, "pid": pid,
                    "proc": process_name(pid), "pos": [],
                }
                print(f"  [new] {' > '.join(names)}")
                print(f"        display={disp}  state={state}  "
                      f"[{rec['proc']} pid={pid}]")
            rec["n"] += 1
            if len(rec["pos"]) < 6:
                rec["pos"].append((p.x, p.y))
            n += 1
            time.sleep(0.03)
    except KeyboardInterrupt:
        pass

    print(f"\n{n} samples, {len(census)} distinct (pid, chain, display, state) "
          "combinations.\n")
    print("=" * 78)
    print("CENSUS")
    print("=" * 78)
    for (pid, names, disp, state), rec in sorted(census.items(),
                                                 key=lambda kv: -kv[1]["n"]):
        print()
        print(f"  {rec['n']:>5} samples   [{rec['proc']} pid={pid}]  "
              f"display={disp}")
        print(f"     chain : {' > '.join(names)}")
        print(f"     state : {state}")
        if len(rec["pos"]) < rec["n"]:
            print(f"     at    : {rec['pos']}")
        if state.startswith("NO CURSOR"):
            print("     VERDICT: MECHANISM C - the app suppressed the cursor "
                  "here.")
        else:
            print("     VERDICT: not C - the OS had a real cursor here, so "
                  "the fault is in the display path (A or B).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

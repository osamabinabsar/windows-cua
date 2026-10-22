"""Second pass: identify the overlay processes and read the live cursor state.

Read-only. Answers:
  - what is QAAgent.exe, and what is the CodexComputerUseSwiftOverlay?
  - is the pointer suppressed, and which cursor image is current?
  - is a mouse button or modifier key stuck down?
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import subprocess

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hCursor", wt.HANDLE),
        ("hScreen", wt.HANDLE),
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


def main() -> int:
    print("=" * 72)
    print("A. STRUCT SIZES (sanity)")
    print("=" * 72)
    print(f"  sizeof(CURSORINFO) = {ctypes.sizeof(CURSORINFO)} (expected 32 on x64)")
    print(f"  sizeof(ICONINFO)   = {ctypes.sizeof(ICONINFO)} (expected 24 on x64)")

    print()
    print("=" * 72)
    print("B. LIVE CURSOR STATE")
    print("=" * 72)
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetCursorInfo.restype = wt.BOOL
    user32.GetIconInfo.argtypes = [wt.HANDLE, ctypes.POINTER(ICONINFO)]
    user32.GetIconInfo.restype = wt.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wt.BOOL
    user32.SystemParametersInfoW.argtypes = [
        wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT
    ]
    user32.SystemParametersInfoW.restype = wt.BOOL
    user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
    user32.LoadCursorW.restype = wt.HANDLE
    user32.GetCursor.argtypes = []
    user32.GetCursor.restype = wt.HANDLE

    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    ok = user32.GetCursorInfo(ctypes.byref(info))
    if not ok:
        print(f"  GetCursorInfo failed, winerr={ctypes.get_last_error()}")
    else:
        showing = bool(info.flags & 0x00000001)
        suppressed = bool(info.flags & 0x00000002)
        print(f"  flags        : 0x{info.flags:08x}")
        print(f"  SHOWING      : {showing}")
        print(f"  SUPPRESSED   : {suppressed}")
        print(f"  position     : ({info.ptScreenPos.x}, {info.ptScreenPos.y})")
        print(f"  hCursor      : {info.hCursor}")

        standard = user32.LoadCursorW(None, 32512)  # IDC_ARROW
        print(f"  IDC_ARROW    : {standard}")
        print(
            "  current cursor IS the standard arrow"
            if info.hCursor == standard
            else "  current cursor is NOT the standard arrow (app set a custom one)"
        )

        ii = ICONINFO()
        if user32.GetIconInfo(info.hCursor, ctypes.byref(ii)):
            print(f"  hotspot      : ({ii.xHotspot}, {ii.yHotspot})")

    vanish = wt.DWORD(0)
    user32.SystemParametersInfoW(0x0017, 0, ctypes.byref(vanish), 0)  # SPI_GETMOUSEVANISH
    print(f"  SPI_GETMOUSEVANISH (hide while typing) = {vanish.value}")

    pos = POINT()
    user32.GetCursorPos(ctypes.byref(pos))
    print(f"  GetCursorPos : ({pos.x}, {pos.y})")

    print()
    print("=" * 72)
    print("C. STUCK KEYS (GetAsyncKeyState high bit)")
    print("=" * 72)
    keys = {
        0x01: "VK_LBUTTON",
        0x02: "VK_RBUTTON",
        0x04: "VK_MBUTTON",
        0x10: "VK_SHIFT",
        0x11: "VK_CONTROL",
        0x12: "VK_MENU(ALT)",
        0x5B: "VK_LWIN",
        0x5C: "VK_RWIN",
        0x14: "VK_CAPITAL",
        0x91: "VK_SCROLL",
        0x90: "VK_NUMLOCK",
    }
    for vk, name in keys.items():
        state = user32.GetAsyncKeyState(vk)
        down = bool(state & 0x8000)
        print(f"  {name:<16} down={down}  (0x{state & 0xFFFF:04X})")

    print()
    print("=" * 72)
    print("D. THE SUSPECT PROCESSES")
    print("=" * 72)
    for proc_name in ("QAAgent", "codex-computer-use", "Code", "explorer"):
        print(f"\n  --- {proc_name} ---")
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | Where-Object { $_.Name -like '"
                + proc_name
                + "*' } | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,"
                "CommandLine,CreationDate | ConvertTo-Json -Depth 3",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        raw = out.stdout.strip()
        if not raw:
            print("    (none running)")
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print("    (no rows)")
            continue
        if isinstance(data, dict):
            data = [data]
        for row in data:
            print(f"    pid={row.get('ProcessId')} ppid={row.get('ParentProcessId')}")
            print(f"    exe={row.get('ExecutablePath')}")
            cmd = (row.get("CommandLine") or "")[:200]
            print(f"    cmd={cmd}")
            print(f"    started={row.get('CreationDate')}")

    print()
    print("=" * 72)
    print("E. FILE VERSION INFO FOR THE OVERLAY BUILDS")
    print("=" * 72)
    ps = (
        "$paths = @("
        "$env:LOCALAPPDATA\\OpenAI\\Codex\\runtimes\\cua_node\\*\\bin\\node_modules\\@oai\\sky\\bin\\windows\\codex-computer-use.exe"
        ",$env:LOCALAPPDATA\\Microsoft\\WindowsApps\\QAAgent.exe"
        ",$env:LOCALAPPDATA\\Programs\\*\\QAAgent.exe"
        ",$env:ProgramFiles\\*\\QAAgent.exe"
        ");"
        "$rows = foreach ($p in $paths) {"
        "  Get-Item $p -ErrorAction SilentlyContinue | ForEach-Object {"
        "    $v = $_.VersionInfo;"
        "    [PSCustomObject]@{ Path=$_.FullName; Company=$v.CompanyName; Product=$v.ProductName;"
        "      Description=$v.FileDescription; Version=$v.FileVersion } } };"
        "$rows | ConvertTo-Json -Depth 3"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=60,
    )
    print(out.stdout.strip() or "  (no version info found)")
    if out.stderr.strip():
        print("  stderr:", out.stderr.strip()[:400])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

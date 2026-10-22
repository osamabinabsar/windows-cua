"""Probe: can an external process end the Codex helper's computer-use turn?

READ-ONLY except for one SetEvent on a named event. The helper is spawned the
same way the MCP server spawns it, and only read-only calls are made
(`list_apps`).  Nothing on the user's screen is touched.

What it establishes:
  1. Does the helper create the named event `Local\\CodexComputerUseTurnEnded`
     while it is running?
  2. If it does, does signalling it make the helper refuse further
     computer-use calls (i.e. is it a real "take over" lever)?
  3. Does the event get reset afterwards, so a later call still works?
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

EVENT_NAME = "Local\\CodexComputerUseTurnEnded"
EVENT_MODIFY_STATE = 0x0002
EVENT_ALL_ACCESS = 0x1F0003

kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def open_event(name: str, access: int) -> int:
    handle = kernel32.OpenEventW(access, False, name)
    if not handle:
        err = ctypes.get_last_error()
        print(f"  OpenEventW({name!r}) failed: winerror {err}")
        return 0
    return handle


def find_helper() -> str:
    from pathlib import Path
    import glob

    pattern = str(
        Path.home()
        / "AppData/Local/OpenAI/Codex/runtimes/*/*/bin/node_modules/@oai/sky/bin/windows/codex-computer-use.exe"
    )
    hits = sorted(glob.glob(pattern))
    if not hits:
        raise SystemExit("helper not found")
    return hits[-1]


def call(proc: subprocess.Popen, method: str, params: dict | None = None) -> dict:
    payload = json.dumps({"id": 1, "method": method, "params": params or {}}) + "\n"
    proc.stdin.write(payload.encode("utf-8"))
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        return {"error": "helper closed stdout"}
    return json.loads(line.decode("utf-8"))


def find_codex_cli() -> str | None:
    from pathlib import Path
    import glob

    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
    hits = sorted(glob.glob(os.path.join(local, "OpenAI/Codex/bin/*/codex.exe")))
    return hits[-1] if hits else None


def main() -> int:
    helper = find_helper()
    cli = find_codex_cli()
    print(f"helper: {helper}")
    print(f"codex cli: {cli}\n")
    env = os.environ.copy()
    if cli:
        env["CODEX_CLI_PATH"] = cli
    proc = subprocess.Popen(
        [helper, "--parent-pid", str(os.getpid())],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        result = call(proc, "list_apps", {})
        ok = "result" in result
        print(f"1. list_apps -> {'OK' if ok else 'FAILED: ' + str(result)[:200]}")

        handle = open_event(EVENT_NAME, EVENT_MODIFY_STATE)
        print(f"2. OpenEventW({EVENT_NAME!r}, EVENT_MODIFY_STATE) -> "
              f"{'handle 0x%x' % handle if handle else 'NOT FOUND'}")
        if not handle:
            print("   -> the helper does not expose a signallable turn-ended event")
            return 0

        set_ok = kernel32.SetEvent(handle)
        print(f"3. SetEvent -> {bool(set_ok)} (winerror {ctypes.get_last_error()})")
        time.sleep(0.4)

        after = call(proc, "list_apps", {})
        print(f"4. list_apps after SetEvent -> "
              f"{'OK' if 'result' in after else 'REFUSED: ' + str(after)[:300]}")
        return 0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())

"""Read the `trycua/cua` driver's own tool schemas.

The driver at https://github.com/trycua/cua is an MCP server. This asks
it for its tool list and prints the *schemas* -- argument names, types,
and required sets -- for the tools this project maps onto. No actions are
performed; this only reads the driver's own description of itself.

Run with:  .venv\\Scripts\\python.exe scripts\\probe_cua_driver.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.cua_backend import find_cua_driver  # noqa: E402

WANTED = [
    "list_apps",
    "list_windows",
    "get_window_state",
    "get_accessibility_tree",
    "click",
    "type_text",
    "press_key",
    "set_value",
    "scroll",
    "drag",
    "launch_app",
    "kill_app",
    "bring_to_front",
    "invoke_menu",
]


def main() -> int:
    driver = find_cua_driver()
    print(f"driver: {driver}\n")

    proc = subprocess.Popen(
        [str(driver), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    pending: dict[int, list] = {}

    def reader():
        for raw in iter(proc.stdout.readline, b""):
            if not raw:
                break
            try:
                msg = json.loads(raw.decode("utf-8", "replace").strip())
            except json.JSONDecodeError:
                continue
            mid = msg.get("id")
            if isinstance(mid, int):
                pending.setdefault(mid, []).append(msg)

    def stderr_reader():
        for raw in iter(proc.stderr.readline, b""):
            text = raw.decode("utf-8", "replace").rstrip()
            if text:
                print(f"  [stderr] {text[:200]}")

    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=stderr_reader, daemon=True).start()

    def send(msg):
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        proc.stdin.flush()

    send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "probe", "version": "1"},
            },
        }
    )
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

    import time

    deadline = time.time() + 30
    while time.time() < deadline and 2 not in pending:
        time.sleep(0.1)

    if 2 not in pending:
        print("no tools/list response")
        proc.terminate()
        return 1

    result = pending[2][0].get("result") or {}
    tools = result.get("tools") or []
    print(f"{len(tools)} tools available\n")
    print("ALL TOOL NAMES:")
    for tool in tools:
        if isinstance(tool, dict) and tool.get("name"):
            print(f"  {tool['name']}")
    print()

    by_name = {t.get("name"): t for t in tools if isinstance(t, dict)}
    for name in WANTED:
        tool = by_name.get(name)
        if tool is None:
            print(f"--- {name}: ABSENT ---")
            continue
        schema = tool.get("inputSchema") or {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        print(f"--- {name} ---")
        for key, spec in props.items():
            if not isinstance(spec, dict):
                continue
            typ = spec.get("type", spec.get("enum", "?"))
            mark = "*" if key in required else " "
            enum = spec.get("enum")
            extra = f" enum={enum}" if enum else ""
            print(f"  {mark}{key}: {typ}{extra}")
        print()

    proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

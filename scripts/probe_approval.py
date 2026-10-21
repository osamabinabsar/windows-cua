"""Probe 2: what does an approval request look like on the stdio wire?

Launches Notepad (a scratch app), clicks into it, and prints whatever the
helper sends back. The point is to learn the exact approval-request shape so
the MCP server can implement a real approval flow instead of forging one.

Run with:  uv run --no-project --python 3.14 -- python -I scripts/probe_approval.py
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

HELPER = Path(
    r"C:\Users\Anagh\AppData\Local\OpenAI\Codex\runtimes\cua_node"
    r"\a9ba6825a525118c\bin\node_modules\@oai\sky\bin\windows\codex-computer-use.exe"
)
CODEX_CLI = Path(
    r"C:\Users\Anagh\AppData\Local\OpenAI\Codex\bin\9614f1d9b40d9c4f\codex.exe"
)

env = os.environ.copy()
env["CODEX_CLI_PATH"] = str(CODEX_CLI)
env["CODEX_HOME"] = str(Path.home() / ".codex")

proc = subprocess.Popen(
    [str(HELPER), "--parent-pid", str(os.getpid())],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=env,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
)

pending: dict[int, tuple[threading.Event, list]] = {}


def read_loop() -> None:
    for raw in iter(proc.stdout.readline, b""):
        if not raw:
            break
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print("  [non-json]", line[:300])
            continue
        print("  <<", json.dumps(msg)[:700])
        mid = msg.get("id")
        if isinstance(mid, int) and mid in pending:
            ev, slot = pending[mid]
            slot.append(msg)
            ev.set()


def drain_stderr() -> None:
    for raw in iter(proc.stderr.readline, b""):
        t = raw.decode("utf-8", "replace").rstrip()
        if t:
            print("  [stderr]", t[:400])


threading.Thread(target=read_loop, daemon=True).start()
threading.Thread(target=drain_stderr, daemon=True).start()

_next = [0]


def call(method: str, params: dict | None = None, meta: dict | None = None, timeout: float = 25.0):
    _next[0] += 1
    mid = _next[0]
    frame = {"id": mid, "method": method, "params": params or {}}
    if meta:
        frame["meta"] = meta
    ev = threading.Event()
    slot: list = []
    pending[mid] = (ev, slot)
    proc.stdin.write((json.dumps(frame) + "\n").encode("utf-8"))
    proc.stdin.flush()
    if not ev.wait(timeout):
        return {"_timeout": True}
    return slot[0] if slot else {"_timeout": True}


print("--- launch_app notepad ---")
print("  >>", json.dumps({"id": 1, "method": "launch_app", "params": {"app": "notepad"}}))
r = call("launch_app", {"app": "notepad"})
print("  RESULT:", json.dumps(r)[:500])

import time
time.sleep(2.5)

print("\n--- list_windows after launch ---")
r = call("list_windows", {})
wins = r.get("result") or []
if isinstance(wins, dict):
    wins = wins.get("windows") or []
wins = [w for w in wins if isinstance(w, dict)]
note = next((w for w in wins if "notepad" in str(w.get("app", "")).lower()), None)
print("  notepad window:", json.dumps(note) if note else "NOT FOUND")
print("  all:", json.dumps(wins)[:800])

if note:
    print("\n--- click into notepad ---")
    r = call(
        "click",
        {
            "window": {"app": note["app"], "id": note["id"]},
            "x": 120,
            "y": 70,
            "click_count": 1,
            "mouse_button": "left",
        },
    )
    print("  RESULT:", json.dumps(r)[:900])

call("close", {})
try:
    proc.terminate()
except OSError:
    pass

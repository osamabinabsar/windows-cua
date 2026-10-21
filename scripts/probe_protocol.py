"""Live probe: learn the exact wire shapes of codex-computer-use.exe on stdio.

Deliberately does NOT forge the ``x-oai-cua-approved-app`` metadata key. That
key is the helper's record of a real user approval, and this probe only
wants to *observe* what an approval request looks like so the MCP server can
implement the flow properly.

Read-only calls plus one benign click into Notepad (a scratch app, no user
data at risk).

Run with:  uv run --no-project --python 3.14 -- python -I scripts/probe_protocol.py
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
        mid = msg.get("id")
        if isinstance(mid, int) and mid in pending:
            ev, slot = pending[mid]
            slot.append(msg)
            ev.set()
    print("[helper stdout closed]")
    for ev, _slot in pending.values():
        ev.set()


def drain_stderr() -> None:
    for raw in iter(proc.stderr.readline, b""):
        t = raw.decode("utf-8", "replace").rstrip()
        if t:
            print("  [stderr]", t[:400])


threading.Thread(target=read_loop, daemon=True).start()
threading.Thread(target=drain_stderr, daemon=True).start()

_next = [0]


def call(method: str, params: dict | None = None, timeout: float = 25.0):
    _next[0] += 1
    mid = _next[0]
    frame = {"id": mid, "method": method, "params": params or {}}
    ev = threading.Event()
    slot: list = []
    pending[mid] = (ev, slot)
    proc.stdin.write((json.dumps(frame) + "\n").encode("utf-8"))
    proc.stdin.flush()
    if not ev.wait(timeout):
        return {"_timeout": True}
    return slot[0] if slot else {"_timeout": True}


def show(label: str, msg) -> None:
    print(f"\n=== {label} ===")
    if not isinstance(msg, dict) or msg.get("_timeout"):
        print("  TIMEOUT")
        return
    print("  top-level keys:", sorted(msg.keys()))
    print("  ok:", msg.get("ok"))
    if "error" in msg:
        print("  error:", json.dumps(msg["error"])[:600])
    if "approvalRequest" in msg:
        print("  approvalRequest:", json.dumps(msg["approvalRequest"])[:600])
    if "meta" in msg:
        print("  meta:", json.dumps(msg["meta"])[:300])
    if "result" in msg:
        res = msg["result"]
        if isinstance(res, list):
            print(f"  result: LIST len={len(res)}")
            if res:
                print("  result[0]:", json.dumps(res[0])[:800])
        elif isinstance(res, dict):
            print("  result keys:", sorted(res.keys()))
            for k, v in res.items():
                s = v if isinstance(v, str) else json.dumps(v)
                print(f"    {k}: {str(s)[:300]}")
        else:
            print("  result:", json.dumps(res)[:300])


# --- read-only probes -----------------------------------------------------
show("list_windows", call("list_windows", {}))
show("list_apps", call("list_apps", {}))

wins = call("list_windows", {}).get("result") or []
if isinstance(wins, dict):
    wins = wins.get("windows") or []
wins = [w for w in wins if isinstance(w, dict)]
print("\napps seen:", sorted({str(w.get("app")) for w in wins}))

notepad = next(
    (w for w in wins if "notepad" in str(w.get("app", "")).lower()), None
)
print("notepad window:", json.dumps(notepad) if notepad else "NOT OPEN")

if notepad:
    show("get_window", call("get_window", {"id": notepad["id"], "app": notepad["app"]}))
    show(
        "get_window_state (text only)",
        call(
            "get_window_state",
            {
                "window": {"app": notepad["app"], "id": notepad["id"]},
                "include_screenshot": False,
                "include_text": True,
            },
        ),
    )
    # Benign click into Notepad. If the app is not yet approved, this should
    # come back as an approval request -- that is the shape we want to see.
    show(
        "click into notepad (expect approval request)",
        call(
            "click",
            {
                "window": {"app": notepad["app"], "id": notepad["id"]},
                "x": 100,
                "y": 60,
                "click_count": 1,
                "mouse_button": "left",
            },
        ),
    )

show("close", call("close", {}))
try:
    proc.terminate()
except OSError:
    pass

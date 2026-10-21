"""Shared harness for the live protocol probes.

Owns the helper subprocess, the JSON-RPC framing, and -- importantly -- the
redaction rules. A probe prints the *shape* of the helper's replies, never
the content: window titles and accessibility text can carry the name of the
document you have open, and a probe has no business printing that into a
terminal or a transcript.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.transport import find_codex_cli, find_helper  # noqa: E402

# Keys whose values are personal or otherwise uninteresting to a probe.
_ELIDED_KEYS = frozenset(
    {"title", "displayName", "value", "text", "name", "content", "description"}
)

# Keys that carry bulk screen content rather than a short string.
_BULK_KEYS = frozenset({"tree", "screenshot", "image", "png", "jpeg", "base64"})


def redact(obj: Any) -> Any:
    """Replace personal strings in a helper reply with a placeholder.

    Keeps the key and the type, drops the value. A protocol probe needs the
    shape of the reply; it does not need what is on your screen.

    Accessibility trees and screenshots are reduced to a structural summary:
    the element roles and their count, never the text or pixels.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _ELIDED_KEYS and isinstance(v, str):
                out[k] = "<elided>"
            elif k in _BULK_KEYS:
                if k == "tree" and isinstance(v, str):
                    out[k] = f"<tree {len(v)} chars: {shape(v)}>"
                elif isinstance(v, str):
                    out[k] = f"<{len(v)} bytes elided>"
                elif isinstance(v, list):
                    out[k] = f"<{len(v)} items elided>"
                else:
                    out[k] = "<elided>"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def shape(tree: str) -> str:
    """The tree's element roles and index numbers, with names elided."""
    import re

    roles = re.findall(r"^\s*(\d+)\s+([a-z_]+)", tree, re.MULTILINE)
    return " -> ".join(f"{i}:{role}" for i, role in roles[:12]) or "(no elements)"


class Probe:
    """A live helper subprocess you can send JSON-RPC frames to."""

    def __init__(self, *, echo: bool = False) -> None:
        self.helper = find_helper()
        self.codex_cli = find_codex_cli()
        self.echo = echo

        env = os.environ.copy()
        if self.codex_cli is not None:
            env["CODEX_CLI_PATH"] = str(self.codex_cli)
        env["CODEX_HOME"] = str(Path.home() / ".codex")

        self.proc = subprocess.Popen(
            [str(self.helper), "--parent-pid", str(os.getpid())],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._next = 0
        self._lock = threading.Lock()

        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    # -- plumbing ----------------------------------------------------------

    def _read_loop(self) -> None:
        for raw in iter(self.proc.stdout.readline, b""):
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print("  [non-json]", line[:200])
                continue
            if self.echo:
                print("  <<", json.dumps(redact(msg))[:500])
            mid = msg.get("id")
            if isinstance(mid, int) and mid in self._pending:
                ev, slot = self._pending[mid]
                slot.append(msg)
                ev.set()
        print("[helper stdout closed]")
        for ev, _slot in list(self._pending.values()):
            ev.set()

    def _drain_stderr(self) -> None:
        for raw in iter(self.proc.stderr.readline, b""):
            t = raw.decode("utf-8", "replace").rstrip()
            if t:
                print("  [stderr]", t[:300])

    # -- api ---------------------------------------------------------------

    def call(
        self,
        method: str,
        params: dict | None = None,
        meta: dict | None = None,
        timeout: float = 25.0,
    ) -> dict:
        with self._lock:
            self._next += 1
            mid = self._next
        frame: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if meta:
            frame["meta"] = meta
        ev = threading.Event()
        slot: list = []
        self._pending[mid] = (ev, slot)
        self.proc.stdin.write((json.dumps(frame) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        if not ev.wait(timeout):
            return {"_timeout": True}
        return slot[0] if slot else {"_timeout": True}

    def close(self) -> None:
        try:
            self.call("close", {}, timeout=3.0)
        except Exception:
            pass
        try:
            self.proc.terminate()
        except OSError:
            pass


def show(label: str, msg: Any) -> None:
    """Print a reply's shape, with personal values elided."""
    print(f"\n=== {label} ===")
    if not isinstance(msg, dict) or msg.get("_timeout"):
        print("  TIMEOUT")
        return
    msg = redact(msg)
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


def state_shape(result: Any) -> None:
    """Print the shape of a ``get_window_state`` result, not its content."""
    if not isinstance(result, dict):
        print("  RESULT:", json.dumps(redact(result))[:300])
        return
    acc = result.get("accessibility") or {}
    if isinstance(acc, dict):
        print("  accessibility keys:", sorted(acc.keys()))
        tree = acc.get("tree")
        if isinstance(tree, str):
            print(f"  tree length: {len(tree)} chars")
            print("  tree shape:", shape(tree))
    else:
        print("  accessibility:", type(acc).__name__)
    shots = result.get("screenshots")
    if isinstance(shots, list):
        print("  screenshots:", len(shots))

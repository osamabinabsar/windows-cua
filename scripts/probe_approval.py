"""Probe 2: what does an approval request look like on the stdio wire?

Launches Notepad and prints whatever the helper sends back. The point is to
learn the exact approval-request shape so the MCP server can implement a
real approval flow instead of forging one.

This probe only *launches*; it never clicks or types, so it cannot modify
whatever document happens to be open in Notepad. Output is redacted.

Run with:  .venv\\Scripts\\python.exe scripts/probe_approval.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _probe import Probe, state_shape  # noqa: E402

probe = Probe(echo=True)

print("--- launch_app notepad ---")
r = probe.call("launch_app", {"app": "notepad"})
print("  RESULT keys:", sorted(r.keys()) if isinstance(r, dict) else type(r))

time.sleep(2.5)

print("\n--- list_windows after launch ---")
r = probe.call("list_windows", {})
wins = r.get("result") or []
if isinstance(wins, dict):
    wins = wins.get("windows") or []
wins = [w for w in wins if isinstance(w, dict)]
note = next((w for w in wins if "notepad" in str(w.get("app", "")).lower()), None)
print("  notepad present:", bool(note))
print("  window count:", len(wins))
print("  app ids:", sorted({str(w.get("app")) for w in wins}))

if note:
    print("\n--- get_window (read-only) ---")
    r = probe.call("get_window", {"id": note["id"], "app": note["app"]})
    print("  RESULT keys:", sorted(r.keys()) if isinstance(r, dict) else type(r))

    print("\n--- get_window_state (read-only) ---")
    r = probe.call(
        "get_window_state",
        {
            "window": {"app": note["app"], "id": note["id"]},
            "include_screenshot": False,
            "include_text": True,
        },
    )
    state_shape(r.get("result"))

probe.close()

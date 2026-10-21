"""Live probe: learn the exact wire shapes of codex-computer-use.exe on stdio.

Deliberately does NOT forge the ``x-oai-cua-approved-app`` metadata key. That
key is the helper's record of a real user approval, and this probe only
wants to *observe* what an approval request looks like so the MCP server can
implement the flow properly.

Read-only calls only -- no clicks, no typing. Output is redacted: window
titles and accessibility text are elided, because they can carry the name of
whatever document you have open.

Run with:  .venv\\Scripts\\python.exe scripts/probe_protocol.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _probe import Probe, show, state_shape  # noqa: E402

probe = Probe()

# --- read-only probes -----------------------------------------------------
show("list_windows", probe.call("list_windows", {}))
show("list_apps", probe.call("list_apps", {}))

wins = probe.call("list_windows", {}).get("result") or []
if isinstance(wins, dict):
    wins = wins.get("windows") or []
wins = [w for w in wins if isinstance(w, dict)]
# Print app identifiers and window counts only. Window titles can carry
# personal information (document names, page titles), so they are elided.
print("\napp ids seen:", sorted({str(w.get("app")) for w in wins}))
print("window count:", len(wins))

notepad = next(
    (w for w in wins if "notepad" in str(w.get("app", "")).lower()), None
)
print("notepad present:", bool(notepad))

if notepad:
    show(
        "get_window",
        probe.call("get_window", {"id": notepad["id"], "app": notepad["app"]}),
    )
    r = probe.call(
        "get_window_state",
        {
            "window": {"app": notepad["app"], "id": notepad["id"]},
            "include_screenshot": False,
            "include_text": True,
        },
    )
    print("\n=== get_window_state (shape only) ===")
    state_shape(r.get("result"))

# The approval-request shape is shown by scripts/probe_approval.py, which
# uses launch_app -- an action, but one that touches no document.

probe.close()

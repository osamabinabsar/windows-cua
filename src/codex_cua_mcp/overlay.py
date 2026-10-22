"""The banner the Codex helper draws while it is controlling the PC.

While the helper is acting, it puts a banner on the screen saying who is
in control and which key stops it. Both sentences are user-configurable,
in a JSON file the helper reads from
``%USERPROFILE%\\.codex\\computer-use\\config.json``::

    {
      "accentColor": "#339cff",
      "direction": "ltr",
      "locale": "en-GB",
      "strings": {
        "usingComputer": "ChatGPT is using your computer",
        "escToCancel": "Esc to cancel"
      }
    }

Nothing here patches a binary: this is the same file the helper already
reads, so changing it changes what the overlay says. The keys are the
ones the helper's own serializer uses, so the file round-trips.

One honesty rule
----------------
``escToCancel`` is a *label*. It does not make a key work. The key the
Codex helper actually watches is the physical **Escape**; this project
adds :class:`~codex_cua_mcp.takeover.TakeoverController`, which adds
**Control+Alt+Shift+Backspace** on top. Point the label at a key that
really stops the agent, or it is a promise the overlay cannot keep.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_OVERLAY_CONFIG",
    "overlay_config_path",
    "read_overlay_config",
    "write_overlay_config",
]

#: The shipped defaults, for reference. The helper's own compiled-in
#: default for ``usingComputer`` is "Codex is using your computer".
DEFAULT_OVERLAY_CONFIG: dict[str, Any] = {
    "accentColor": "#339cff",
    "direction": "ltr",
    "locale": "en-GB",
    "strings": {
        "usingComputer": "AI is controlling this computer",
        "escToCancel": "Press Ctrl+Alt+Shift+Backspace to take over",
    },
}

#: The string keys the helper understands.
_STRING_KEYS = ("usingComputer", "escToCancel")
#: The top-level keys the helper understands.
_TOP_KEYS = ("accentColor", "direction", "locale")


def overlay_config_path() -> Path:
    """Where the helper looks for its overlay configuration."""
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return home / ".codex" / "computer-use" / "config.json"


def read_overlay_config(path: Path | None = None) -> dict[str, Any]:
    """Read the overlay configuration.

    Returns an empty dict if the file does not exist, and the file's
    contents unchanged otherwise -- unknown keys are preserved so a
    round-trip through :func:`write_overlay_config` does not quietly drop
    a setting this project does not know about.
    """
    target = path or overlay_config_path()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_overlay_config(
    using_computer: str | None = None,
    esc_to_cancel: str | None = None,
    accent_color: str | None = None,
    direction: str | None = None,
    locale: str | None = None,
    path: Path | None = None,
) -> Path:
    """Update the overlay configuration, leaving untouched keys alone.

    Only the arguments you pass are changed. The file is created if it
    does not exist, and its parent directory is created with it.
    """
    target = path or overlay_config_path()
    config = read_overlay_config(target)

    strings = config.get("strings")
    if not isinstance(strings, dict):
        strings = {}
    if using_computer is not None:
        strings["usingComputer"] = using_computer
    if esc_to_cancel is not None:
        strings["escToCancel"] = esc_to_cancel
    if strings:
        config["strings"] = strings

    if accent_color is not None:
        config["accentColor"] = accent_color
    if direction is not None:
        config["direction"] = direction
    if locale is not None:
        config["locale"] = locale

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target

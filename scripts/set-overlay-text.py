"""Set the banner the Codex helper shows while it is controlling the PC.

The helper draws a banner saying who is in control and which key stops
it. Both sentences live in a JSON file the helper already reads:

    %USERPROFILE%\\.codex\\computer-use\\config.json

This rewrites the two strings in it and leaves everything else alone.
No binary is patched.

    python scripts\\set-overlay-text.py
    python scripts\\set-overlay-text.py --using "AI is controlling this computer" \\
        --hint "Press Ctrl+Alt+Shift+Backspace to take over"
    python scripts\\set-overlay-text.py --show

The change takes effect the next time the helper starts an overlay, so
restart the MCP server (or the harness session) to see it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.overlay import (  # noqa: E402
    DEFAULT_OVERLAY_CONFIG,
    overlay_config_path,
    read_overlay_config,
    write_overlay_config,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="set-overlay-text",
        description="Set the text the Codex helper shows while it controls the PC.",
    )
    parser.add_argument(
        "--using",
        dest="using_computer",
        default=None,
        help="the main banner, e.g. 'AI is controlling this computer'",
    )
    parser.add_argument(
        "--hint",
        dest="esc_to_cancel",
        default=None,
        help="the second line, naming the key that takes control back",
    )
    parser.add_argument("--accent", dest="accent_color", default=None, help="banner colour")
    parser.add_argument(
        "--direction", default=None, choices=("ltr", "rtl"), help="text direction"
    )
    parser.add_argument("--locale", default=None, help="locale for the built-in strings")
    parser.add_argument(
        "--show",
        action="store_true",
        help="print the current configuration and exit",
    )
    parser.add_argument(
        "--defaults",
        action="store_true",
        help=f"restore the shipped defaults ({json.dumps(DEFAULT_OVERLAY_CONFIG)})",
    )
    args = parser.parse_args()

    path = overlay_config_path()

    if args.show:
        current = read_overlay_config(path)
        if not current:
            print(f"no overlay config at {path}")
            print("defaults:")
            print(json.dumps(DEFAULT_OVERLAY_CONFIG, indent=2))
            return 0
        print(f"{path}:")
        print(json.dumps(current, indent=2, ensure_ascii=False))
        return 0

    if args.defaults:
        using = DEFAULT_OVERLAY_CONFIG["strings"]["usingComputer"]
        hint = DEFAULT_OVERLAY_CONFIG["strings"]["escToCancel"]
    else:
        using = args.using_computer
        hint = args.esc_to_cancel

    if using is None and hint is None and not args.defaults:
        parser.error("nothing to do: pass --using and/or --hint, or --show")

    written = write_overlay_config(
        using_computer=using,
        esc_to_cancel=hint,
        accent_color=args.accent_color,
        direction=args.direction,
        locale=args.locale,
        path=path,
    )
    print(f"wrote {written}:")
    print(json.dumps(read_overlay_config(written), indent=2, ensure_ascii=False))
    print(
        "\nThe helper reads this when it starts an overlay, so restart the "
        "MCP server to see the new text."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

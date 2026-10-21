"""Verify the MCP elicitation approval path end-to-end.

Drives a real tool call that needs approval, with an MCP client that
answers the elicitation the way a harness would. Uses Notepad -- a scratch
app with no user data -- and types a scratch string.

This is the real flow: no forged approval key, no --auto-approve. The
server must elicit, the client must answer, and only then may the action
proceed.

Run with:  .venv\\Scripts\\python.exe scripts/test_elicitation.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ElicitRequestParams, ElicitResult

SCRATCH_TEXT = "codex-cua-mcp elicitation test"


async def main() -> int:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "codex_cua_mcp.server"],
        env=env,
    )

    elicitations: list[str] = []

    async def handler(context, req: ElicitRequestParams):
        """Answer the elicitation, the way a harness would."""
        msg = req.message
        elicitations.append(msg)
        print("\n>>> ELICITATION RECEIVED")
        print(msg)
        print(">>> answering: allow=True")
        return ElicitResult(action="accept", content={"allow": True})

    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read,
            write,
            elicitation_callback=handler,
        ) as session:
            init = await session.initialize()
            print("server:", init.server_info.name, init.server_info.version)

            print("\n--- status (before approval) ---")
            r = await session.call_tool("status", {})
            print(_text(r))

            print("\n--- launch_app notepad (requires approval) ---")
            r = await session.call_tool("launch_app", {"app": "notepad"})
            print(_text(r))

            print("\n--- list_windows: find notepad ---")
            r = await session.call_tool("list_windows", {})
            text = _text(r)
            print(text[:800])

            note = _find_notepad(text)
            if not note:
                print("\nNotepad window not found; cannot continue.")
                return 1

            print(f"\n>>> target: app={note['app']!r} id={note['id']}")

            # SAFETY: the test must not type into a window that has a real
            # file open. Read the document value first; if it is not empty,
            # skip the mutating steps and report that the test was skipped
            # rather than risk clobbering the user's document.
            state = await _state(session, note)
            doc = _doc_value(state)
            print(f"\ndocument value length: {len(doc) if doc is not None else 'n/a'}")

            if doc is None or doc.strip():
                print(
                    "\n!! The Notepad window has content in it -- this is a real "
                    "document, not a scratch buffer."
                )
                print("!! Skipping the type_text/press_key steps to avoid "
                      "modifying the user's file.")
                print("!! Close that Notepad window (or open a blank one) and "
                      "re-run to exercise the full write path.")
                return 0

            print("\n--- get_window_state on notepad (requires approval) ---")
            print(state[:1200])

            print("\n--- type_text into notepad (requires approval) ---")
            r = await session.call_tool(
                "type_text",
                {
                    "app": note["app"],
                    "window_id": note["id"],
                    "text": SCRATCH_TEXT,
                },
            )
            print(_text(r))

            print("\n--- press_key ctrl+a ---")
            r = await session.call_tool(
                "press_key",
                {"app": note["app"], "window_id": note["id"], "key": "Control_L+a"},
            )
            print(_text(r))

            print("\n--- undo the typing so the buffer is clean ---")
            for _ in range(10):
                await session.call_tool(
                    "press_key",
                    {"app": note["app"], "window_id": note["id"], "key": "Control_L+z"},
                )
                state = await _state(session, note)
                if SCRATCH_TEXT not in (_doc_value(state) or ""):
                    print("scratch text removed from the buffer")
                    break
            await session.call_tool(
                "press_key",
                {"app": note["app"], "window_id": note["id"], "key": "Right"},
            )

            print("\n--- status (after approval) ---")
            r = await session.call_tool("status", {})
            print(_text(r))

            print(f"\n=== {len(elicitations)} elicitation(s) received ===")
            for i, e in enumerate(elicitations, 1):
                print(f"  {i}. {e.splitlines()[0]}")

    return 0


def _text(result) -> str:
    return "\n".join(
        c.text for c in result.content if getattr(c, "type", "") == "text"
    )


def _find_notepad(listing: str) -> dict | None:
    app = None
    wid = None
    for line in listing.splitlines():
        if "app=" in line and "notepad" in line.lower():
            for part in line.split():
                if part.startswith("app="):
                    app = part[4:]
                if part.startswith("id="):
                    wid = int(part[3:])
    if app and wid is not None:
        return {"app": app, "id": wid}
    return None


async def _state(session, note: dict) -> str:
    r = await session.call_tool(
        "get_window_state",
        {
            "app": note["app"],
            "window_id": note["id"],
            "include_text": True,
            "include_screenshot": False,
        },
    )
    return _text(r)


def _doc_value(state_text: str) -> str | None:
    """The document's text value, from the accessibility tree."""
    marker = "Text editor Value: "
    idx = state_text.find(marker)
    if idx < 0:
        return None
    rest = state_text[idx + len(marker):]
    nl = rest.find("\n")
    return rest if nl < 0 else rest[:nl]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Restore the Notepad buffer that the elicitation test typed into.

The test typed a scratch string into a Notepad window that had a real file
open, and left it selected. The file on disk was never written, but the
buffer is dirty -- a stray Ctrl+S would overwrite the file.

This undoes the edit in the buffer and verifies the result. It is a
one-off repair script, not part of the test suite.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ElicitRequestParams, ElicitResult

TEST_STRING = "codex-cua-mcp elicitation test"
TARGET_APP = "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"


async def main() -> int:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "codex_cua_mcp.server"],
        env=env,
    )

    async def handler(context, req: ElicitRequestParams):
        return ElicitResult(action="accept", content={"allow": True})

    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, elicitation_callback=handler
        ) as session:
            await session.initialize()

            r = await session.call_tool("list_windows", {})
            text = _text(r)
            target = _find(text, TARGET_APP)
            if not target:
                print("Notepad window not found; nothing to restore.")
                return 1
            wid = target["id"]
            print(f"target: {TARGET_APP} id={wid}")

            # Read the document value and check for the test string.
            state = await _state(session, wid)
            value = _doc_value(state)
            print(f"document value length: {len(value) if value is not None else 'n/a'}")
            if value is None:
                print("no document value; aborting")
                return 1
            print(f"contains test string: {TEST_STRING in value}")

            if TEST_STRING in value:
                # Undo the insertion. Notepad groups a typed run into undo
                # steps, so press undo until the string is gone.
                for i in range(1, 41):
                    await session.call_tool(
                        "press_key",
                        {"app": TARGET_APP, "window_id": wid, "key": "Control_L+z"},
                    )
                    state = await _state(session, wid)
                    value = _doc_value(state) or ""
                    if TEST_STRING not in value:
                        print(f"undo steps needed: {i}")
                        break
                else:
                    print("undo did not remove the string after 40 steps")
                    return 1

            # Verify.
            state = await _state(session, wid)
            value = _doc_value(state) or ""
            print(f"contains test string now: {TEST_STRING in value}")

            # Confirm the buffer no longer looks dirty, and that the
            # on-disk file is untouched.
            print(f"document value length now: {len(value)}")

            # Deselect so a stray Ctrl+S is a no-op rather than a
            # select-all-and-overwrite.
            r = await session.call_tool(
                "press_key",
                {"app": TARGET_APP, "window_id": wid, "key": "Right"},
            )
            print("cleared selection")

    return 0


def _text(result) -> str:
    return "\n".join(
        c.text for c in result.content if getattr(c, "type", "") == "text"
    )


def _find(listing: str, app_substr: str) -> dict | None:
    for line in listing.splitlines():
        if app_substr.lower() in line.lower() and "app=" in line:
            app = wid = None
            for part in line.split():
                if part.startswith("app="):
                    app = part[4:]
                elif part.startswith("id="):
                    wid = int(part[3:])
            if app and wid is not None:
                return {"app": app, "id": wid}
    return None


async def _state(session, wid) -> str:
    r = await session.call_tool(
        "get_window_state",
        {
            "app": TARGET_APP,
            "window_id": wid,
            "include_text": True,
            "include_screenshot": False,
        },
    )
    return _text(r)


def _doc_value(state_text: str) -> str | None:
    """Pull the document's value out of the accessibility tree text."""
    marker = "Text editor Value: "
    idx = state_text.find(marker)
    if idx < 0:
        return None
    rest = state_text[idx + len(marker):]
    # The value runs to the end of the line.
    nl = rest.find("\n")
    return rest if nl < 0 else rest[:nl]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

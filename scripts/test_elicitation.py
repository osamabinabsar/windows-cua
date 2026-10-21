"""Verify the MCP elicitation approval path end-to-end.

Drives a real tool call that needs approval, with an MCP client that
answers the elicitation the way a harness would. This is the real flow: no
forged approval key, no --auto-approve. The server must elicit, the client
must answer, and only then may the action proceed.

The target is **Character Map** (charmap.exe), not Notepad. Notepad on
Windows 11 is single-instance with tabs, so a "new" Notepad can share a
window with whatever document you already had open -- which is how an
earlier version of this test typed into a real file. Character Map is a
single-purpose app that can hold no user document at all, so there is
nothing to damage. It also has a rich accessibility tree (an edit field, a
grid, buttons), which makes it a good exercise of the write path.

Run with:  .venv\\Scripts\\python.exe scripts\\test_elicitation.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ElicitRequestParams, ElicitResult

TARGET_APP = "charmap.exe"
SCRATCH_TEXT = "codex-cua-mcp"


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

            print(f"\n--- launch_app {TARGET_APP} (requires approval) ---")
            r = await session.call_tool("launch_app", {"app": TARGET_APP})
            print(_text(r))

            print("\n--- list_windows: find the target ---")
            r = await session.call_tool("list_windows", {})
            text = _text(r)
            # Print only the app id, never a window title.
            for line in text.splitlines():
                if "app=" in line:
                    print("  ", line.split("title=")[0].rstrip())

            target = _find(text, "charmap")
            if not target:
                print("\nTarget window not found; cannot continue.")
                return 1

            print(f"\n>>> target: app={target['app']!r} id={target['id']}")

            print("\n--- get_window_state (requires approval) ---")
            state = await _state(session, target)
            roles = _roles(state)
            print(f"  elements: {len(roles)}")
            print("  ", " -> ".join(f"{i}:{r}" for i, r in roles[:14]))

            # Find the "Go to Unicode" edit field, which is Character Map's
            # only text input. Typing into it changes a character code
            # number, not any file.
            edit_index = _first_edit(roles)
            if edit_index is None:
                print("\n  no edit field found; skipping the type test")
                return 1
            print(f"  edit field at element {edit_index}")

            print("\n--- click_element on the edit field ---")
            r = await session.call_tool(
                "click_element",
                {
                    "app": target["app"],
                    "window_id": target["id"],
                    "element_index": edit_index,
                },
            )
            print(" ", _text(r))

            print("\n--- type_text into the edit field (requires approval) ---")
            # element_index is passed so the server clicks the field before
            # typing. Without it the keystrokes go to whatever holds focus,
            # and with nothing focused they are silently discarded.
            r = await session.call_tool(
                "type_text",
                {
                    "app": target["app"],
                    "window_id": target["id"],
                    "text": SCRATCH_TEXT,
                    "element_index": edit_index,
                },
            )
            print(" ", _text(r))

            print("\n--- press_key ctrl+a ---")
            r = await session.call_tool(
                "press_key",
                {
                    "app": target["app"],
                    "window_id": target["id"],
                    "key": "Control_L+a",
                    "element_index": edit_index,
                },
            )
            print(" ", _text(r))

            print("\n--- verify the field changed ---")
            state = await _state(session, target)
            value = _edit_value(state)
            # Character Map's field is numeric ("Characters to copy"), so it
            # keeps only the leading digits of what was typed. Either way the
            # field must no longer be empty.
            print(f"  edit value length: {len(value) if value is not None else 'n/a'}")
            print(f"  field is non-empty: {bool((value or '').strip())}")

            print("\n--- clear the field (restores it) ---")
            # Re-read the tree first: the element index from the earlier
            # observation may have shifted.
            state = await _state(session, target)
            current_edit = _first_edit(_roles(state))
            print(f"  edit field is now element {current_edit}")

            # set_value needs no focus, and an empty value clears the field.
            await session.call_tool(
                "set_value",
                {
                    "app": target["app"],
                    "window_id": target["id"],
                    "element_index": current_edit,
                    "value": "",
                },
            )
            state = await _state(session, target)
            value = _edit_value(state)
            print(f"  field is empty again: {not (value or '').strip()}")
            # Show the field's *shape*, not its content.
            print(f"  field now looks like: {repr(_shape_of_value(value))}")

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


def _find(listing: str, app_substr: str) -> dict | None:
    for line in listing.splitlines():
        if app_substr in line.lower() and "app=" in line:
            app = wid = None
            for part in line.split():
                if part.startswith("app="):
                    app = part[4:]
                elif part.startswith("id="):
                    wid = int(part[3:])
            if app and wid is not None:
                return {"app": app, "id": wid}
    return None


def _roles(state_text: str) -> list[tuple[int, str]]:
    """Element index -> role, parsed from the accessibility tree."""
    import re

    return [
        (int(i), role)
        for i, role in re.findall(r"^\s*(\d+)\s+([a-z_]+)", state_text, re.MULTILINE)
    ]


def _first_edit(roles: list[tuple[int, str]]) -> int | None:
    for index, role in roles:
        if role == "edit":
            return index
    return None


def _edit_value(state_text: str) -> str | None:
    """The edit field's value, from the accessibility tree.

    A tree line looks like::

        3 edit (settable, string) Characters to copy : Value: 0041  ID: 104

    The value sits between ``Value:`` and the trailing ``ID:``, so the
    parse has to stop there -- an earlier version ran to end-of-line and
    read the element id as the field's contents, which made an empty
    field look like it held seven characters.
    """
    import re

    m = re.search(r"^\s*\d+\s+edit\b.*$", state_text, re.MULTILINE)
    if not m:
        return None
    line = re.sub(r"\s*\bID:\s*\S+\s*$", "", m.group(0))
    vm = re.search(r"Value:\s*(.*)$", line)
    return vm.group(1).strip() if vm else ""


async def _state(session, target: dict) -> str:
    r = await session.call_tool(
        "get_window_state",
        {
            "app": target["app"],
            "window_id": target["id"],
            "include_text": True,
            "include_screenshot": False,
        },
    )
    return _text(r)


def _shape_of_value(value: str | None) -> str:
    """Describe a field's value without printing it: <digits:N> or <text:N>."""
    if value is None:
        return "<none>"
    stripped = value.strip()
    if not stripped:
        return "<empty>"
    if stripped.isdigit():
        return f"<digits:{len(stripped)}>"
    return f"<text:{len(stripped)}>"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

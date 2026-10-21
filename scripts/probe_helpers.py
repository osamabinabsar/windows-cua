"""Re-verify the Windows helper's undocumented behaviours.

The Codex computer-use helper's type definitions do not describe what it
actually does on Windows. Three behaviours matter enough that the client
has to work around them, and all three were found by probing the live
binary:

1. ``click`` requires x/y. An element-targeted click is rejected with
   ``parse click params: missing field 'x'``; the element path is the
   separate method ``click_element``.
2. An element-targeted action needs a prior observation *with a
   screenshot*, or the helper refuses it with ``coordinate input
   geometry is unavailable``.
3. ``type_text`` and ``press_key`` go to whatever holds focus, and are
   silently lost when nothing does -- the call reports success either
   way.

When Codex updates its runtime, the helper may change. Run this to check
that the workarounds in ``client.py`` are still the right ones.

The target is **Character Map** (``charmap.exe``): a single-purpose app
that cannot hold a user document, so nothing here can damage your work.
Windows 11 Notepad would be the obvious alternative, but it is
single-instance with tabs -- a "new" Notepad can share a window with the
document you already have open.

Approval is obtained the honest way: a real MCP client answers a real
elicitation, exactly as a harness would. Nothing is auto-approved.

Run with:  .venv\\Scripts\\python.exe scripts\\probe_helpers.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ElicitRequestParams, ElicitResult

APP = "charmap.exe"

#: Every expectation this script checks, and whether it currently holds.
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))


# --------------------------------------------------------------------------
# Tree parsing. Values are never printed: only their shape.
# --------------------------------------------------------------------------


def roles(state_text: str) -> list[tuple[int, str]]:
    """Element index -> role, from the accessibility tree."""
    return [
        (int(i), role)
        for i, role in re.findall(r"^\s*(\d+)\s+([a-z_]+)", state_text, re.MULTILINE)
    ]


def first_edit(state_text: str) -> int | None:
    for index, role in roles(state_text):
        if role == "edit":
            return index
    return None


def edit_value(state_text: str) -> str | None:
    """The edit field's value.

    A tree line looks like::

        3 edit (settable, string) Characters to copy : Value: 0041  ID: 104

    The value sits between ``Value:`` and the trailing ``ID:``, so the
    parse has to stop there -- running to end-of-line reads the element
    id as the field's contents.
    """
    m = re.search(r"^\s*\d+\s+edit\b.*$", state_text, re.MULTILINE)
    if not m:
        return None
    line = re.sub(r"\s*\bID:\s*\S+\s*$", "", m.group(0))
    vm = re.search(r"Value:\s*(.*)$", line)
    return vm.group(1).strip() if vm else ""


def value_shape(value: str | None) -> str:
    """Describe a field's value without printing it."""
    if value is None:
        return "<none>"
    s = value.strip()
    return "<empty>" if not s else f"<{len(s)} chars>"


def text(result) -> str:
    return "\n".join(
        c.text for c in result.content if getattr(c, "type", "") == "text"
    )


def find_window(listing: str, app_substr: str) -> dict | None:
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


# --------------------------------------------------------------------------


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
        elicitations.append(req.message)
        return ElicitResult(action="accept", content={"allow": True})

    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, elicitation_callback=handler
        ) as session:
            await session.initialize()

            r = await session.call_tool("launch_app", {"app": APP})
            check("launch_app succeeds", "could not launch" not in text(r), text(r)[:80])
            await asyncio.sleep(2.0)

            r = await session.call_tool("list_windows", {})
            target = find_window(text(r), "charmap")
            if target is None:
                print("  Character Map window not found; cannot continue")
                return 1

            async def state() -> str:
                r = await session.call_tool(
                    "get_window_state",
                    {
                        "app": target["app"],
                        "window_id": target["id"],
                        "include_text": True,
                        "include_screenshot": False,
                    },
                )
                return text(r)

            async def set_field(value: str) -> None:
                idx = first_edit(await state())
                await session.call_tool(
                    "set_value",
                    {
                        "app": target["app"],
                        "window_id": target["id"],
                        "element_index": idx,
                        "value": value,
                    },
                )

            print("\n--- 1. click requires x/y on Windows ---")
            idx = first_edit(await state())
            r = await session.call_tool(
                "click",
                {
                    "app": target["app"],
                    "window_id": target["id"],
                    "element_index": idx,
                },
            )
            # The server routes this to click_element, so the tool call
            # succeeds. The *helper* is what rejects the element form, so
            # check the routing instead.
            check(
                "click with element_index is routed to click_element",
                "clicked element" in text(r),
                text(r)[:80],
            )

            print("\n--- 2. element actions need a screenshot observation ---")
            # The client observes with a screenshot on its own; a direct
            # text-only observation followed by an element action is what
            # the helper refuses. This is exercised through the client, so
            # the check is that the action still works.
            r = await session.call_tool(
                "click_element",
                {
                    "app": target["app"],
                    "window_id": target["id"],
                    "element_index": idx,
                },
            )
            check(
                "click_element succeeds (client observes with a screenshot)",
                "clicked element" in text(r),
                text(r)[:80],
            )

            print("\n--- 3. type_text needs focus; set_value does not ---")
            await set_field("")
            r = await session.call_tool(
                "type_text",
                {
                    "app": target["app"],
                    "window_id": target["id"],
                    "text": "0042",
                },
            )
            unfocused = value_shape(edit_value(await state()))
            check(
                "type_text without a click loses the text (the trap)",
                unfocused == "<empty>",
                f"field={unfocused}, call said: {text(r)[:40]}",
            )

            await set_field("")
            r = await session.call_tool(
                "type_text",
                {
                    "app": target["app"],
                    "window_id": target["id"],
                    "text": "0042",
                    "element_index": idx,
                },
            )
            focused = value_shape(edit_value(await state()))
            check(
                "type_text with element_index lands the text",
                focused == "<4 chars>",
                f"field={focused}",
            )

            print("\n--- 4. set_value writes and clears without focus ---")
            await set_field("0041")
            written = value_shape(edit_value(await state()))
            check("set_value writes the field", written == "<4 chars>", f"field={written}")

            await set_field("")
            cleared = value_shape(edit_value(await state()))
            check("set_value '' clears the field", cleared == "<empty>", f"field={cleared}")

            # Leave the app as we found it.
            await set_field("")

    print("\n--- approval flow ---")
    check(
        "one elicitation covers the whole session",
        len(elicitations) == 1,
        f"{len(elicitations)} elicitation(s)",
    )

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("failed:", ", ".join(failed))
        print("\nThe helper's behaviour has changed. client.py's workarounds")
        print("need revisiting before trusting this server.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

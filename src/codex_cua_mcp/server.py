"""MCP server exposing collaborative Windows computer use.

Serves the `sky` Windows API over MCP stdio, backed by the *local* Codex
computer-use helper. No cloud, no account, no usage limit: the model you use
is whatever your harness already talks to, and the desktop automation is a
local process on your own machine.

The defining property is **collaboration**. Every action names a target
window and is delivered to that window without raising it, so you can keep
using your mouse and keyboard while the agent works. ``activate_window`` is
the single explicit escape hatch that takes focus.

Approval
--------
The helper gates every app behind a per-app consent prompt. This server
turns that prompt into an MCP *elicitation*, so the harness asks you and
your answer decides. The approved-app key is written to the wire only after
you accept -- there is no path that bypasses this.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field

from .client import CuaClient, WindowRef, format_windows
from .transport import (
    ApprovalRequired,
    HelperError,
    HelperNotFoundError,
    HelperTransport,
    find_codex_cli,
    find_helper,
)

__all__ = ["build_server", "main"]

SERVER_NAME = "codex-cua-mcp"
SERVER_VERSION = "0.1.0"

INSTRUCTIONS = """\
Collaborative Windows computer use, powered by the local Codex computer-use
helper. Everything runs on this machine -- no cloud, no account, no usage
limit.

HOW IT STAYS COLLABORATIVE
Every action names a target window and is delivered to that window WITHOUT
raising it. You and the agent can use the PC at the same time. The one
exception is `activate_window`, which explicitly takes focus.

WORKFLOW
1. `list_apps` / `list_windows` -- find the target. Never guess a window.
2. `get_window_state` -- read the accessibility tree to get element indexes.
3. Act with exactly one action.
4. Re-read state to verify. Element indexes are only valid for the
   observation that produced them; re-observe after anything changes.

Element indexes come from `get_window_state`'s accessibility tree. Pass one
as `element_index` instead of coordinates when the target is a known
control.

WHERE THE TEXT GOES
`type_text` and `press_key` deliver keystrokes to the control that holds
focus in the target window. If nothing holds focus there, the characters
are lost and the call still reports success -- always pass
`element_index` so the control is clicked first. `set_value` needs no
focus at all, so prefer it for filling a field; an empty value clears it.

APPROVAL
The first action against an app asks you to approve it. The request goes to
the client as an elicitation, so you see it and decide. Approvals persist
for the session.
"""


# --------------------------------------------------------------------------
# Elicitation schemas
# --------------------------------------------------------------------------


class ApprovalDecision(BaseModel):
    """The user's answer to an app-approval request."""

    allow: bool = Field(
        default=False,
        description=(
            "True to let the agent use this app now, False to deny it. "
            "Denial blocks the current action only."
        ),
    )
    reason: str = Field(
        default="",
        description="Optional note back to the agent about your decision.",
    )


# --------------------------------------------------------------------------
# Helpers for tool implementations
# --------------------------------------------------------------------------


def _window_ref(app: str, window_id: int, title: str | None = None) -> WindowRef:
    return WindowRef(app=app, id=int(window_id), title=title)


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _state_summary(state: dict[str, Any]) -> str:
    """A compact, agent-readable digest of a window state."""
    acc = state.get("accessibility")
    tree = ""
    focus = ""
    if isinstance(acc, dict):
        tree = acc.get("tree") or ""
        focus = acc.get("focused_element") or ""
    shots = state.get("screenshots") or []
    lines = []
    if tree:
        lines.append(tree if isinstance(tree, str) else json.dumps(tree))
    elif focus:
        lines.append(f"focused: {focus}")
    else:
        lines.append("(no accessibility text; try include_screenshot=True)")
    if shots:
        ids = [s.get("id") for s in shots if isinstance(s, dict) and s.get("id")]
        lines.append(f"[screenshots: {len(shots)} id={ids}]")
    return "\n".join(lines)


class _CuaApi:
    """A uniform async surface over whichever backend is in use.

    The two backends differ in small ways: the Codex client is
    synchronous for its read-only calls and takes an approval hook,
    while the ``trycua/cua`` client is async throughout and has no
    approval concept. This adapter hides that, so the sixteen MCP tools
    are written once and work against either driver.
    """

    def __init__(self, client: Any) -> None:
        self._c = client

    async def list_apps(self) -> list[dict[str, Any]]:
        result = self._c.list_apps()
        if asyncio.iscoroutine(result):
            result = await result
        return list(result)

    async def list_windows(self) -> list[WindowRef]:
        result = self._c.list_windows()
        if asyncio.iscoroutine(result):
            result = await result
        return list(result)

    async def get_window(self, window_id: int, app: str) -> WindowRef:
        result = self._c.get_window(window_id, app)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def get_window_state(
        self, window: WindowRef, include_screenshot: bool, include_text: bool
    ) -> dict[str, Any]:
        return await self._c.get_window_state(
            window, include_screenshot=include_screenshot, include_text=include_text
        )

    async def accessibility_tree(self, window: WindowRef) -> str:
        return await self._c.accessibility_tree(window)

    async def launch_app(self, app: str) -> None:
        await self._c.launch_app(app)

    async def activate_window(self, window: WindowRef) -> None:
        await self._c.activate_window(window)

    async def click(
        self,
        window: WindowRef,
        x: int | None = None,
        y: int | None = None,
        element_index: int | None = None,
        mouse_button: str = "left",
        click_count: int = 1,
        screenshot_id: str | None = None,
    ) -> None:
        await self._c.click(
            window,
            x=x,
            y=y,
            element_index=element_index,
            mouse_button=mouse_button,
            click_count=click_count,
            screenshot_id=screenshot_id,
        )

    async def click_element(
        self,
        window: WindowRef,
        element_index: int,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> None:
        await self._c.click_element(
            window,
            element_index=element_index,
            mouse_button=mouse_button,
            click_count=click_count,
        )

    async def type_text(
        self, window: WindowRef, text: str, element_index: int | None = None
    ) -> None:
        await self._c.type_text(window, text, element_index=element_index)

    async def press_key(
        self, window: WindowRef, key: str, element_index: int | None = None
    ) -> None:
        await self._c.press_key(window, key, element_index=element_index)

    async def scroll(
        self,
        window: WindowRef,
        x: int,
        y: int,
        scroll_x: int = 0,
        scroll_y: int = 0,
        screenshot_id: str | None = None,
    ) -> None:
        await self._c.scroll(
            window,
            x,
            y,
            scroll_x=scroll_x,
            scroll_y=scroll_y,
            screenshot_id=screenshot_id,
        )

    async def drag(
        self,
        window: WindowRef,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        screenshot_id: str | None = None,
    ) -> None:
        await self._c.drag(
            window, from_x, from_y, to_x, to_y, screenshot_id=screenshot_id
        )

    async def set_value(self, window: WindowRef, element_index: int, value: str) -> None:
        await self._c.set_value(window, element_index, value)

    async def perform_secondary_action(
        self, window: WindowRef, element_index: int, action: str
    ) -> None:
        await self._c.perform_secondary_action(window, element_index, action)


# --------------------------------------------------------------------------
# Server construction
# --------------------------------------------------------------------------


def build_server(
    helper_path: str | None = None,
    codex_cli_path: str | None = None,
    auto_approve: bool = False,
    backend: str = "codex",
    driver_path: str | None = None,
) -> MCPServer:
    """Build the MCP server.

    Parameters
    ----------
    helper_path:
        Explicit path to ``codex-computer-use.exe``. Auto-detected if omitted.
    codex_cli_path:
        Explicit path to the ``codex`` CLI. Auto-detected if omitted.
    auto_approve:
        When True, approve app requests without asking the user. Off by
        default: the whole point is that you stay in control.
    backend:
        Which driver to use: ``"codex"`` (the Codex helper, the default)
        or ``"cua"`` (the MIT-licensed ``trycua/cua`` driver). The two
        present the same tool surface, so a harness config does not
        change when you switch.
    driver_path:
        Explicit path to ``cua-driver.exe``. Auto-detected if omitted.
    """
    if backend == "cua":
        return _build_cua_server(driver_path=driver_path)

    resolved_helper = find_helper(helper_path)
    resolved_cli = find_codex_cli(codex_cli_path)

    transport = HelperTransport(
        helper_path=resolved_helper,
        codex_cli_path=resolved_cli,
    )

    server = MCPServer(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS,
    )

    # The approval hook needs the Context of the running tool call, so each
    # call gets its own client bound to that call's hook. The transport --
    # and therefore the helper process and the approved-app set -- is shared.
    base_client = CuaClient(transport, on_approval=None)

    def _client_for(ctx: Context) -> CuaClient:
        """A client whose approval hook elicits this call's user."""

        async def hook(request: dict[str, Any]) -> bool:
            if auto_approve:
                return True
            app = str(request.get("app") or "")
            display = str(request.get("displayName") or app)
            risk = request.get("riskLevel")
            risk_note = f" (risk: {risk})" if risk else ""
            result = await ctx.elicit(
                message=(
                    f"Allow Computer Use to control '{display}'?{risk_note}\n"
                    f"App id: {app}\n\n"
                    "The agent will be able to read this window and send input to it "
                    "without bringing it to the foreground."
                ),
                schema=ApprovalDecision,
            )
            action = getattr(result, "action", None)
            if action != "accept":
                return False
            data = getattr(result, "data", None)
            if data is None:
                return False
            return bool(getattr(data, "allow", False))

        return base_client.with_approval_hook(hook)

    # -- discovery ---------------------------------------------------------

    @server.tool(
        title="List apps",
        description=(
            "List installed apps and the windows each one currently owns. "
            "Read-only; no approval needed. Start here to find a target."
        ),
    )
    async def list_apps() -> str:
        apps = await asyncio.to_thread(base_client.list_apps)
        lines = []
        for a in apps:
            flag = "running" if a["isRunning"] else "idle"
            lines.append(f"- {a['displayName']}  [id={a['id']}]  ({flag})")
            for w in a["windows"]:
                lines.append(f"    window id={w['id']} title={w.get('title')!r}")
        return "\n".join(lines) if lines else "(no apps found)"

    @server.tool(
        title="List windows",
        description=(
            "List every open, targetable window with its app id, window id, "
            "and title. Read-only; no approval needed."
        ),
    )
    async def list_windows() -> str:
        return format_windows(await asyncio.to_thread(base_client.list_windows))

    @server.tool(
        title="Get window",
        description=(
            "Rehydrate an open window by id. Use after a lost window binding "
            "or a stale handle. Read-only."
        ),
    )
    async def get_window(window_id: int, app: str) -> str:
        ref = await asyncio.to_thread(base_client.get_window, window_id, app)
        return f"id={ref.id} app={ref.app} title={ref.title!r}"

    # -- observation -------------------------------------------------------

    @server.tool(
        title="Read window state",
        description=(
            "Read a window's accessibility tree (with element indexes) and/or "
            "a screenshot. Element indexes are only valid for the observation "
            "that produced them. Requires approval for the app the first time."
        ),
    )
    async def get_window_state(
        ctx: Context,
        app: str,
        window_id: int,
        include_text: bool = True,
        include_screenshot: bool = False,
    ) -> str:
        ref = _window_ref(app, window_id)
        client = _client_for(ctx)
        try:
            state = await client.get_window_state(
                ref,
                include_screenshot=include_screenshot,
                include_text=include_text,
            )
        except HelperError as exc:
            return f"could not read window state: {_err(exc)}"
        return _state_summary(state)

    @server.tool(
        title="Read accessibility tree",
        description=(
            "The formatted accessibility tree for a window, as plain text "
            "with element indexes. Cheaper than a full window state read."
        ),
    )
    async def read_accessibility_tree(ctx: Context, app: str, window_id: int) -> str:
        ref = _window_ref(app, window_id)
        client = _client_for(ctx)
        try:
            tree = await client.accessibility_tree(ref)
        except HelperError as exc:
            return f"could not read accessibility tree: {_err(exc)}"
        return tree or "(empty accessibility tree)"

    # -- actions -----------------------------------------------------------

    @server.tool(
        title="Launch app",
        description=(
            "Launch an app by its id from `list_apps`, or by an explicit "
            ".exe path. Requires approval for the app."
        ),
    )
    async def launch_app(ctx: Context, app: str) -> str:
        client = _client_for(ctx)
        try:
            await client.launch_app(app)
        except HelperError as exc:
            return f"could not launch {app!r}: {_err(exc)}"
        return f"launched {app!r}; call list_apps or list_windows to see its window"

    @server.tool(
        title="Click",
        description=(
            "Click at window-relative coordinates in a target window. The "
            "window is NOT brought to the foreground. To click an element "
            "you have an index for, use `click_element` instead -- it "
            "actuates the control directly and needs no coordinates."
        ),
    )
    async def click(
        ctx: Context,
        app: str,
        window_id: int,
        element_index: int | None = None,
        x: int | None = None,
        y: int | None = None,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> str:
        ref = _window_ref(app, window_id)
        client = _client_for(ctx)
        try:
            await client.click(
                ref,
                x=x,
                y=y,
                element_index=element_index,
                mouse_button=mouse_button,
                click_count=click_count,
            )
        except HelperError as exc:
            return f"click failed: {_err(exc)}"
        target = f"element {element_index}" if element_index is not None else f"({x}, {y})"
        return f"clicked {target} in window {window_id}"

    @server.tool(
        title="Click element",
        description=(
            "Click an element by its index in the accessibility tree, "
            "actuated through UI Automation. Prefer this over `click` when "
            "you have an element index: the Windows helper's `click` method "
            "needs window-relative coordinates, while this one needs "
            "neither. The window is NOT brought to the foreground."
        ),
    )
    async def click_element(
        ctx: Context,
        app: str,
        window_id: int,
        element_index: int,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> str:
        ref = _window_ref(app, window_id)
        client = _client_for(ctx)
        try:
            await client.click_element(
                ref,
                element_index=element_index,
                mouse_button=mouse_button,
                click_count=click_count,
            )
        except HelperError as exc:
            return f"click_element failed: {_err(exc)}"
        return f"clicked element {element_index} in window {window_id}"

    @server.tool(
        title="Type text",
        description=(
            "Type literal text into a target window. Pass `element_index` "
            "to say which control the text belongs in: the element is "
            "clicked first, then the text is typed. Without it the text "
            "goes to whatever currently holds focus, and if nothing does "
            "the characters are silently lost -- the call still reports "
            "success. Use press_key for Enter, Tab, arrows, and shortcuts."
        ),
    )
    async def type_text(
        ctx: Context,
        app: str,
        window_id: int,
        text: str,
        element_index: int | None = None,
    ) -> str:
        ref = _window_ref(app, window_id)
        client = _client_for(ctx)
        try:
            await client.type_text(ref, text, element_index=element_index)
        except HelperError as exc:
            return f"type failed: {_err(exc)}"
        where = f" element {element_index}" if element_index is not None else ""
        return f"typed {len(text)} chars into{where or ''} window {window_id}"

    @server.tool(
        title="Press key",
        description=(
            "Press a key or chord in the target window, using X keysym-style "
            "names: 'Return', 'Tab', 'Escape', 'Control_L+a', "
            "'Control_L+Shift_L+period'. Pass `element_index` to click that "
            "element first, so the chord lands on it."
        ),
    )
    async def press_key(
        ctx: Context,
        app: str,
        window_id: int,
        key: str,
        element_index: int | None = None,
    ) -> str:
        ref = _window_ref(app, window_id)
        client = _client_for(ctx)
        try:
            await client.press_key(ref, key, element_index=element_index)
        except HelperError as exc:
            return f"press_key failed: {_err(exc)}"
        return f"pressed {key!r} in window {window_id}"

    @server.tool(
        title="Scroll",
        description=(
            "Scroll from a window-relative coordinate. Negative scroll_y "
            "scrolls up, positive scrolls down; negative scroll_x scrolls "
            "left, positive right."
        ),
    )
    async def scroll(
        ctx: Context,
        app: str,
        window_id: int,
        x: int,
        y: int,
        scroll_x: int = 0,
        scroll_y: int = 0,
    ) -> str:
        ref = _window_ref(app, window_id)
        client = _client_for(ctx)
        try:
            await client.scroll(ref, x, y, scroll_x, scroll_y)
        except HelperError as exc:
            return f"scroll failed: {_err(exc)}"
        return f"scrolled ({scroll_x}, {scroll_y}) in window {window_id}"

    @server.tool(
        title="Drag",
        description=(
            "Drag from one window-relative point to another. Use for "
            "sliders, drawing, and canvas work."
        ),
    )
    async def drag(
        ctx: Context,
        app: str,
        window_id: int,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
    ) -> str:
        ref = _window_ref(app, window_id)
        client = _client_for(ctx)
        try:
            await client.drag(ref, from_x, from_y, to_x, to_y)
        except HelperError as exc:
            return f"drag failed: {_err(exc)}"
        return f"dragged ({from_x},{from_y}) -> ({to_x},{to_y}) in window {window_id}"

    @server.tool(
        title="Set value",
        description=(
            "Replace the value of an editable element, addressed by the "
            "element_index from a window state read. Works without focus, "
            "and an empty value clears the field -- so this is the reliable "
            "way to type into a control that type_text cannot reach."
        ),
    )
    async def set_value(
        ctx: Context, app: str, window_id: int, element_index: int, value: str
    ) -> str:
        ref = _window_ref(app, window_id)
        client = _client_for(ctx)
        try:
            await client.set_value(ref, element_index, value)
        except HelperError as exc:
            return f"set_value failed: {_err(exc)}"
        return f"set element {element_index} in window {window_id}"

    @server.tool(
        title="Secondary action",
        description=(
            "Invoke a secondary accessibility action on an element, such as "
            "'Expand', 'Collapse', 'Raise', or 'Scroll Up'."
        ),
    )
    async def perform_secondary_action(
        ctx: Context, app: str, window_id: int, element_index: int, action: str
    ) -> str:
        ref = _window_ref(app, window_id)
        client = _client_for(ctx)
        try:
            await client.perform_secondary_action(ref, element_index, action)
        except HelperError as exc:
            return f"secondary action failed: {_err(exc)}"
        return f"performed {action!r} on element {element_index} in window {window_id}"

    @server.tool(
        title="Activate window",
        description=(
            "Bring a window to the foreground. This is the ONLY tool that "
            "takes focus away from you -- every other action leaves your "
            "focus and mouse alone. Use it only when the agent truly needs "
            "to be the active window."
        ),
    )
    async def activate_window(ctx: Context, app: str, window_id: int) -> str:
        ref = _window_ref(app, window_id)
        client = _client_for(ctx)
        try:
            await client.activate_window(ref)
        except HelperError as exc:
            return f"activate failed: {_err(exc)}"
        return f"activated window {window_id} (it now has focus)"

    # -- diagnostics -------------------------------------------------------

    @server.tool(
        title="Server status",
        description=(
            "Report which helper binary and codex CLI are in use, whether "
            "the helper is running, which apps are approved so far, and "
            "the helper's own recent log lines."
        ),
    )
    def status() -> str:
        lines = [
            f"helper      : {transport.helper_path}",
            f"codex cli   : {transport.codex_cli_path or '(not found)'}",
            f"alive       : {transport.is_alive()}",
            f"approved    : {', '.join(transport.approved_apps()) or '(none)'}",
            f"auto_approve: {auto_approve}",
        ]
        tail = transport.stderr_tail(limit=10)
        if tail:
            lines.append("helper log  :")
            lines.extend(f"    {line}" for line in tail)
        return "\n".join(lines)

    server._cua_transport = transport  # type: ignore[attr-defined]
    return server


# --------------------------------------------------------------------------
# The trycua/cua backend
# --------------------------------------------------------------------------

CUA_INSTRUCTIONS = """\
Collaborative Windows computer use, powered by the local `trycua/cua`
driver. Everything runs on this machine -- no cloud, no account, no usage
limit, and no OpenAI binaries.

HOW IT STAYS COLLABORATIVE
Every action names a target window and is delivered to that window
WITHOUT raising it. You and the agent can use the PC at the same time.
The one exception is `activate_window`, which explicitly takes focus.

WORKFLOW
1. `list_apps` / `list_windows` -- find the target. Never guess a window.
2. `get_window_state` -- read the accessibility tree to get element indexes.
3. Act with exactly one action.
4. Re-read state to verify. Element indexes are only valid for the
   observation that produced them; re-observe after anything changes.

Element indexes come from `get_window_state`'s accessibility tree. Pass one
as `element_index` instead of coordinates when the target is a known
control.

WHERE THE TEXT GOES
`type_text` and `press_key` deliver keystrokes to the target window. This
driver routes them to the window rather than to whatever happens to hold
focus, so no click is needed first -- but pass `element_index` when you
mean a specific control. `set_value` is the most reliable way to fill a
field, and an empty value clears it.

SECONDARY ACTIONS
This driver has no accessibility-action tool. `perform_secondary_action`
therefore takes two forms: a single word ('Invoke', 'Expand', 'Toggle',
'Check', 'Uncheck') is honoured by clicking the element, and a menu path
('File>Save As') walks the application's menu. Anything else returns an
error rather than doing nothing.
"""


def _build_cua_server(driver_path: str | None = None) -> MCPServer:
    """Build the server on the `trycua/cua` driver.

    The same sixteen tools, the same names, the same arguments -- only the
    driver underneath is different. A harness config does not have to
    change when you switch backends.
    """
    from .cua_backend import CuaDriverTransport, find_cua_driver
    from .cua_client import CuaDriverClient

    resolved = find_cua_driver(driver_path)
    transport = CuaDriverTransport(driver_path=resolved)
    client = CuaDriverClient(transport)
    api = _CuaApi(client)

    server = MCPServer(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        instructions=CUA_INSTRUCTIONS,
    )

    def _client_for(ctx: Context) -> _CuaApi:
        return api

    # -- discovery ---------------------------------------------------------

    @server.tool(
        title="List apps",
        description=(
            "List installed apps and the windows each one currently owns. "
            "Read-only; no approval needed. Start here to find a target."
        ),
    )
    async def list_apps() -> str:
        apps = await api.list_apps()
        lines = []
        for a in apps:
            flag = "running" if a["isRunning"] else "idle"
            lines.append(f"- {a['displayName']}  [id={a['id']}]  ({flag})")
            for w in a["windows"]:
                lines.append(f"    window id={w['id']} title={w.get('title')!r}")
        return "\n".join(lines) if lines else "(no apps found)"

    @server.tool(
        title="List windows",
        description=(
            "List every open, targetable window with its app id, window id, "
            "and title. Read-only; no approval needed."
        ),
    )
    async def list_windows() -> str:
        return format_windows(await api.list_windows())

    @server.tool(
        title="Get window",
        description=(
            "Rehydrate an open window by id. Use after a lost window binding "
            "or a stale handle. Read-only."
        ),
    )
    async def get_window(window_id: int, app: str) -> str:
        ref = await api.get_window(window_id, app)
        return f"id={ref.id} app={ref.app} title={ref.title!r}"

    # -- observation -------------------------------------------------------

    @server.tool(
        title="Read window state",
        description=(
            "Read a window's accessibility tree (with element indexes) and/or "
            "a screenshot. Element indexes are only valid for the observation "
            "that produced them."
        ),
    )
    async def get_window_state(
        ctx: Context,
        app: str,
        window_id: int,
        include_text: bool = True,
        include_screenshot: bool = False,
    ) -> str:
        ref = _window_ref(app, window_id)
        try:
            state = await api.get_window_state(
                ref, include_screenshot=include_screenshot, include_text=include_text
            )
        except HelperError as exc:
            return f"could not read window state: {_err(exc)}"
        return _state_summary(state)

    @server.tool(
        title="Read accessibility tree",
        description=(
            "The formatted accessibility tree for a window, as plain text "
            "with element indexes. Cheaper than a full window state read."
        ),
    )
    async def read_accessibility_tree(ctx: Context, app: str, window_id: int) -> str:
        ref = _window_ref(app, window_id)
        try:
            tree = await api.accessibility_tree(ref)
        except HelperError as exc:
            return f"could not read accessibility tree: {_err(exc)}"
        return tree or "(empty accessibility tree)"

    # -- actions -----------------------------------------------------------

    @server.tool(
        title="Launch app",
        description=(
            "Launch an app by its installed name, or by an explicit path. "
            "The window is opened without being brought to the foreground."
        ),
    )
    async def launch_app(ctx: Context, app: str) -> str:
        try:
            await api.launch_app(app)
        except HelperError as exc:
            return f"could not launch {app!r}: {_err(exc)}"
        return f"launched {app!r}; call list_apps or list_windows to see its window"

    @server.tool(
        title="Click",
        description=(
            "Click at window-relative coordinates in a target window. The "
            "window is NOT brought to the foreground. To click an element "
            "you have an index for, use `click_element` instead."
        ),
    )
    async def click(
        ctx: Context,
        app: str,
        window_id: int,
        element_index: int | None = None,
        x: int | None = None,
        y: int | None = None,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> str:
        ref = _window_ref(app, window_id)
        try:
            await api.click(
                ref,
                x=x,
                y=y,
                element_index=element_index,
                mouse_button=mouse_button,
                click_count=click_count,
            )
        except HelperError as exc:
            return f"click failed: {_err(exc)}"
        target = f"element {element_index}" if element_index is not None else f"({x}, {y})"
        return f"clicked {target} in window {window_id}"

    @server.tool(
        title="Click element",
        description=(
            "Click an element by its index in the accessibility tree, "
            "actuated through UI Automation. Prefer this over `click` when "
            "you have an element index. The window is NOT brought to the "
            "foreground."
        ),
    )
    async def click_element(
        ctx: Context,
        app: str,
        window_id: int,
        element_index: int,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> str:
        ref = _window_ref(app, window_id)
        try:
            await api.click_element(
                ref,
                element_index=element_index,
                mouse_button=mouse_button,
                click_count=click_count,
            )
        except HelperError as exc:
            return f"click_element failed: {_err(exc)}"
        return f"clicked element {element_index} in window {window_id}"

    @server.tool(
        title="Type text",
        description=(
            "Type literal text into a target window. Pass `element_index` "
            "to say which control the text belongs in. Use press_key for "
            "Enter, Tab, arrows, and shortcuts."
        ),
    )
    async def type_text(
        ctx: Context,
        app: str,
        window_id: int,
        text: str,
        element_index: int | None = None,
    ) -> str:
        ref = _window_ref(app, window_id)
        try:
            await api.type_text(ref, text, element_index=element_index)
        except HelperError as exc:
            return f"type failed: {_err(exc)}"
        where = f" element {element_index}" if element_index is not None else ""
        return f"typed {len(text)} chars into{where or ''} window {window_id}"

    @server.tool(
        title="Press key",
        description=(
            "Press a key or chord in the target window, using X keysym-style "
            "names: 'Return', 'Tab', 'Escape', 'Control_L+a', "
            "'Control_L+Shift_L+period'. Pass `element_index` to target a "
            "specific control."
        ),
    )
    async def press_key(
        ctx: Context,
        app: str,
        window_id: int,
        key: str,
        element_index: int | None = None,
    ) -> str:
        ref = _window_ref(app, window_id)
        try:
            await api.press_key(ref, key, element_index=element_index)
        except HelperError as exc:
            return f"press_key failed: {_err(exc)}"
        return f"pressed {key!r} in window {window_id}"

    @server.tool(
        title="Scroll",
        description=(
            "Scroll from a window-relative coordinate. Negative scroll_y "
            "scrolls up, positive scrolls down; negative scroll_x scrolls "
            "left, positive right."
        ),
    )
    async def scroll(
        ctx: Context,
        app: str,
        window_id: int,
        x: int,
        y: int,
        scroll_x: int = 0,
        scroll_y: int = 0,
    ) -> str:
        ref = _window_ref(app, window_id)
        try:
            await api.scroll(ref, x, y, scroll_x=scroll_x, scroll_y=scroll_y)
        except HelperError as exc:
            return f"scroll failed: {_err(exc)}"
        return f"scrolled ({scroll_x}, {scroll_y}) in window {window_id}"

    @server.tool(
        title="Drag",
        description=(
            "Drag from one window-relative point to another. Use for "
            "sliders, drawing, and canvas work."
        ),
    )
    async def drag(
        ctx: Context,
        app: str,
        window_id: int,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
    ) -> str:
        ref = _window_ref(app, window_id)
        try:
            await api.drag(ref, from_x, from_y, to_x, to_y)
        except HelperError as exc:
            return f"drag failed: {_err(exc)}"
        return f"dragged ({from_x},{from_y}) -> ({to_x},{to_y}) in window {window_id}"

    @server.tool(
        title="Set value",
        description=(
            "Replace the value of an editable element, addressed by the "
            "element_index from a window state read. Works without focus, "
            "and an empty value clears the field."
        ),
    )
    async def set_value(
        ctx: Context, app: str, window_id: int, element_index: int, value: str
    ) -> str:
        ref = _window_ref(app, window_id)
        try:
            await api.set_value(ref, element_index, value)
        except HelperError as exc:
            return f"set_value failed: {_err(exc)}"
        return f"set element {element_index} in window {window_id}"

    @server.tool(
        title="Secondary action",
        description=(
            "Invoke an accessibility action on an element, or a menu path "
            "on the window.\n\n"
            "A single word names an accessibility action: 'Invoke', "
            "'Expand', 'Collapse', 'Toggle', 'Check', 'Uncheck'. The "
            "driver honours these by clicking the element, and 'Check' / "
            "'Uncheck' only click when the click would move the control to "
            "the state asked for.\n\n"
            "A path with '>' names an application menu: 'File>Save As'. "
            "That goes to the driver's invoke_menu, which resolves the "
            "menu labels through the operating system's accessibility API.\n\n"
            "An action this driver cannot express returns an error rather "
            "than doing nothing."
        ),
    )
    async def perform_secondary_action(
        ctx: Context, app: str, window_id: int, element_index: int, action: str
    ) -> str:
        ref = _window_ref(app, window_id)
        try:
            await api.perform_secondary_action(ref, element_index, action)
        except HelperError as exc:
            return f"secondary action failed: {_err(exc)}"
        return f"performed {action!r} on element {element_index} in window {window_id}"

    @server.tool(
        title="Activate window",
        description=(
            "Bring a window to the foreground. This is the ONLY tool that "
            "takes focus away from you -- every other action leaves your "
            "focus and mouse alone. Use it only when the agent truly needs "
            "to be the active window."
        ),
    )
    async def activate_window(ctx: Context, app: str, window_id: int) -> str:
        ref = _window_ref(app, window_id)
        try:
            await api.activate_window(ref)
        except HelperError as exc:
            return f"activate failed: {_err(exc)}"
        return f"activated window {window_id} (it now has focus)"

    # -- diagnostics -------------------------------------------------------

    @server.tool(
        title="Server status",
        description=(
            "Report which driver binary is in use, whether it is running, "
            "and the backend it serves."
        ),
    )
    def status() -> str:
        return "\n".join(
            [
                f"backend     : cua (trycua/cua)",
                f"driver      : {transport.driver_path}",
                f"alive       : {transport.is_alive()}",
                f"approval    : handled by the driver, not by this server",
            ]
        )

    server._cua_transport = transport  # type: ignore[attr-defined]
    return server


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------


def main() -> int:
    """Console-script entry point: serve MCP over stdio."""
    import argparse

    parser = argparse.ArgumentParser(
        prog=SERVER_NAME,
        description=(
            "Collaborative Windows computer-use MCP server. Backed by the "
            "local Codex computer-use helper, or by the MIT-licensed "
            "trycua/cua driver. No cloud, no usage limits."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=("codex", "cua"),
        default=os.environ.get("WINDOWS_CUA_BACKEND", "codex"),
        help=(
            "which driver to use: 'codex' (the helper that ships with Codex "
            "CLI, default) or 'cua' (the trycua/cua driver)"
        ),
    )
    parser.add_argument(
        "--helper",
        default=os.environ.get("CODEX_COMPUTER_USE_BIN"),
        help="path to codex-computer-use.exe (auto-detected if omitted)",
    )
    parser.add_argument(
        "--codex-cli",
        default=os.environ.get("CODEX_CLI_PATH"),
        help="path to the codex CLI used for the approval handshake",
    )
    parser.add_argument(
        "--driver",
        default=os.environ.get("CUA_DRIVER_BIN"),
        help="path to cua-driver.exe (auto-detected if omitted)",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="approve app requests without asking the user (not recommended)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the driver is discoverable and exit",
    )
    args = parser.parse_args()

    try:
        if args.backend == "cua":
            from .cua_backend import find_cua_driver

            driver: Path | None = find_cua_driver(args.driver)
            helper = None
        else:
            driver = None
            helper = find_helper(args.helper)
    except HelperError as exc:
        # HelperNotFoundError for the Codex helper, CuaDriverNotFound for
        # the trycua/cua driver -- both are HelperError subclasses.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        print(f"backend   : {args.backend}")
        if args.backend == "cua":
            print(f"driver    : {driver}")
        else:
            cli = find_codex_cli(args.codex_cli)
            print(f"helper    : {helper}")
            print(f"codex cli : {cli or '(not found)'}")
        return 0

    try:
        server = build_server(
            helper_path=args.helper,
            codex_cli_path=args.codex_cli,
            auto_approve=args.auto_approve,
            backend=args.backend,
            driver_path=args.driver,
        )
    except HelperNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # stderr is the log channel; stdout is the MCP wire and must stay clean.
    if args.backend == "cua":
        target = f"driver={driver}"
    else:
        target = f"helper={helper}, codex_cli={args.codex_cli or 'auto'}"
    print(
        f"{SERVER_NAME} {SERVER_VERSION} ready "
        f"(backend={args.backend}, {target})",
        file=sys.stderr,
    )

    try:
        server.run(transport="stdio")
    except KeyboardInterrupt:
        with contextlib.suppress(Exception):
            server._cua_transport.close()  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

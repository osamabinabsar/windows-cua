"""Typed client over the local Codex computer-use helper.

Wraps :class:`~codex_cua_mcp.transport.HelperTransport` with the `sky`
Windows API surface, expressed in the vocabulary an agent actually uses:
apps, windows, and window-relative actions.

The important property this layer preserves is **collaboration**: input
actions name a target window and are delivered to that window *without*
raising it. The helper does this natively (per-window UIA actuation and
coordinate-routed background injection), so the human keeps their focus and
their mouse while the agent works.

Terminology
-----------
``app``
    A canonical app identifier, e.g. ``Microsoft.WindowsNotepad_8wekyb3d8bbwe!App``
    or ``process:C:\\Program Files\\Obsidian\\Obsidian.exe``.

``window``
    An open window, identified by ``{"app": <app id>, "id": <int>}``. The
    ``id`` is opaque and only valid for windows returned by a prior call.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .transport import (
    APPROVED_APP_META_KEY,
    ApprovalRequired,
    HelperError,
    HelperTransport,
)

__all__ = ["CuaClient", "WindowRef", "format_windows"]


@dataclass(frozen=True)
class WindowRef:
    """A targetable window."""

    app: str
    id: int
    title: str | None = None

    def to_helper(self) -> dict[str, Any]:
        """The wire shape the helper expects."""
        out: dict[str, Any] = {"app": self.app, "id": self.id}
        if self.title is not None:
            out["title"] = self.title
        return out

    @classmethod
    def from_helper(cls, raw: dict[str, Any], fallback_app: str | None = None) -> "WindowRef | None":
        if not isinstance(raw, dict):
            return None
        wid = raw.get("id")
        if not isinstance(wid, int):
            return None
        app = raw.get("app") or fallback_app
        if not isinstance(app, str) or not app.strip():
            return None
        title = raw.get("title")
        return cls(app=app, id=wid, title=title if isinstance(title, str) else None)


def format_windows(windows: Iterable[WindowRef]) -> str:
    """Render windows as a compact table for an agent to read."""
    lines = []
    for w in windows:
        title = w.title or "(untitled)"
        lines.append(f"- id={w.id}  app={w.app}  title={title!r}")
    return "\n".join(lines) if lines else "(no windows)"


class CuaClient:
    """The `sky` Windows API, over the local helper.

    Every method that touches an app goes through :meth:`_act`, which
    handles the approval handshake: if the helper says the app is not
    approved, the caller-supplied ``on_approval`` hook is invoked to obtain
    real user consent, and only then is the request retried with the
    approved-app meta key.

    The client is cheap to build -- it is a thin wrapper over the shared
    transport -- so a server can make one per tool call, each bound to that
    call's own approval hook. That keeps concurrent tool calls from
    trampling each other's consent state.
    """

    def __init__(
        self,
        transport: HelperTransport,
        on_approval: Any = None,
        gate: Callable[[], None] | None = None,
    ) -> None:
        """
        Parameters
        ----------
        transport:
            The helper transport.
        on_approval:
            Optional callable ``(request: dict) -> bool`` (sync or async).
            Receives the helper's approval request and returns True to
            allow the app. When absent, actions against unapproved apps
            raise :class:`ApprovalRequired` instead of being silently
            approved.
        gate:
            Optional callable, called before every request. It raises if
            the user has taken the computer back -- the take-over gate
            from :class:`~codex_cua_mcp.takeover.TakeoverController`.
        """
        self._t = transport
        self._on_approval = on_approval
        self._gate = gate
        # Window ids we have observed with a screenshot, so an
        # element-targeted action is not refused for want of geometry.
        self._observed: set[int] = set()

    def with_approval_hook(self, on_approval: Any) -> "CuaClient":
        """A sibling client sharing this transport, with a new approval hook.

        Use this per tool call so each call carries its own consent
        callback instead of mutating shared state.
        """
        sibling = CuaClient(self._t, on_approval=on_approval, gate=self._gate)
        sibling._observed = self._observed
        return sibling

    # -- plumbing ----------------------------------------------------------

    @property
    def transport(self) -> HelperTransport:
        return self._t

    def _check_gate(self) -> None:
        """Refuse the call if the user has taken the computer back."""
        if self._gate is not None:
            self._gate()

    async def _act(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        app: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Perform an action, handling the approval handshake.

        ``app`` is the canonical app identifier the action targets. It is
        sent to the helper in the approved-app meta key *only* after the
        user has actually approved that app.
        """
        self._check_gate()
        try:
            return await asyncio.to_thread(
                self._t.request, method, params, timeout=timeout, approved_app=app
            )
        except ApprovalRequired as exc:
            request = exc.request
            if self._on_approval is None:
                raise
            allowed = self._on_approval(request)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                raise HelperError(
                    f"Computer Use was not approved to use "
                    f"{request.get('displayName') or request.get('app')}"
                ) from exc
            # Record the approval under the app id the helper itself asked
            # about. The transport matches approvals by app identity, so a
            # later window-targeted call -- which names the same app
            # differently -- is covered by this one approval.
            approved = str(request.get("app") or app or "")
            self._t.approve(approved)
            return await asyncio.to_thread(
                self._t.request,
                method,
                params,
                timeout=timeout,
                approved_app=approved,
            )

    def _read(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """A read-only call. Never triggers an approval prompt."""
        self._check_gate()
        return self._t.request(method, params)

    # -- discovery ---------------------------------------------------------

    def list_apps(self) -> list[dict[str, Any]]:
        """Installed apps, with the windows each one currently owns.

        Read-only: does not require approval for any app.
        """
        raw = self._read("list_apps", {})
        if not isinstance(raw, list):
            return []
        apps: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            app_id = entry.get("id")
            if not isinstance(app_id, str) or not app_id.strip():
                continue
            windows = []
            for w in entry.get("windows") or []:
                ref = WindowRef.from_helper(w, fallback_app=app_id)
                if ref is not None:
                    windows.append(ref.to_helper())
            apps.append(
                {
                    "id": app_id,
                    "displayName": entry.get("displayName") or app_id,
                    "isRunning": bool(entry.get("isRunning", bool(windows))),
                    "windows": windows,
                }
            )
        return apps

    def list_windows(self) -> list[WindowRef]:
        """Every open, targetable window."""
        raw = self._read("list_windows", {})
        if isinstance(raw, dict):
            raw = raw.get("windows") or []
        if not isinstance(raw, list):
            return []
        out: list[WindowRef] = []
        for w in raw:
            ref = WindowRef.from_helper(w)
            if ref is not None:
                out.append(ref)
        return out

    def get_window(self, window_id: int, app: str | None = None) -> WindowRef:
        """Rehydrate a window by id. Use after a lost window binding."""
        params: dict[str, Any] = {"id": int(window_id)}
        if app:
            params["app"] = app
        raw = self._read("get_window", params)
        ref = WindowRef.from_helper(raw, fallback_app=app)
        if ref is None:
            raise HelperError("helper did not return a window")
        return ref

    # -- observation -------------------------------------------------------

    async def get_window_state(
        self,
        window: WindowRef,
        include_screenshot: bool = False,
        include_text: bool = True,
    ) -> dict[str, Any]:
        """Read a window's accessibility tree and/or screenshot.

        ``include_screenshot`` defaults to False here: screenshots are large
        and this client returns them as data the caller can forward, not
        something it renders. Accessibility text is the cheap, precise
        signal for element-targeted work.
        """
        params = {
            "window": window.to_helper(),
            "include_screenshot": bool(include_screenshot),
            "include_text": bool(include_text),
        }
        raw = await self._act("get_window_state", params, app=window.app)
        if not isinstance(raw, dict):
            raise HelperError("helper did not return window state")
        if include_screenshot and raw.get("screenshots"):
            self._observed.add(int(window.id))
        return raw

    async def accessibility_tree(self, window: WindowRef) -> str:
        """The formatted accessibility tree text for a window."""
        state = await self.get_window_state(
            window, include_screenshot=False, include_text=True
        )
        acc = state.get("accessibility")
        if not isinstance(acc, dict):
            return ""
        tree = acc.get("tree")
        return tree if isinstance(tree, str) else ""

    # -- actions -----------------------------------------------------------

    async def launch_app(self, app: str) -> None:
        """Launch an app by id, or by explicit ``.exe`` path.

        The Windows helper sometimes reports ``accessibility window-opened
        handler did not become ready`` on a cold start: the app launches,
        but the helper's UIA watcher is not wired up in time to notice the
        new window. The app is running by then, so the helper is the thing
        that is late. One retry settles it.
        """
        last: Exception | None = None
        for attempt in range(2):
            try:
                await self._act("launch_app", {"app": app}, app=app, timeout=45.0)
                return
            except HelperError as exc:
                last = exc
                if attempt == 0 and "window-opened handler" in str(exc):
                    continue
                raise
        if last is not None:  # pragma: no cover - the loop always returns or raises
            raise last

    async def activate_window(self, window: WindowRef) -> None:
        """Bring a window to the foreground.

        This is the one action that *does* take focus. It is an explicit
        escape hatch -- every other action leaves the user's focus alone.
        """
        await self._act("activate_window", {"window": window.to_helper()}, app=window.app)

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
        """Click in a window, by element index or by window-relative coords.

        Delivered to the target window without raising it, so the user can
        keep working elsewhere.
        """
        params: dict[str, Any] = {"window": window.to_helper()}
        if element_index is not None:
            # The Windows helper's `click` method requires x/y; it rejects
            # an element-targeted click. Route to `click_element`, which
            # actuates the control through UI Automation.
            await self.click_element(
                window,
                element_index=element_index,
                mouse_button=mouse_button,
                click_count=click_count,
            )
            return
        if x is None or y is None:
            raise ValueError("click needs either element_index or both x and y")
        params["x"] = int(x)
        params["y"] = int(y)
        params["mouse_button"] = mouse_button
        params["click_count"] = int(click_count)
        if screenshot_id:
            params["screenshotId"] = screenshot_id
        await self._act("click", params, app=window.app)

    async def click_element(
        self,
        window: WindowRef,
        element_index: int,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> None:
        """Click an element by its index in the accessibility tree.

        The Windows helper's ``click`` method requires window-relative
        coordinates, so an element-targeted click goes through
        ``click_element`` instead.

        The helper also refuses an element-targeted action unless the
        window has been observed with a screenshot -- it needs the
        captured geometry to route the input. This method takes that
        observation itself when the caller has not already made one.
        """
        await self._ensure_observed(window)
        params: dict[str, Any] = {
            "window": window.to_helper(),
            "element_index": int(element_index),
            "mouse_button": mouse_button,
            "click_count": int(click_count),
        }
        await self._act_on_element(window, "click_element", params)

    async def _ensure_observed(self, window: WindowRef) -> None:
        """Observe the window with a screenshot, if it has not been observed.

        The helper tracks the last observation per window and rejects an
        element-targeted action with "coordinate input geometry is
        unavailable" when that observation had no screenshot. Re-observing
        is cheap relative to a failed action, and it keeps the caller from
        having to know this rule.
        """
        if window.id in self._observed:
            return
        await self.get_window_state(
            window, include_screenshot=True, include_text=False
        )

    async def _act_on_element(
        self,
        window: WindowRef,
        method: str,
        params: dict[str, Any],
    ) -> None:
        """Run an element-targeted action, re-observing if the helper says so.

        The helper's observation of a window can go stale -- the window
        moved, resized, or the observation aged out -- and it then refuses
        the action with "coordinate input geometry is unavailable" or
        "call get_window_state before using this window". A fresh
        observation fixes both, and the action is worth retrying once
        because the refusal says nothing about the action itself.
        """
        await self._ensure_observed(window)
        try:
            await self._act(method, params, app=window.app)
        except HelperError as exc:
            message = str(exc)
            stale = "coordinate input geometry is unavailable" in message or (
                "call get_window_state before using this window" in message
            )
            if not stale:
                raise
            self._observed.discard(int(window.id))
            await self._ensure_observed(window)
            await self._act(method, params, app=window.app)

    async def type_text(
        self,
        window: WindowRef,
        text: str,
        element_index: int | None = None,
    ) -> None:
        """Type literal text into a window.

        The helper's ``type_text`` delivers keystrokes to whatever control
        currently holds focus in the target window. If nothing holds focus
        there, the characters are typed into the void and are lost -- the
        call reports success and the target is unchanged. That silent
        failure is the single most dangerous thing an agent can hit here,
        so pass ``element_index`` to make the target explicit: the element
        is clicked first to place the caret, then the text is typed.

        With no ``element_index`` the call types into the window's existing
        focus, which is right when the caller has just clicked something.
        """
        if element_index is not None:
            await self.click_element(window, element_index=element_index)
        await self._act(
            "type_text", {"window": window.to_helper(), "text": text}, app=window.app
        )

    async def press_key(
        self,
        window: WindowRef,
        key: str,
        element_index: int | None = None,
    ) -> None:
        """Press a key chord, e.g. ``Control_L+a`` or ``Return``.

        Like :meth:`type_text`, the key goes to the window's current focus.
        Pass ``element_index`` to click that element first, so the chord
        lands where it was meant to.
        """
        if not key or not key.strip():
            raise ValueError("key is required")
        if element_index is not None:
            await self.click_element(window, element_index=element_index)
        await self._act(
            "press_key", {"window": window.to_helper(), "key": key}, app=window.app
        )

    async def scroll(
        self,
        window: WindowRef,
        x: int,
        y: int,
        scroll_x: int = 0,
        scroll_y: int = 0,
        screenshot_id: str | None = None,
    ) -> None:
        """Scroll from a window-relative coordinate.

        Negative ``scroll_y`` scrolls up, positive scrolls down. Negative
        ``scroll_x`` scrolls left, positive right.
        """
        params: dict[str, Any] = {
            "window": window.to_helper(),
            "x": int(x),
            "y": int(y),
            "scrollX": int(scroll_x),
            "scrollY": int(scroll_y),
        }
        if screenshot_id:
            params["screenshotId"] = screenshot_id
        await self._act("scroll", params, app=window.app)

    async def drag(
        self,
        window: WindowRef,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        screenshot_id: str | None = None,
    ) -> None:
        """Drag from one window-relative point to another."""
        params: dict[str, Any] = {
            "window": window.to_helper(),
            "from_x": int(from_x),
            "from_y": int(from_y),
            "to_x": int(to_x),
            "to_y": int(to_y),
        }
        if screenshot_id:
            params["screenshotId"] = screenshot_id
        await self._act("drag", params, app=window.app)

    async def set_value(self, window: WindowRef, element_index: int, value: str) -> None:
        """Replace the value of an editable element.

        This one needs no focused control, so it is the reliable way to
        fill a field. An empty value clears it.
        """
        await self._act_on_element(
            window,
            "set_value",
            {
                "window": window.to_helper(),
                "element_index": int(element_index),
                "value": value,
            },
        )

    async def perform_secondary_action(
        self, window: WindowRef, element_index: int, action: str
    ) -> None:
        """Invoke a secondary accessibility action, e.g. ``Expand``."""
        await self._act_on_element(
            window,
            "perform_secondary_action",
            {
                "window": window.to_helper(),
                "element_index": int(element_index),
                "action": action,
            },
        )

"""A `sky`-shaped client over the `trycua/cua` driver.

The Codex backend and this one present the *same* API to the MCP layer:
apps, windows, and window-relative actions. That is the point — a
harness configuration does not have to change when the driver changes.

The mapping is not one-to-one, and the differences are worth stating:

**Windows are addressed by pid + window_id**, not by an app id. The
driver has no "app id" concept, so :class:`CuaDriverClient` synthesises
one as ``pid:<pid>`` and keeps the pid alongside it. An action needs
both, so the window carries them.

**Delivery mode is explicit.** Every input the driver accepts carries
``delivery_mode``, and ``"background"`` is the one that keeps the target
window from taking focus. This client always asks for background. The
one exception is :meth:`activate_window`, which is the deliberate
focus-taking escape hatch.

**Element handles are tokens, not indexes.** The driver returns both
``element_index`` and ``element_token`` for each element. It will not
accept a bare ``element_index`` for a value-setting action: it wants
either an ``element_token``, or a ``snapshot_id`` *together with* the
index. The client resolves the index to a token from a fresh read, so
the handle is never stale when it is used.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .client import WindowRef
from .cua_backend import CuaDriverError, CuaDriverTransport

__all__ = ["CuaDriverClient", "CuaWindow"]


@dataclass(frozen=True)
class _ElementHandle:
    """How the driver addresses one element."""

    index: int
    token: str | None = None
    snapshot_id: str | None = None

    def apply(self, args: dict[str, Any]) -> None:
        """Add this element's addressing to a call's arguments."""
        if self.token:
            args["element_token"] = self.token
        elif self.snapshot_id:
            # The driver's other accepted form: the snapshot plus the row.
            args["snapshot_id"] = self.snapshot_id
            args["element_index"] = self.index
        else:
            raise CuaDriverError(
                f"element {self.index} has no usable handle; re-read the window"
            )


@dataclass(frozen=True)
class CuaWindow:
    """A window in the driver's vocabulary: a pid and a window id."""

    pid: int
    window_id: int
    title: str = ""
    app_name: str = ""

    @property
    def app(self) -> str:
        """A synthetic app id, so the shared API can name a target."""
        return f"pid:{self.pid}"

    def to_ref(self) -> WindowRef:
        return WindowRef(app=self.app, id=self.window_id, title=self.title or None)


class CuaDriverClient:
    """The `sky` Windows API, over the `trycua/cua` driver.

    Deliberately not approval-gated: the driver has its own permission
    model, and this client's job is to be a faithful, predictable
    translation of the shared API.
    """

    def __init__(self, transport: CuaDriverTransport) -> None:
        self._t = transport

    @property
    def transport(self) -> CuaDriverTransport:
        return self._t

    # -- plumbing ----------------------------------------------------------

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._t.call_tool, name, arguments)

    async def _call_action(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call an input tool, escalating to foreground if it must.

        The driver delivers input in the background, so the target window
        never takes focus. Some window classes refuse background input
        for some event kinds -- a dialog cannot take a background scroll,
        for instance. When that happens the driver says so and asks for
        ``delivery_mode: "foreground"``, promising to restore the
        previous foreground window afterwards.

        So the escalation is exactly what the driver asks for, and the
        user's focus comes back.
        """
        try:
            return await self._call(name, arguments)
        except CuaDriverError as exc:
            message = str(exc)
            if "Background delivery is not available" not in message:
                raise
            retry = dict(arguments)
            retry["delivery_mode"] = "foreground"
            return await self._call(name, retry)

    # -- discovery ---------------------------------------------------------

    async def list_apps(self) -> list[dict[str, Any]]:
        """Installed apps, with the windows each one currently owns."""
        raw = await self._call("list_apps", {})
        apps = raw.get("apps") if isinstance(raw, dict) else None
        if not isinstance(apps, list):
            return []

        windows = await self._windows_by_pid()
        out: list[dict[str, Any]] = []
        for entry in apps:
            if not isinstance(entry, dict):
                continue
            pid = entry.get("pid")
            if not isinstance(pid, int):
                continue
            out.append(
                {
                    "id": f"pid:{pid}",
                    "displayName": entry.get("name") or f"pid:{pid}",
                    "isRunning": bool(entry.get("running", True)),
                    "windows": windows.get(pid, []),
                }
            )
        return out

    async def list_windows(self) -> list[WindowRef]:
        """Every open, targetable window."""
        windows = await self._windows()
        return [w.to_ref() for w in windows]

    async def _windows(self, pid: int | None = None) -> list[CuaWindow]:
        args: dict[str, Any] = {}
        if pid is not None:
            args["pid"] = int(pid)
        raw = await self._call("list_windows", args)
        entries = raw.get("windows") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            return []
        out: list[CuaWindow] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            window_id = entry.get("window_id")
            window_pid = entry.get("pid")
            if not isinstance(window_id, int) or not isinstance(window_pid, int):
                continue
            out.append(
                CuaWindow(
                    pid=window_pid,
                    window_id=window_id,
                    title=str(entry.get("title") or ""),
                    app_name=str(entry.get("app_name") or ""),
                )
            )
        return out

    async def _windows_by_pid(self) -> dict[int, list[dict[str, Any]]]:
        """Windows grouped by pid, in the shared API's window shape."""
        out: dict[int, list[dict[str, Any]]] = {}
        for w in await self._windows():
            out.setdefault(w.pid, []).append(
                {
                    "app": w.app,
                    "id": w.window_id,
                    "title": w.title or None,
                }
            )
        return out

    async def get_window(self, window_id: int, app: str | None = None) -> WindowRef:
        """Rehydrate a window by id."""
        pid = _pid_from_app(app)
        for w in await self._windows(pid):
            if w.window_id == int(window_id):
                return w.to_ref()
        raise CuaDriverError(f"no window with id {window_id}")

    # -- observation -------------------------------------------------------

    async def get_window_state(
        self,
        window: WindowRef,
        include_screenshot: bool = False,
        include_text: bool = True,
    ) -> dict[str, Any]:
        """Read a window's accessibility tree and/or screenshot."""
        args: dict[str, Any] = {
            "pid": _pid_from_app(window.app),
            "window_id": int(window.id),
            "include_accessibility_tree": bool(include_text),
        }
        if include_screenshot:
            # The driver's capture modes are "ax" (accessibility only) and
            # "vision" (a screenshot of the window, occluded or not).
            args["capture_mode"] = "vision"
        raw = await self._call("get_window_state", args)
        return raw if isinstance(raw, dict) else {}

    async def accessibility_tree(self, window: WindowRef) -> str:
        """The formatted accessibility tree text for a window.

        The driver's ``get_accessibility_tree`` returns a whole-desktop
        view -- ``{"processes": [...], "windows": [...]}`` -- whose
        window entries carry geometry but no tree. The per-window tree
        is what ``get_window_state`` returns as ``tree_markdown``, so
        that is used, and the desktop-wide call is the fallback.
        """
        tree = await self.accessibility_tree_for(window)
        if tree:
            return tree
        raw = await self._call(
            "get_accessibility_tree",
            {
                "pid": _pid_from_app(window.app),
                "window_id": int(window.id),
            },
        )
        return _extract_tree(raw, int(window.id))

    async def accessibility_tree_for(self, window: WindowRef) -> str:
        """The tree for one window, from the same read the state uses.

        ``get_window_state`` already returns the window's tree as
        ``tree_markdown``, which is simpler and always consistent with
        the element indexes it reports.
        """
        state = await self.get_window_state(window, include_text=True)
        tree = state.get("tree_markdown")
        return tree if isinstance(tree, str) else ""

    # -- actions -----------------------------------------------------------

    async def launch_app(self, app: str) -> None:
        """Launch an app by name or path.

        The driver distinguishes an installed app (``name``) from a
        program on disk (``path``). The test is the shape of the value,
        not its extension: ``charmap.exe`` is an installed app's file
        name, while ``C:\\Windows\\System32\\charmap.exe`` is a path.
        """
        args: dict[str, Any] = {}
        if _looks_like_path(app):
            args["path"] = app
        else:
            args["name"] = app
        await self._call("launch_app", args)

    async def activate_window(self, window: WindowRef) -> None:
        """Bring a window to the foreground. The one focus-taking action."""
        await self._call(
            "bring_to_front",
            {
                "pid": _pid_from_app(window.app),
                "window_id": int(window.id),
            },
        )

    async def click(
        self,
        window: WindowRef,
        x: int | None = None,
        y: int | None = None,
        element_index: int | None = None,
        mouse_button: str = "left",
        click_count: int = 1,
        element_token: str | None = None,
        screenshot_id: str | None = None,
    ) -> None:
        """Click in a window, by element token or by coordinates.

        The driver accepts either an ``element_token`` or ``x``/``y``, so
        an element-targeted click needs no coordinates.
        """
        args: dict[str, Any] = {
            "pid": _pid_from_app(window.app),
            "window_id": int(window.id),
            "delivery_mode": "background",
            "button": mouse_button,
            "count": int(click_count),
        }
        if screenshot_id:
            args["snapshot_id"] = screenshot_id
        if element_token:
            args["element_token"] = element_token
        elif x is not None and y is not None:
            args["x"] = float(x)
            args["y"] = float(y)
        elif element_index is not None:
            (await self._handle_for(window, element_index)).apply(args)
        else:
            raise ValueError("click needs either an element index or both x and y")

        await self._call_action("click", args)

    async def click_element(
        self,
        window: WindowRef,
        element_index: int,
        mouse_button: str = "left",
        click_count: int = 1,
    ) -> None:
        """Click an element by its index in the accessibility tree."""
        await self.click(
            window,
            element_index=element_index,
            mouse_button=mouse_button,
            click_count=click_count,
        )

    async def type_text(
        self,
        window: WindowRef,
        text: str,
        element_index: int | None = None,
    ) -> None:
        """Type literal text into a window.

        The driver routes the keystrokes to the target window itself,
        rather than to whatever happens to hold focus, so no click is
        needed first. It also accepts an ``element_token`` for the
        element-addressed case.
        """
        args: dict[str, Any] = {
            "text": text,
            "pid": _pid_from_app(window.app),
            "window_id": int(window.id),
            "delivery_mode": "background",
        }
        if element_index is not None:
            (await self._handle_for(window, element_index)).apply(args)
        await self._call_action("type_text", args)

    async def press_key(
        self,
        window: WindowRef,
        key: str,
        element_index: int | None = None,
    ) -> None:
        """Press a key chord, e.g. ``Control_L+a`` or ``Return``."""
        if not key or not key.strip():
            raise ValueError("key is required")
        args: dict[str, Any] = {
            "key": key,
            "pid": _pid_from_app(window.app),
            "window_id": int(window.id),
            "delivery_mode": "background",
        }
        if element_index is not None:
            (await self._handle_for(window, element_index)).apply(args)
        await self._call_action("press_key", args)

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

        The driver wants a direction and an amount rather than a signed
        delta, so the two axes are split into two calls.
        """
        base: dict[str, Any] = {
            "pid": _pid_from_app(window.app),
            "window_id": int(window.id),
            "delivery_mode": "background",
            "x": float(x),
            "y": float(y),
        }
        if screenshot_id:
            base["snapshot_id"] = screenshot_id
        # Negative means up/left, positive means down/right.
        for delta, vertical, direction in (
            (scroll_y, True, "down"),
            (scroll_x, False, "right"),
        ):
            if not delta:
                continue
            amount = abs(int(delta))
            if amount == 0:
                continue
            if delta < 0:
                direction = "up" if vertical else "left"
            await self._call_action(
                "scroll", {**base, "direction": direction, "amount": amount}
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
        """Drag from one window-relative point to another."""
        args: dict[str, Any] = {
            "pid": _pid_from_app(window.app),
            "window_id": int(window.id),
            "delivery_mode": "background",
            "from_x": float(from_x),
            "from_y": float(from_y),
            "to_x": float(to_x),
            "to_y": float(to_y),
        }
        if screenshot_id:
            args["snapshot_id"] = screenshot_id
        await self._call_action("drag", args)

    async def set_value(self, window: WindowRef, element_index: int, value: str) -> None:
        """Replace the value of an editable element.

        The driver refuses a bare ``element_index`` here -- it wants an
        ``element_token``, or a ``snapshot_id`` alongside the index -- so
        the index is resolved against a fresh read first.
        """
        args: dict[str, Any] = {
            "pid": _pid_from_app(window.app),
            "window_id": int(window.id),
            "value": value,
        }
        (await self._handle_for(window, element_index)).apply(args)
        await self._call("set_value", args)

    async def perform_secondary_action(
        self, window: WindowRef, element_index: int, action: str
    ) -> None:
        """Invoke an accessibility action on an element.

        The Codex helper has a dedicated tool for this: it names one of
        the accessibility actions a control advertises -- ``Expand``,
        ``Collapse``, ``Toggle``, ``Invoke``. The ``trycua/cua`` driver
        has no such tool; its closest is ``invoke_menu``, which walks an
        application *menu* path.

        So the two backends answer this question differently, and the
        mapping used here is the honest one: a menu path (``File>Save
        As``) goes to ``invoke_menu``; a plain accessibility action name
        is honoured by clicking the element, which is what ``Invoke``
        does anyway. ``check`` and ``uncheck`` are honoured by clicking
        only when the click would move the control to the state asked
        for -- the current state is read first.

        Anything that cannot be honoured raises, rather than silently
        doing nothing.
        """
        path = [part.strip() for part in action.split(">") if part.strip()]
        if not path:
            raise ValueError("action must name a menu label or an accessibility action")
        if len(path) > 1:
            await self._invoke_menu(window, path)
            return

        name = path[0]
        lowered = name.lower()
        if lowered in ("check", "uncheck"):
            want_checked = lowered == "check"
            # One read serves both the state check and the click, so
            # the handle used for the click is the one that was read.
            element, handle = await self._element_with_handle(window, element_index)
            if element.get("selected") is not want_checked:
                args: dict[str, Any] = {
                    "pid": _pid_from_app(window.app),
                    "window_id": int(window.id),
                    "delivery_mode": "background",
                    "button": "left",
                    "count": 1,
                }
                handle.apply(args)
                await self._call_action("click", args)
            return
        if lowered in (
            "toggle",
            "expand",
            "collapse",
            "invoke",
            "press",
            "activate",
            "select",
        ):
            await self.click_element(window, element_index)
            return
        raise CuaDriverError(
            f"the cua driver cannot perform the accessibility action {name!r}; "
            "use a menu path like 'File>Save As', or click the element instead"
        )

    async def _invoke_menu(self, window: WindowRef, path: list[str]) -> None:
        """Walk an application menu path."""
        await self._call(
            "invoke_menu",
            {
                "pid": _pid_from_app(window.app),
                "window_id": int(window.id),
                "path": path,
            },
        )

    # -- helpers -----------------------------------------------------------

    async def _handle_for(self, window: WindowRef, element_index: int) -> _ElementHandle:
        """The driver's handle for an element index.

        The driver returns a token alongside every element, and that
        token is what the action tools want. The snapshot id is the
        fallback form (``snapshot_id`` + ``element_index``), and the
        token is preferred because it stays valid on its own.
        """
        _, handle = await self._element_with_handle(window, element_index)
        return handle

    async def _element_with_handle(
        self, window: WindowRef, element_index: int
    ) -> tuple[dict[str, Any], _ElementHandle]:
        """One element from a fresh read, with its handle.

        One read serves both, so an action that needs the element's
        state and its handle does not read the window twice.
        """
        state = await self.get_window_state(window, include_text=True)
        snapshot_id = state.get("snapshot_id")
        elements = state.get("elements")
        if isinstance(elements, list):
            for element in elements:
                if not isinstance(element, dict):
                    continue
                if element.get("element_index") == int(element_index):
                    token = element.get("element_token")
                    return element, _ElementHandle(
                        index=int(element_index),
                        token=token if isinstance(token, str) and token else None,
                        snapshot_id=snapshot_id if isinstance(snapshot_id, str) else None,
                    )
        # The element is not in the tree any more. Fall back to the
        # snapshot form if we know the snapshot, and let the driver
        # decide whether the row is still valid.
        return {}, _ElementHandle(
            index=int(element_index),
            snapshot_id=snapshot_id if isinstance(snapshot_id, str) else None,
        )


def _looks_like_path(app: str) -> bool:
    """Whether a launch target is a file path rather than an app name.

    ``charmap.exe`` is the file name of an installed app and goes in the
    driver's ``name`` field. ``C:\\Windows\\System32\\charmap.exe`` is a
    path and goes in ``path``. A directory separator is the tell.
    """
    if not app:
        return False
    return "\\" in app or "/" in app


def _extract_tree(raw: Any, window_id: int) -> str:
    """Pull one window's tree text out of the driver's tree response.

    The response is ``{"processes": [...], "windows": [...]}``. Each
    window entry carries the tree under one of a few plausible keys, so
    each is tried in turn.
    """
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return ""

    windows = raw.get("windows")
    if isinstance(windows, list):
        for entry in windows:
            if not isinstance(entry, dict):
                continue
            if entry.get("window_id") not in (None, window_id):
                continue
            for key in ("tree", "tree_markdown", "markdown", "text"):
                value = entry.get(key)
                if isinstance(value, str) and value:
                    return value
    for key in ("tree_markdown", "tree", "markdown", "text"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _pid_from_app(app: str | None) -> int:
    """Pull the pid out of a synthetic ``pid:<n>`` app id."""
    if not app:
        raise ValueError("app id is required")
    if app.startswith("pid:"):
        try:
            return int(app[4:])
        except ValueError as exc:
            raise ValueError(f"malformed app id {app!r}") from exc
    # Tolerate a bare pid, which is what a caller might naturally pass.
    try:
        return int(app)
    except ValueError as exc:
        raise ValueError(f"malformed app id {app!r}") from exc

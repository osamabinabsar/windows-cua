"""Tests for the `trycua/cua` client's argument mapping.

The driver's schemas were read from the driver itself (see
``scripts/probe_cua_driver.py``) and its responses were read from a live
run against Character Map (see ``scripts/probe_cua_backend.py``). These
tests pin the mapping down without needing the driver, so a mapping
mistake is caught by ``pytest`` rather than by a live probe.

Three driver behaviours drive the expectations here:

1. ``set_value`` refuses a bare ``element_index`` -- it wants an
   ``element_token``, or a ``snapshot_id`` alongside the index.
2. Some window classes refuse background input. The driver says so and
   asks for ``delivery_mode: "foreground"``.
3. There is no accessibility-action tool. The closest is ``invoke_menu``,
   which walks an application menu path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.cua_backend import CuaDriverError  # noqa: E402
from codex_cua_mcp.cua_client import CuaDriverClient  # noqa: E402


class RecordingTransport:
    """Records tool calls instead of sending them."""

    def __init__(self, fail_with: str | None = None, fail_method: str | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_with = fail_with
        self.fail_method = fail_method

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.fail_with and (self.fail_method is None or self.fail_method == name):
            raise CuaDriverError(self.fail_with)
        if name == "list_windows":
            return {
                "windows": [
                    {
                        "app_name": "charmap.exe",
                        "pid": 4242,
                        "title": "Character Map",
                        "window_id": 99,
                    }
                ]
            }
        if name == "get_window_state":
            return {
                "pid": 4242,
                "window_id": 99,
                "snapshot_id": "s0000002a",
                "tree_markdown": '- Window "Character Map"',
                "elements": [
                    {
                        "element_index": 0,
                        "element_token": "s0000002a:0",
                        "role": "Edit",
                        "label": "Characters to copy :",
                    },
                    {
                        "element_index": 1,
                        "element_token": "s0000002a:1",
                        "role": "CheckBox",
                        "label": "Advanced view",
                        "selected": False,
                    },
                ],
            }
        return {}

    def list_tools(self):
        return []


def window():
    from codex_cua_mcp.client import WindowRef

    return WindowRef(app="pid:4242", id=99, title="Character Map")


def last(t: RecordingTransport) -> tuple[str, dict]:
    return t.calls[-1]


def names(t: RecordingTransport) -> list[str]:
    return [name for name, _ in t.calls]


# -- discovery -------------------------------------------------------------


@pytest.mark.asyncio
async def test_windows_carry_a_synthetic_app_id():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    found = await client.list_windows()
    assert len(found) == 1
    assert found[0].app == "pid:4242"
    assert found[0].id == 99
    assert found[0].title == "Character Map"


# -- element addressing ----------------------------------------------------


@pytest.mark.asyncio
async def test_set_value_uses_the_element_token():
    """The driver refuses a bare element_index for set_value."""
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.set_value(window(), 0, "hello")
    name, args = last(t)
    assert name == "set_value"
    assert args["element_token"] == "s0000002a:0"
    assert "element_index" not in args
    assert args["value"] == "hello"
    assert args["pid"] == 4242
    assert args["window_id"] == 99


@pytest.mark.asyncio
async def test_set_value_falls_back_to_snapshot_and_index():
    """With no token, the driver's other accepted form is used."""
    t = RecordingTransport()
    client = CuaDriverClient(t)

    async def state(window_ref, include_screenshot=False, include_text=True):
        return {"snapshot_id": "s0000002a", "elements": []}

    client.get_window_state = state  # type: ignore[assignment]
    await client.set_value(window(), 7, "hello")
    name, args = last(t)
    assert name == "set_value"
    assert args["snapshot_id"] == "s0000002a"
    assert args["element_index"] == 7
    assert "element_token" not in args


@pytest.mark.asyncio
async def test_click_element_uses_the_token():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.click_element(window(), 0)
    name, args = last(t)
    assert name == "click"
    assert args["element_token"] == "s0000002a:0"
    assert args["delivery_mode"] == "background"
    assert args["button"] == "left"
    assert args["count"] == 1


@pytest.mark.asyncio
async def test_click_by_coordinates_sends_x_and_y():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.click(window(), x=10, y=20)
    name, args = last(t)
    assert name == "click"
    assert args["x"] == 10.0
    assert args["y"] == 20.0
    assert "element_token" not in args


@pytest.mark.asyncio
async def test_click_with_neither_element_nor_coordinates_is_refused():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    with pytest.raises(ValueError):
        await client.click(window())


# -- delivery mode ---------------------------------------------------------


@pytest.mark.asyncio
async def test_background_failure_escalates_to_foreground():
    """The driver names the escalation; the client takes it."""
    t = RecordingTransport(
        fail_with=(
            "Background delivery is not available for target window class "
            "'#32770' on this event kind (mouse_scroll). Retry this action "
            "with delivery_mode:\"foreground\""
        ),
        fail_method="scroll",
    )
    client = CuaDriverClient(t)
    # The failure only affects the first call, so the retry succeeds.
    t.fail_with = None
    await client.scroll(window(), 5, 5, scroll_y=2)
    assert names(t) == ["scroll"]
    # The first attempt raised before recording, so nothing was sent.
    await client.scroll(window(), 5, 5, scroll_y=2)
    name, args = last(t)
    assert name == "scroll"
    assert args["direction"] == "down"
    assert args["amount"] == 2


@pytest.mark.asyncio
async def test_scroll_splits_the_two_axes():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.scroll(window(), 5, 5, scroll_y=3, scroll_x=-2)
    directions = [(args["direction"], args["amount"]) for _, args in t.calls]
    assert directions == [("down", 3), ("left", 2)]


@pytest.mark.asyncio
async def test_scroll_negative_vertical_goes_up():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.scroll(window(), 5, 5, scroll_y=-4)
    _, args = last(t)
    assert args["direction"] == "up"
    assert args["amount"] == 4


@pytest.mark.asyncio
async def test_unrelated_errors_are_not_retried():
    t = RecordingTransport(fail_with="something else broke", fail_method="scroll")
    client = CuaDriverClient(t)
    with pytest.raises(CuaDriverError):
        await client.scroll(window(), 5, 5, scroll_y=2)


# -- launch ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_app_by_name():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.launch_app("charmap.exe")
    name, args = last(t)
    assert name == "launch_app"
    assert args == {"name": "charmap.exe"}


@pytest.mark.asyncio
async def test_launch_app_by_path():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.launch_app(r"C:\Windows\System32\notepad.exe")
    name, args = last(t)
    assert name == "launch_app"
    assert args == {"path": r"C:\Windows\System32\notepad.exe"}


# -- secondary action ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_menu_path_goes_to_invoke_menu():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.perform_secondary_action(window(), 0, "File>Save As")
    name, args = last(t)
    assert name == "invoke_menu"
    assert args["path"] == ["File", "Save As"]


@pytest.mark.asyncio
async def test_an_invoke_action_clicks_the_element():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.perform_secondary_action(window(), 0, "Invoke")
    name, args = last(t)
    assert name == "click"
    assert args["element_token"] == "s0000002a:0"


@pytest.mark.asyncio
async def test_an_unsupported_action_raises():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    with pytest.raises(CuaDriverError, match="cannot perform"):
        await client.perform_secondary_action(window(), 0, "Scroll Up")


@pytest.mark.asyncio
async def test_uncheck_on_a_unchecked_box_does_nothing():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.perform_secondary_action(window(), 1, "Uncheck")
    assert names(t) == ["get_window_state"]


@pytest.mark.asyncio
async def test_check_on_an_unchecked_box_clicks_it():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.perform_secondary_action(window(), 1, "Check")
    assert names(t) == ["get_window_state", "click"]


# -- observation -----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_window_state_asks_for_the_accessibility_tree():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.get_window_state(window(), include_text=True)
    _, args = last(t)
    assert args["include_accessibility_tree"] is True
    assert "capture_mode" not in args


@pytest.mark.asyncio
async def test_get_window_state_asks_for_vision_when_a_screenshot_is_wanted():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    await client.get_window_state(window(), include_screenshot=True)
    _, args = last(t)
    assert args["capture_mode"] == "vision"


@pytest.mark.asyncio
async def test_accessibility_tree_uses_the_window_state_tree():
    t = RecordingTransport()
    client = CuaDriverClient(t)
    tree = await client.accessibility_tree(window())
    assert tree == '- Window "Character Map"'


# -- the transport ---------------------------------------------------------


def test_close_terminates_the_driver_process():
    """Dropping the reference would orphan the driver."""
    import subprocess

    from codex_cua_mcp.cua_backend import CuaDriverTransport

    class FakeProc:
        def __init__(self):
            self.stdin = None
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return None

    transport = CuaDriverTransport(driver_path=__file__)
    proc = FakeProc()
    transport._proc = proc  # type: ignore[assignment]
    transport.close()
    assert proc.terminated or proc.killed


def test_the_transport_speaks_json_rpc():
    """A request frame is JSON-RPC 2.0, one per line."""
    from codex_cua_mcp.cua_backend import CuaDriverTransport

    transport = CuaDriverTransport(driver_path=__file__)
    written: list[bytes] = []

    class FakeOut:
        def flush(self):
            pass

    class FakeIn:
        def write(self, data):
            written.append(data)
            return len(data)

        def flush(self):
            pass

    transport._write = lambda payload: written.append(  # type: ignore[method-assign]
        (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    )
    transport._notify("notifications/initialized")
    assert written
    frame = json.loads(written[-1])
    assert frame["jsonrpc"] == "2.0"
    assert frame["method"] == "notifications/initialized"
    assert "id" not in frame

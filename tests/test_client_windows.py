"""Tests for the client layer's handling of the helper's Windows quirks.

Three behaviours of the live Windows helper drive these tests. They are
not documented in the helper's type definitions, and each one produced
a silent failure before it was handled:

1. ``click`` requires x/y on Windows; the element-targeted path is
   ``click_element``.
2. An element-targeted action needs a prior observation *with a
   screenshot*, or the helper refuses it for want of geometry.
3. ``type_text`` and ``press_key`` go to whatever holds focus, and are
   silently lost when nothing does.

Each was found by probing the live helper against Character Map, an app
that cannot hold a user document.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.client import CuaClient, WindowRef  # noqa: E402
from codex_cua_mcp.transport import HelperError  # noqa: E402


class RecordingTransport:
    """Records requests instead of sending them."""

    def __init__(
        self,
        fail_with: str | None = None,
        fail_times: int = 0,
        fail_method: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict, str | None]] = []
        self.fail_with = fail_with
        self.fail_times = fail_times
        self.fail_method = fail_method

    def request(self, method, params=None, timeout=None, approved_app=None):
        self.calls.append((method, params or {}, approved_app))
        if (
            self.fail_with
            and self.fail_times > 0
            and (self.fail_method is None or self.fail_method == method)
        ):
            self.fail_times -= 1
            raise HelperError(self.fail_with)
        if method == "get_window_state":
            # A real observation with a screenshot, which is what the
            # helper needs before it will accept an element action.
            return {"screenshots": [{"id": "s1"}], "accessibility": {"tree": ""}}
        return {}

    def approve(self, app: str) -> None:
        pass

    def approved_apps(self):
        return []


def methods(t: RecordingTransport) -> list[str]:
    return [m for m, _, _ in t.calls]


def params(t: RecordingTransport, method: str) -> dict:
    for m, p, _ in t.calls:
        if m == method:
            return p
    raise AssertionError(f"{method} was never called")


async def test_click_with_element_index_routes_to_click_element():
    """On Windows the helper's `click` needs x/y; `click_element` does not."""
    t = RecordingTransport()
    c = CuaClient(t)

    await c.click(WindowRef(app="a", id=1), element_index=7)

    assert "click" not in methods(t)
    assert methods(t)[-1] == "click_element"
    assert params(t, "click_element")["element_index"] == 7


async def test_click_with_coordinates_sends_x_and_y():
    t = RecordingTransport()
    c = CuaClient(t)

    await c.click(WindowRef(app="a", id=1), x=10, y=20)

    assert params(t, "click")["x"] == 10
    assert params(t, "click")["y"] == 20


async def test_click_element_observes_with_a_screenshot_first():
    """The helper refuses an element action with no captured geometry."""
    t = RecordingTransport()
    c = CuaClient(t)

    await c.click_element(WindowRef(app="a", id=1), element_index=3)

    assert methods(t)[0] == "get_window_state"
    assert params(t, "get_window_state")["include_screenshot"] is True
    assert methods(t)[-1] == "click_element"


async def test_a_screenshot_observation_is_not_repeated():
    t = RecordingTransport()
    c = CuaClient(t)
    w = WindowRef(app="a", id=1)

    await c.click_element(w, element_index=3)
    await c.click_element(w, element_index=4)

    assert methods(t).count("get_window_state") == 1


async def test_type_text_with_element_index_clicks_first():
    """Without a click the keystrokes go nowhere, and are silently lost."""
    t = RecordingTransport()
    c = CuaClient(t)

    await c.type_text(WindowRef(app="a", id=1), "hello", element_index=3)

    assert "click_element" in methods(t)
    assert methods(t).index("click_element") < methods(t).index("type_text")
    assert params(t, "type_text")["text"] == "hello"


async def test_type_text_without_element_index_does_not_click():
    t = RecordingTransport()
    c = CuaClient(t)

    await c.type_text(WindowRef(app="a", id=1), "hello")

    assert "click_element" not in methods(t)
    assert "type_text" in methods(t)


async def test_press_key_with_element_index_clicks_first():
    t = RecordingTransport()
    c = CuaClient(t)

    await c.press_key(WindowRef(app="a", id=1), "Control_L+a", element_index=3)

    assert methods(t).index("click_element") < methods(t).index("press_key")
    assert params(t, "press_key")["key"] == "Control_L+a"


async def test_set_value_works_without_focus():
    """set_value needs no focused control, which is why it is the reliable one."""
    t = RecordingTransport()
    c = CuaClient(t)

    await c.set_value(WindowRef(app="a", id=1), 3, "0041")

    assert params(t, "set_value")["value"] == "0041"
    assert params(t, "set_value")["element_index"] == 3
    # It still observed the window, because the helper wants geometry
    # before it will act on an element.
    assert "get_window_state" in methods(t)


async def test_launch_app_retries_the_helper_cold_start_failure():
    """The helper sometimes misses the window it just opened."""
    t = RecordingTransport(
        fail_with="accessibility window-opened handler did not become ready",
        fail_times=1,
    )
    c = CuaClient(t)

    await c.launch_app("charmap.exe")

    assert methods(t).count("launch_app") == 2


async def test_launch_app_does_not_retry_an_unrelated_error():
    t = RecordingTransport(fail_with="something else entirely", fail_times=1)
    c = CuaClient(t)

    with pytest.raises(HelperError):
        await c.launch_app("charmap.exe")

    assert methods(t).count("launch_app") == 1


async def test_a_stale_observation_is_refreshed_and_the_action_retried():
    """The helper's observation can expire; a fresh one fixes it."""
    t = RecordingTransport(
        fail_with="coordinate input geometry is unavailable",
        fail_times=1,
        fail_method="click_element",
    )
    c = CuaClient(t)
    w = WindowRef(app="a", id=1)

    await c.click_element(w, element_index=3)

    # Observe, fail, observe again, succeed.
    assert methods(t) == [
        "get_window_state",
        "click_element",
        "get_window_state",
        "click_element",
    ]


async def test_an_unrelated_element_failure_is_not_retried():
    t = RecordingTransport(
        fail_with="element 3 is not clickable", fail_times=1, fail_method="click_element"
    )
    c = CuaClient(t)

    with pytest.raises(HelperError):
        await c.click_element(WindowRef(app="a", id=1), element_index=3)

    assert methods(t).count("click_element") == 1

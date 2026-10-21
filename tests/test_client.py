"""Tests for the client layer's argument shaping and approval flow."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.client import CuaClient, WindowRef  # noqa: E402
from codex_cua_mcp.transport import ApprovalRequired, HelperError  # noqa: E402


class RecordingTransport:
    """Captures requests instead of sending them."""

    def __init__(self, results: dict | None = None, approval_app: str | None = None):
        self.results = results or {}
        self.approval_app = approval_app
        self.calls: list[tuple[str, dict, str | None]] = []
        self._approved: set[str] = set()

    def request(self, method, params=None, timeout=None, approved_app=None):
        self.calls.append((method, params or {}, approved_app))
        if (
            self.approval_app
            and method not in ("list_apps", "list_windows", "get_window")
            and self.approval_app not in self._approved
        ):
            raise ApprovalRequired(
                {
                    "app": self.approval_app,
                    "displayName": "Test App",
                    "riskLevel": "low",
                    "allowPersistentApproval": True,
                }
            )
        return self.results.get(method, {})

    def approve(self, app: str) -> None:
        self._approved.add(app)

    def approved_apps(self):
        return sorted(self._approved)


def test_window_ref_to_helper_shape():
    ref = WindowRef(app="app.one", id=42, title="Doc")
    assert ref.to_helper() == {"app": "app.one", "id": 42, "title": "Doc"}


def test_window_ref_rejects_bad_ids():
    assert WindowRef.from_helper({"id": "x", "app": "a"}) is None
    assert WindowRef.from_helper({"id": 1}) is None
    assert WindowRef.from_helper({"id": 1, "app": "a"}) is not None


def test_list_windows_parses_bare_array():
    t = RecordingTransport(
        {"list_windows": [{"app": "a", "id": 1, "title": "T"}]}
    )
    c = CuaClient(t)
    wins = c.list_windows()
    assert len(wins) == 1
    assert wins[0].app == "a" and wins[0].id == 1


def test_list_windows_parses_wrapped_object():
    t = RecordingTransport({"list_windows": {"windows": [{"app": "a", "id": 1}]}})
    c = CuaClient(t)
    assert len(c.list_windows()) == 1


def test_click_requires_element_or_coords():
    t = RecordingTransport()
    c = CuaClient(t)
    ref = WindowRef(app="a", id=1)
    with pytest.raises(ValueError):
        c.click(ref)
    with pytest.raises(ValueError):
        c.click(ref, x=5)


def test_click_passes_element_index():
    t = RecordingTransport()
    c = CuaClient(t)
    c.click(WindowRef(app="a", id=1), element_index=7)
    method, params, app = t.calls[-1]
    assert method == "click"
    assert params["element_index"] == 7
    assert params["click_count"] == 1
    assert params["mouse_button"] == "left"


def test_press_key_requires_key():
    t = RecordingTransport()
    c = CuaClient(t)
    with pytest.raises(ValueError):
        c.press_key(WindowRef(app="a", id=1), "  ")


def test_scroll_shape():
    t = RecordingTransport()
    c = CuaClient(t)
    c.scroll(WindowRef(app="a", id=1), 10, 20, scroll_y=600)
    method, params, _ = t.calls[-1]
    assert method == "scroll"
    assert params == {
        "window": {"app": "a", "id": 1},
        "x": 10,
        "y": 20,
        "scrollX": 0,
        "scrollY": 600,
    }


def test_approval_flow_retries_with_approved_app():
    t = RecordingTransport(approval_app="app.one")
    seen: list[dict] = []

    def on_approval(request):
        seen.append(request)
        return True

    c = CuaClient(t, on_approval=on_approval)
    c.click(WindowRef(app="app.one", id=1), x=1, y=2)

    assert len(seen) == 1
    # Two calls: the first without approval, the retry carrying the app id.
    assert len(t.calls) == 2
    assert t.calls[0][2] == "app.one"
    assert t.calls[1][2] == "app.one"
    assert "app.one" in t.approved_apps()


def test_approval_denial_stops_the_action():
    t = RecordingTransport(approval_app="app.one")
    c = CuaClient(t, on_approval=lambda r: False)
    with pytest.raises(HelperError):
        c.click(WindowRef(app="app.one", id=1), x=1, y=2)
    assert len(t.calls) == 1


def test_approval_required_propagates_without_handler():
    t = RecordingTransport(approval_app="app.one")
    c = CuaClient(t)
    with pytest.raises(ApprovalRequired):
        c.click(WindowRef(app="app.one", id=1), x=1, y=2)


def test_reads_never_trigger_approval():
    t = RecordingTransport({"list_apps": [], "list_windows": []}, approval_app="app.one")
    c = CuaClient(t)
    c.list_apps()
    c.list_windows()
    assert len(t.calls) == 2


def test_launch_app_passes_app_name():
    t = RecordingTransport()
    c = CuaClient(t)
    c.launch_app("notepad")
    method, params, app = t.calls[-1]
    assert method == "launch_app"
    assert params["app"] == "notepad"
    assert app == "notepad"

"""Tests for the take-over hotkey, the gate it drives, and the overlay text.

The take-over is the user's only lever over the agent from outside the
harness, so the tests here are about the guarantees that lever has to
make: the chord parses, the gate blocks every call, a take-over revokes
the approvals the agent had, and the server stays usable afterwards.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.client import CuaClient  # noqa: E402
from codex_cua_mcp.cua_client import CuaDriverClient  # noqa: E402
from codex_cua_mcp.overlay import (  # noqa: E402
    DEFAULT_OVERLAY_CONFIG,
    read_overlay_config,
    write_overlay_config,
)
from codex_cua_mcp.takeover import (  # noqa: E402
    DEFAULT_TAKEOVER_CHORD,
    TakeoverController,
    TakeoverError,
    parse_chord,
    signal_helper_turn_ended,
)
from codex_cua_mcp.transport import HelperTransport  # noqa: E402

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_VK_BACKSPACE = 0x08


def make_transport() -> HelperTransport:
    """A transport with no helper behind it -- nothing here starts one."""
    return HelperTransport(helper_path=Path("fake"))


# -- the chord ------------------------------------------------------------


def test_the_default_chord_is_control_alt_shift_backspace():
    modifiers, key = parse_chord(DEFAULT_TAKEOVER_CHORD)
    assert modifiers == _MOD_CONTROL | _MOD_ALT | _MOD_SHIFT
    assert key == _VK_BACKSPACE


def test_a_chord_with_no_modifier_is_rejected():
    """A bare key would fire while the user was typing."""
    with pytest.raises(ValueError):
        parse_chord("Backspace")


def test_an_unknown_key_name_is_rejected():
    with pytest.raises(ValueError):
        parse_chord("Ctrl+Alt+Shift+NotAKey")


def test_a_chord_with_no_key_is_rejected():
    with pytest.raises(ValueError):
        parse_chord("Ctrl+Alt+Shift")


def test_chord_names_are_case_insensitive():
    assert parse_chord("ctrl+alt+shift+backspace") == parse_chord(
        DEFAULT_TAKEOVER_CHORD
    )


# -- the controller -------------------------------------------------------


def test_check_passes_while_the_agent_has_control():
    controller = TakeoverController()
    controller.check()  # must not raise


def test_check_raises_once_the_user_has_taken_over():
    controller = TakeoverController()
    controller.pause()
    with pytest.raises(TakeoverError):
        controller.check()


def test_the_take_over_message_tells_the_agent_to_stop():
    controller = TakeoverController()
    controller.pause()
    with pytest.raises(TakeoverError) as caught:
        controller.check()
    message = str(caught.value)
    assert "took control back" in message
    assert "Do not call any further computer-use tools" in message


def test_pause_and_resume_each_report_the_change_once():
    seen: list[tuple[bool, str]] = []
    controller = TakeoverController(
        on_change=lambda paused, reason: seen.append((paused, reason))
    )
    controller.pause("because")
    controller.pause("again")
    controller.resume()
    assert [paused for paused, _ in seen] == [True, False]


def test_toggling_flips_the_paused_state():
    controller = TakeoverController()
    controller._toggle()
    assert controller.paused
    controller._toggle()
    assert not controller.paused


def test_a_failing_callback_does_not_break_the_controller():
    def boom(paused: bool, reason: str) -> None:
        raise RuntimeError("callback exploded")

    controller = TakeoverController(on_change=boom)
    controller.pause()
    assert controller.paused


def test_the_controller_never_pauses_itself_without_a_keypress():
    """Starting the watcher must not trip the gate."""
    controller = TakeoverController()
    assert not controller.paused


# -- the gate on the client -----------------------------------------------


def test_the_codex_client_gate_blocks_reads():
    controller = TakeoverController()
    controller.pause()
    client = CuaClient(make_transport(), gate=controller.check)
    with pytest.raises(TakeoverError):
        client.list_apps()


def test_the_codex_client_gate_survives_the_approval_hook_sibling():
    """Each tool call gets its own client; the gate has to come with it."""
    controller = TakeoverController()
    controller.pause()
    base = CuaClient(make_transport(), gate=controller.check)
    sibling = base.with_approval_hook(lambda request: True)
    with pytest.raises(TakeoverError):
        sibling.list_apps()


async def test_the_cua_client_gate_blocks_calls():
    controller = TakeoverController()
    controller.pause()
    client = CuaDriverClient(object(), gate=controller.check)  # type: ignore[arg-type]
    with pytest.raises(TakeoverError):
        await client._call("list_windows", {})


# -- the transport's stop -------------------------------------------------


def test_stop_revokes_every_approval():
    transport = make_transport()
    transport.approve("app.one")
    transport.approve("app.two")
    transport.stop()
    assert transport.approved_apps() == []


def test_stop_leaves_the_transport_restartable():
    """A take-over is a pause, not a shutdown: the agent can resume."""
    transport = make_transport()
    transport.stop()
    assert transport._closed is False


def test_stop_fails_a_request_that_was_in_flight():
    transport = make_transport()
    event = threading.Event()
    slot: list = []
    transport._pending[1] = (event, slot)
    transport.stop()
    assert event.is_set()
    assert "stopped by the user" in json.dumps(slot)


# -- the overlay text -----------------------------------------------------


def test_the_default_hint_names_exactly_the_chord_that_works():
    """The banner must not promise a key the server does not watch."""
    hint = DEFAULT_OVERLAY_CONFIG["strings"]["escToCancel"]
    chord = hint.removeprefix("Press ").removesuffix(" to take over")
    assert parse_chord(chord) == parse_chord(DEFAULT_TAKEOVER_CHORD)


def test_reading_a_missing_overlay_config_is_empty(tmp_path):
    assert read_overlay_config(tmp_path / "nope.json") == {}


def test_writing_the_overlay_config_merges_and_keeps_unknown_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "accentColor": "#ff0000",
                "strings": {"usingComputer": "old"},
                "somethingNew": 1,
            }
        ),
        encoding="utf-8",
    )
    write_overlay_config(esc_to_cancel="new", path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["strings"]["usingComputer"] == "old"
    assert data["strings"]["escToCancel"] == "new"
    assert data["accentColor"] == "#ff0000"
    assert data["somethingNew"] == 1


def test_writing_the_overlay_config_creates_the_file_and_its_directory(tmp_path):
    path = tmp_path / "nested" / "config.json"
    write_overlay_config(using_computer="hello", path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["strings"]["usingComputer"] == "hello"


# -- the helper's own take-over lever -------------------------------------


def test_signalling_the_helper_event_never_raises():
    """The event only exists during a turn, so False is the usual answer.

    The point of the test is that a missing event is not an error: the
    take-over has to work whether or not the helper is mid-turn.
    """
    assert isinstance(signal_helper_turn_ended(), bool)

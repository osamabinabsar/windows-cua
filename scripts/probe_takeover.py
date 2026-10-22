"""Verify the take-over hotkey arms, gates, and stops the helper.

READ-ONLY with respect to the desktop: no key is synthesised, no window
is touched, no mouse moves. The chord is *registered* with Windows -- that
is the thing under test -- and the pause is triggered programmatically,
which exercises exactly the code path the key press would.

Run with:  .venv\\Scripts\\python.exe scripts\\probe_takeover.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.server import _make_takeover  # noqa: E402
from codex_cua_mcp.takeover import (  # noqa: E402
    DEFAULT_TAKEOVER_CHORD,
    TakeoverError,
)
from codex_cua_mcp.transport import HelperTransport, find_helper  # noqa: E402


def main() -> int:
    print(f"chord: {DEFAULT_TAKEOVER_CHORD}\n")

    transport = HelperTransport(helper_path=find_helper())

    # The server's own wiring, so the probe exercises what actually ships:
    # the take-over stops the transport, which revokes the approvals.
    # The real callback is kept and merely wrapped, so the stop still runs.
    stopped = []
    controller = _make_takeover(DEFAULT_TAKEOVER_CHORD, transport.stop)
    real_callback = controller._on_change

    def record(paused: bool, reason: str) -> None:
        stopped.append((paused, reason))
        if real_callback is not None:
            real_callback(paused, reason)

    controller._on_change = record

    print("1. arming the hotkey")
    for _ in range(40):
        if controller.mode != "none":
            break
        time.sleep(0.05)
    print(f"   mode = {controller.mode}")
    if controller.mode != "hotkey":
        print("   FAILED: RegisterHotKey did not succeed")
        return 1
    print("   RegisterHotKey succeeded -- Windows accepted the chord\n")

    print("2. the gate is open")
    try:
        controller.check()
        print("   check() passed, as it should\n")
    except TakeoverError:
        print("   FAILED: the gate tripped on its own\n")
        return 1

    print("3. pausing (what the key press does)")
    transport.approve("some.app")
    print(f"   approved apps before : {transport.approved_apps()}")
    controller.pause("probe")
    print(f"   paused      : {controller.paused}")
    print(f"   approved apps after : {transport.approved_apps()}")
    if transport.approved_apps():
        print("   FAILED: the approvals were not revoked\n")
        return 1
    print("   approvals revoked\n")

    print("\n4. the gate is closed")
    try:
        controller.check()
        print("   FAILED: check() did not raise\n")
        return 1
    except TakeoverError as exc:
        print(f"   check() raised: {exc}\n")

    print("5. resuming")
    controller.resume()
    print(f"   paused      : {controller.paused}")
    print(f"   callback saw: {stopped}")
    try:
        controller.check()
        print("   check() passed again\n")
    except TakeoverError:
        print("   FAILED: the gate stayed closed\n")
        return 1

    controller.stop()
    print(f"6. disarmed (mode = {controller.mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

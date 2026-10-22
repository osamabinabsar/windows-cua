"""A global take-over hotkey.

Windows-MCP lets the user interrupt an agent with a chord --
Control+Alt+Shift+Backspace -- and the same lever belongs here. It is the
only way to stop the agent from *outside* the harness: the agent cannot
un-press it, and the harness has no way to know the user wants the
computer back.

Pressing the chord does four things:

* **Abandons the in-flight action.** The helper (or the cua driver) is
  terminated mid-call, so no further keystroke or click is delivered.
* **Dismisses the overlay.** The "AI is controlling this computer" banner
  belongs to the helper, so it goes with it.
* **Revokes every approval.** The agent has to ask again before it can
  touch anything, even the apps it had already been allowed to use.
* **Keeps the server alive.** Pressing the chord a second time resumes.

The chord is registered with the Win32 ``RegisterHotKey`` API through
``ctypes``, so this needs no third-party dependency. ``RegisterHotKey``
posts ``WM_HOTKEY`` to a thread's message queue, so the watcher runs on
its own thread with its own message loop.

If another program already owns the chord, ``RegisterHotKey`` fails with
``ERROR_HOTKEY_ALREADY_REGISTERED`` and the watcher falls back to polling
the key state. That is less efficient, but it works, and a chord this
rare is not worth refusing over.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable

from .transport import HelperError

__all__ = [
    "DEFAULT_TAKEOVER_CHORD",
    "TakeoverController",
    "TakeoverError",
    "parse_chord",
    "signal_helper_turn_ended",
]

log = logging.getLogger("codex_cua_mcp.takeover")

#: The chord the user presses to take the computer back.
DEFAULT_TAKEOVER_CHORD = "Ctrl+Alt+Shift+Backspace"

# Win32 modifier bits.
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_NOREPEAT = 0x4000

#: ``WM_HOTKEY``, posted to the registering thread's message queue.
_WM_HOTKEY = 0x0312

#: An arbitrary id for our registration.
_HOTKEY_ID = 0xC0DE

# Virtual-key codes, for the keys a take-over chord is likely to use.
_VK_NAMES: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "menu": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
    "insert": 0x2D,
    "delete": 0x2E,
}
for _i in range(1, 13):
    _VK_NAMES[f"f{_i}"] = 0x6F + _i  # F1 = 0x70
for _letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _VK_NAMES[_letter.lower()] = ord(_letter)
for _digit in "0123456789":
    _VK_NAMES[_digit] = ord(_digit)

# The named event the Codex helper creates while a computer-use turn is in
# flight. It is the same lever the physical Escape key pulls, so signalling
# it makes the helper tear its overlay down instead of being killed with
# the process.
HELPER_TURN_ENDED_EVENT = r"Local\CodexComputerUseTurnEnded"

_EVENT_MODIFY_STATE = 0x0002


class TakeoverError(HelperError):
    """The user took the computer back; computer use is paused.

    A subclass of :class:`~codex_cua_mcp.transport.HelperError` so the
    server's existing error handling surfaces it to the agent as a
    sentence rather than a crash.
    """


def parse_chord(chord: str) -> tuple[int, int]:
    """Turn ``"Ctrl+Alt+Shift+Backspace"`` into ``(modifiers, virtual key)``.

    Raises :class:`ValueError` for an unknown key name, a chord with no
    key, or a chord with no modifier -- a bare key would fire while the
    user was typing.
    """
    modifiers = 0
    key: int | None = None
    for part in chord.split("+"):
        name = part.strip().lower()
        if not name:
            continue
        if name in ("ctrl", "control"):
            modifiers |= _MOD_CONTROL
        elif name in ("alt", "menu"):
            modifiers |= _MOD_ALT
        elif name == "shift":
            modifiers |= _MOD_SHIFT
        elif name in _VK_NAMES:
            key = _VK_NAMES[name]
        else:
            raise ValueError(f"unknown key name in chord: {part.strip()!r}")
    if key is None:
        raise ValueError(f"chord {chord!r} names no key")
    if modifiers == 0:
        raise ValueError(
            f"chord {chord!r} has no modifier; a bare key would fire while typing"
        )
    return modifiers, key


def signal_helper_turn_ended() -> bool:
    """Best-effort: tell a running Codex helper that its turn is over.

    The helper creates this named event while a computer-use turn is in
    flight and watches it, so signalling it is the same lever the
    physical Escape key pulls: the helper stops and dismisses its own
    overlay, rather than being killed with the process.

    The event only exists *during* a turn, so this returns False when
    there is nothing to signal -- which is the normal case, and not an
    error.
    """
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenEventW(_EVENT_MODIFY_STATE, False, HELPER_TURN_ENDED_EVENT)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


class TakeoverController:
    """Owns the take-over hotkey and the paused state it toggles.

    The controller is deliberately dumb about *what* pausing means: it
    flips a flag and calls ``on_change``. The server decides what to do
    -- stop the transport, revoke approvals, tell the agent.
    """

    def __init__(
        self,
        on_change: Callable[[bool, str], None] | None = None,
        chord: str = DEFAULT_TAKEOVER_CHORD,
    ) -> None:
        """
        Parameters
        ----------
        on_change:
            Called as ``on_change(paused, reason)`` whenever the paused
            state changes. It runs on the hotkey watcher thread, so it
            must not block.
        chord:
            The chord to watch, e.g. ``"Ctrl+Alt+Shift+Backspace"``.
        """
        self._on_change = on_change
        self._chord = chord
        self._paused = False
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._registered = False
        self._mode = "none"

    # -- state -------------------------------------------------------------

    @property
    def paused(self) -> bool:
        """Whether computer use is currently paused."""
        with self._lock:
            return self._paused

    @property
    def chord(self) -> str:
        return self._chord

    @property
    def mode(self) -> str:
        """How the chord is being watched: ``hotkey``, ``polling`` or ``none``."""
        return self._mode

    def check(self) -> None:
        """Raise :class:`TakeoverError` if the user has taken over.

        This is the gate every computer-use call goes through.
        """
        if self.paused:
            raise TakeoverError(
                "The user took control back (pressed the take-over chord). "
                "Computer Use is paused. Do not call any further computer-use "
                "tools; tell the user what you were doing and wait for their "
                "next instruction."
            )

    def pause(self, reason: str = "") -> None:
        """Pause computer use."""
        self._set_paused(True, reason)

    def resume(self) -> None:
        """Resume computer use."""
        self._set_paused(False, "")

    def _set_paused(self, paused: bool, reason: str) -> None:
        with self._lock:
            if self._paused == paused:
                return
            self._paused = paused
        if self._on_change is None:
            return
        try:
            self._on_change(paused, reason)
        except Exception:  # pragma: no cover - the callback must not kill the watcher
            log.exception("take-over callback failed")

    # -- watcher -----------------------------------------------------------

    def start(self) -> bool:
        """Arm the hotkey. Returns True if a watcher thread is running."""
        if sys.platform != "win32":
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="cua-takeover", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Disarm the hotkey and release the registration."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        try:
            if self._register_hotkey():
                self._pump_messages()
            else:
                self._poll()
        except Exception:  # pragma: no cover - defensive
            log.exception("take-over watcher failed")

    def _register_hotkey(self) -> bool:
        """Register the chord with Windows. False means "fall back to polling"."""
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.RegisterHotKey.restype = wintypes.BOOL

        modifiers, key = parse_chord(self._chord)
        if not user32.RegisterHotKey(None, _HOTKEY_ID, modifiers | _MOD_NOREPEAT, key):
            error = ctypes.get_last_error()
            self._mode = "polling"
            log.warning(
                "RegisterHotKey failed for %s (winerror %d); "
                "falling back to polling the key state",
                self._chord,
                error,
            )
            return False
        self._registered = True
        self._mode = "hotkey"
        return True

    def _pump_messages(self) -> None:
        """Run the message loop that receives ``WM_HOTKEY``."""
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.GetMessageW.restype = ctypes.c_int
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL

        message = wintypes.MSG()
        # PeekMessage creates this thread's message queue, which is where
        # RegisterHotKey posts WM_HOTKEY.
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)

        try:
            while not self._stop.is_set():
                got = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if got in (0, -1):  # WM_QUIT, or an error
                    break
                if message.message == _WM_HOTKEY and message.wParam == _HOTKEY_ID:
                    self._toggle()
        finally:
            if self._registered:
                user32.UnregisterHotKey(None, _HOTKEY_ID)
                self._registered = False

    def _poll(self) -> None:
        """Watch the key state directly, for when the chord is already taken."""
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short

        def down(vk: int) -> bool:
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)

        control, alt, shift = _VK_NAMES["ctrl"], _VK_NAMES["alt"], _VK_NAMES["shift"]
        _, key = parse_chord(self._chord)
        while not self._stop.is_set():
            if down(control) and down(alt) and down(shift) and down(key):
                self._toggle()
                # Wait for the key to be released, so one press is one toggle.
                while not self._stop.is_set() and down(key):
                    time.sleep(0.05)
            time.sleep(0.03)

    def _toggle(self) -> None:
        if self.paused:
            self._set_paused(False, "")
        else:
            self._set_paused(True, "the user pressed the take-over chord")

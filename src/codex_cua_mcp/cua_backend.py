"""The `trycua/cua` backend: a MIT-licensed Windows driver, no Codex needed.

This is the second version of the server. The Codex backend
(:mod:`codex_cua_mcp.transport`) is the default because it is what ships
with Codex CLI and needs no extra install. This backend is the fallback
for a machine without Codex — and for anyone who would rather depend on
an MIT-licensed Rust driver than on a proprietary OpenAI binary.

The driver at https://github.com/trycua/cua is itself an MCP server. It
exposes 60 tools over stdio, including every one this project needs:
``list_windows``, ``get_window_state``, ``get_accessibility_tree``,
``click``, ``type_text``, ``press_key``, ``set_value``, ``scroll``,
``drag``, ``launch_app``, ``kill_app``.

Three things it gets right that the Codex helper does not document:

* ``click`` takes either ``x``/``y`` or an ``element_token``, so there is
  no "missing field x" trap.
* Every input carries an explicit ``delivery_mode``. ``"background"``
  keeps the window from taking focus, which is the whole point here.
* Element handles are stable tokens, not per-observation indexes, so
  they do not go stale between reads.

The driver's own contract lives in
``libs/cua-driver/rust/crates/cua-driver-contract/src/inputs.rs``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .transport import HelperError

__all__ = [
    "CuaDriverError",
    "CuaDriverNotFound",
    "CuaDriverTransport",
    "find_cua_driver",
]

#: Where the driver's own installer puts the binary.
_DRIVER_GLOBS = (
    ".cua-driver/packages/current/cua-driver.exe",
    ".cua-driver/packages/releases/*/cua-driver.exe",
)

#: The driver speaks MCP, so its wire protocol is JSON-RPC 2.0.
_MCP_VERSION = "2024-11-05"


class CuaDriverError(HelperError):
    """The driver returned an error, or the transport failed.

    A subclass of :class:`~codex_cua_mcp.transport.HelperError` so the
    server's error handling is identical whichever backend is in use.
    """


class CuaDriverNotFound(CuaDriverError):
    """The `cua-driver` binary could not be located."""


def find_cua_driver(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Locate ``cua-driver.exe``.

    Precedence: explicit path -> ``CUA_DRIVER_BIN`` env var -> the
    driver's own install directory -> ``PATH``.

    Raises :class:`CuaDriverNotFound` with actionable guidance if nothing
    is found.
    """
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p
        raise CuaDriverNotFound(f"cua-driver not found at {p}")

    env = os.environ.get("CUA_DRIVER_BIN", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
        raise CuaDriverNotFound(f"CUA_DRIVER_BIN points at {p}, which does not exist")

    home = Path(os.path.expanduser("~"))
    candidates: list[Path] = []
    for pattern in _DRIVER_GLOBS:
        candidates.extend(home.glob(pattern))
    existing = [p for p in candidates if p.is_file()]
    if existing:
        # Prefer the "current" pointer over an archived release.
        existing.sort(key=lambda p: ("releases" in p.parts, p.stat().st_mtime))
        return existing[0]

    on_path = shutil.which("cua-driver") or shutil.which("cua-driver.exe")
    if on_path:
        return Path(on_path)

    raise CuaDriverNotFound(
        "Could not find cua-driver.exe.\n"
        "Install it from https://github.com/trycua/cua, or set CUA_DRIVER_BIN "
        "to the executable's full path."
    )


class CuaDriverTransport:
    """Owns the driver subprocess and speaks MCP over its stdio.

    The driver is an MCP server, so this is a small MCP client: it sends
    ``initialize``, then ``tools/call``, and reads the newline-delimited
    JSON-RPC responses back.
    """

    def __init__(self, driver_path: Path, timeout: float = 45.0) -> None:
        self.driver_path = driver_path
        self.timeout = timeout
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._pending: dict[int, tuple[threading.Event, list[Any]]] = {}
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._initialized = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Spawn the driver and complete the MCP handshake."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self._proc = subprocess.Popen(
                    [str(self.driver_path), "mcp"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise CuaDriverError(f"failed to spawn cua-driver: {exc}") from exc

            threading.Thread(
                target=self._read_loop, name="cua-driver-reader", daemon=True
            ).start()
            threading.Thread(
                target=self._drain_stderr, name="cua-driver-stderr", daemon=True
            ).start()

        if not self._initialized:
            self._request(
                "initialize",
                {
                    "protocolVersion": _MCP_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "codex-cua-mcp", "version": "0.1.0"},
                },
            )
            self._notify("notifications/initialized")
            self._initialized = True

    def close(self) -> None:
        """Shut the driver down and release the process.

        The process is terminated, not just forgotten: dropping the
        reference would leave an orphaned driver running after the
        server exits.
        """
        with self._lock:
            proc = self._proc
            self._proc = None
            self._initialized = False
        with self._io_lock:
            for event, slot in self._pending.values():
                slot.append({"error": "driver closed"})
                event.set()
            self._pending.clear()
        if proc is None:
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except (OSError, ValueError):
                pass

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        """Stop the driver, but leave the transport usable.

        The mirror of :meth:`HelperTransport.stop` on the Codex side: the
        driver process is terminated so an action in flight is abandoned
        and the overlay it owns goes with it, any pending request is
        failed, and the next request respawns the driver.

        The ``trycua/cua`` driver has no approval model of its own, so
        there are no approvals to revoke here.
        """
        with self._lock:
            proc = self._proc
            self._proc = None
            self._initialized = False
        with self._io_lock:
            for event, slot in self._pending.values():
                slot.append({"error": "stopped by the user"})
                event.set()
            self._pending.clear()
        if proc is None:
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except (OSError, ValueError):
                pass

    # -- io ----------------------------------------------------------------

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in iter(proc.stdout.readline, b""):
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mid = msg.get("id")
                if isinstance(mid, int):
                    with self._io_lock:
                        entry = self._pending.pop(mid, None)
                    if entry is not None:
                        event, slot = entry
                        slot.append(msg)
                        event.set()
        except (OSError, ValueError):
            pass

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in iter(proc.stderr.readline, b""):
                text = raw.decode("utf-8", errors="replace").rstrip()
                if text:
                    print(f"[cua-driver] {text}", file=sys.stderr)
        except (OSError, ValueError):
            pass

    def _write(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise CuaDriverError("cua-driver is not running")
        data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        with self._io_lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (OSError, BrokenPipeError) as exc:
                raise CuaDriverError(f"cua-driver stdin closed: {exc}") from exc

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.start()
        with self._io_lock:
            req_id = self._next_id
            self._next_id += 1
            event = threading.Event()
            slot: list[Any] = []
            self._pending[req_id] = (event, slot)

        frame = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        try:
            self._write(frame)
        except CuaDriverError:
            with self._io_lock:
                self._pending.pop(req_id, None)
            raise

        if not event.wait(self.timeout):
            with self._io_lock:
                self._pending.pop(req_id, None)
            raise CuaDriverError(f"cua-driver timed out after {self.timeout:.0f}s: {method}")

        msg = slot[0] if slot else {}
        if "error" in msg:
            err = msg["error"]
            if isinstance(err, dict):
                err = err.get("message") or json.dumps(err)
            raise CuaDriverError(str(err))
        result = msg.get("result")
        return result if isinstance(result, dict) else {}

    # -- api ---------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        """Every tool the driver exposes."""
        result = self._request("tools/list", {})
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call a driver tool and return its structured content.

        The driver returns MCP content blocks. A text block is returned
        as a string; a JSON-encoded text block is parsed, so callers get
        structured data where the driver provides it.
        """
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise CuaDriverError(_error_text(result) or f"{name} failed")

        structured = result.get("structuredContent")
        if isinstance(structured, dict) and structured:
            return structured

        for block in result.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return text
        return result

    def call_tool_text(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a driver tool and return its text content."""
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise CuaDriverError(_error_text(result) or f"{name} failed")
        parts = [
            block.get("text", "")
            for block in result.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)


def _error_text(result: dict[str, Any]) -> str:
    parts = [
        block.get("text", "")
        for block in result.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p)

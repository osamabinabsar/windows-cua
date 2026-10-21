"""JSON-RPC transport for the local Codex computer-use helper.

The helper (``codex-computer-use.exe``) speaks newline-delimited JSON-RPC over
stdin/stdout. It is a *local* Windows binary: no network, no account, no
usage limit. Everything it does -- enumerating windows, capturing an occluded
window, injecting input into a background window -- happens on this machine.

Wire format (verified against the shipped helper)::

    ->  {"id": 1, "method": "list_windows", "params": {}}
    <-  {"id": 1, "ok": true,  "result": [...]}
    <-  {"id": 1, "ok": false, "error": "message"}

Two approval paths exist, and which one applies depends on the transport:

* **stdio** (this module): an action against an unapproved app comes back as
  ``{"id": 1, "ok": false, "approvalRequest": {...}}``. The client is expected
  to obtain user consent, then **retry the same request with a ``meta`` block
  carrying the approved app id**::

      {"id": 2, "method": "click", "params": {...},
       "meta": {"x-oai-cua-approved-app": "<app id>"}}

* **native pipe**: the helper pushes a ``requestComputerUseApproval``
  request and expects a response frame.

This module implements the stdio path. The approved-app meta key is only ever
written after :meth:`HelperTransport.approve` is called, so the approval gate
cannot be bypassed by a caller forging the key.

The helper shells out to ``codex app-server`` to broker the approval prompt,
so ``CODEX_CLI_PATH`` must point at a real ``codex`` binary. That binary is
used only for the approval handshake; no model is ever invoked through it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "APPROVED_APP_META_KEY",
    "HelperError",
    "HelperNotFoundError",
    "HelperTransport",
    "find_codex_cli",
    "find_helper",
]

#: Meta key the helper reads to learn which app the user approved.
APPROVED_APP_META_KEY = "x-oai-cua-approved-app"

#: Meta key the helper reads to learn how long it may take for a request.
#: The reference client always sends this; without it the helper falls back
#: to its own 10s default and can abort work that would have succeeded.
REQUEST_BUDGET_META_KEY = "x-oai-cua-request-budget-ms"

#: Default per-request timeout. The helper itself uses 10s for normal calls
#: and 15s for ``launch_app``; we allow more headroom for cold starts.
DEFAULT_TIMEOUT = 30.0


class HelperError(RuntimeError):
    """The helper returned an error, or the transport failed."""


class HelperNotFoundError(HelperError):
    """The helper binary could not be located on this machine."""


class ApprovalRequired(HelperError):
    """The helper wants user consent before touching an app.

    Carries the approval request so the caller can prompt the user, then
    retry with :meth:`HelperTransport.approve`.
    """

    def __init__(self, request: dict[str, Any]) -> None:
        self.request = request
        app = request.get("app") or "this app"
        display = request.get("displayName") or app
        super().__init__(f"Computer Use needs approval to use {display} ({app})")


# --------------------------------------------------------------------------
# Locating the binaries
# --------------------------------------------------------------------------

# Codex installs the runtime under
# %LOCALAPPDATA%\OpenAI\Codex\runtimes\cua_node\<hash>\bin\node_modules\
# @oai\sky\bin\windows\.
_CODEX_RUNTIME_GLOBS = (
    "OpenAI/Codex/runtimes/cua_node/*/bin/node_modules/@oai/sky/bin/windows/codex-computer-use.exe",
    "OpenAI/Codex/runtimes/cua_node/*/bin/node_modules/@oai/sky/bin/windows/codex-computer-use-arm64.exe",
)

# Fallback: the @oai/sky npm package if installed standalone.
_SKY_PACKAGE_GLOBS = (
    "AppData/Roaming/npm/node_modules/@oai/sky/bin/windows/codex-computer-use.exe",
    "AppData/Roaming/npm/node_modules/@oai/sky/bin/windows/codex-computer-use-arm64.exe",
    ".local/share/pnpm/global/*/node_modules/@oai/sky/bin/windows/codex-computer-use.exe",
)


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def find_helper(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Locate ``codex-computer-use.exe``.

    Precedence: explicit path -> ``CODEX_COMPUTER_USE_BIN`` env -> Codex
    runtime install -> standalone ``@oai/sky`` npm install.

    Raises :class:`HelperNotFoundError` with actionable guidance if nothing
    is found.
    """
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise HelperNotFoundError(f"helper not found at {p}")
        return p

    env = os.environ.get("CODEX_COMPUTER_USE_BIN", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
        raise HelperNotFoundError(
            f"CODEX_COMPUTER_USE_BIN points at {p}, which does not exist"
        )

    localappdata = os.environ.get("LOCALAPPDATA")
    roots: list[Path] = []
    if localappdata:
        roots.append(Path(localappdata))
    roots.append(_home())

    candidates: list[Path] = []
    for root in roots:
        for pattern in _CODEX_RUNTIME_GLOBS:
            candidates.extend(root.glob(pattern))
        for pattern in _SKY_PACKAGE_GLOBS:
            candidates.extend(root.glob(pattern))

    existing = [p for p in candidates if p.is_file()]
    if existing:
        # Prefer the newest runtime by mtime so a Codex upgrade wins over a
        # stale copy left behind by an older install.
        existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return existing[0]

    raise HelperNotFoundError(
        "Could not find codex-computer-use.exe.\n"
        "Searched: Codex runtime installs and standalone @oai/sky packages.\n"
        "Fix by either:\n"
        "  * installing Codex CLI (npm i -g @openai/codex), or\n"
        "  * setting CODEX_COMPUTER_USE_BIN to the executable's full path."
    )


def find_codex_cli(explicit: str | os.Path[str] | None = None) -> Path | None:
    """Locate the ``codex`` CLI used for the approval handshake.

    The helper needs it to broker "allow Computer Use to use <app>?" prompts.
    It is *not* used to run a model, so a missing CLI is a soft failure:
    the transport still works for read-only calls and for apps that were
    already approved.
    """
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None

    env = os.environ.get("CODEX_CLI_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p

    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        # Codex's own bundled CLI: .../OpenAI/Codex/bin/<hash>/codex.exe
        for p in sorted(Path(localappdata).glob("OpenAI/Codex/bin/*/codex.exe")):
            if p.is_file():
                return p

    found = shutil.which("codex")
    return Path(found) if found else None


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


@dataclass
class HelperTransport:
    """Owns the helper subprocess and serialises JSON-RPC over its stdio.

    The helper is single-threaded from our point of view, so requests are
    funnelled through one lock. Reads happen on a dedicated thread that
    dispatches responses to the waiting caller, so a hung helper cannot wedge
    an unrelated caller past its timeout.
    """

    helper_path: Path
    codex_cli_path: Path | None = None
    timeout: float = DEFAULT_TIMEOUT

    _proc: subprocess.Popen[bytes] | None = field(default=None, init=False)
    _next_id: int = field(default=1, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _io_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _pending: dict[int, tuple[threading.Event, list[Any]]] = field(default_factory=dict)
    _reader: threading.Thread | None = field(default=None, init=False)
    _stderr: list[str] = field(default_factory=list, init=False)
    _approved_apps: set[str] = field(default_factory=set, init=False)
    _closed: bool = field(default=False, init=False)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Spawn the helper process if it is not already running."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            if self._closed:
                raise HelperError("transport is closed")

            env = os.environ.copy()
            if self.codex_cli_path is not None:
                env["CODEX_CLI_PATH"] = str(self.codex_cli_path)
            env.setdefault("CODEX_HOME", str(_home() / ".codex"))

            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            try:
                self._proc = subprocess.Popen(
                    [str(self.helper_path), "--parent-pid", str(os.getpid())],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise HelperError(f"failed to spawn helper: {exc}") from exc

            self._reader = threading.Thread(
                target=self._read_loop, name="cua-helper-reader", daemon=True
            )
            self._reader.start()
            threading.Thread(
                target=self._stderr_loop, name="cua-helper-stderr", daemon=True
            ).start()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            proc = self._proc
            self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
        # Wake anyone still waiting on a response.
        with self._io_lock:
            for event, slot in self._pending.values():
                slot.append({"ok": False, "error": "helper closed"})
                event.set()
            self._pending.clear()

    def __enter__(self) -> "HelperTransport":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- reader threads ----------------------------------------------------

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
                self._dispatch(msg)
        except (OSError, ValueError):
            pass

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in iter(proc.stderr.readline, b""):
                text = raw.decode("utf-8", errors="replace").rstrip()
                if text:
                    with self._lock:
                        self._stderr.append(text)
                        del self._stderr[:-50]
        except (OSError, ValueError):
            pass

    def _dispatch(self, msg: dict[str, Any]) -> None:
        msg_id = msg.get("id")
        if not isinstance(msg_id, int):
            return
        with self._io_lock:
            entry = self._pending.pop(msg_id, None)
        if entry is None:
            return
        event, slot = entry
        slot.append(msg)
        event.set()

    # -- io ----------------------------------------------------------------

    def _write(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise HelperError("helper is not running")
        data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        with self._io_lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (OSError, BrokenPipeError) as exc:
                raise HelperError(f"helper stdin closed: {exc}") from exc

    # -- approval ----------------------------------------------------------

    def approve(self, app: str) -> None:
        """Record that the user approved use of ``app``.

        Subsequent requests for that app carry the approved-app meta key, so
        the helper stops asking. This is the *only* way the key gets set.
        """
        if app:
            self._approved_apps.add(app)

    def approved_apps(self) -> list[str]:
        return sorted(self._approved_apps)

    def _meta_for(self, app: str | None) -> dict[str, Any]:
        """Build the meta block for a request.

        The approved-app key is only sent for an app the user has actually
        approved through :meth:`approve`. The request-budget key is always
        sent so the helper has headroom for slow UIA calls.
        """
        meta: dict[str, Any] = {REQUEST_BUDGET_META_KEY: int(self.timeout * 1000)}
        if app and app in self._approved_apps:
            meta[APPROVED_APP_META_KEY] = app
        return meta

    # -- request -----------------------------------------------------------

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        approved_app: str | None = None,
    ) -> Any:
        """Send a request and return its ``result`` payload.

        Raises :class:`ApprovalRequired` when the helper wants user consent
        for ``approved_app``, and :class:`HelperError` for anything else.
        """
        self.start()
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)

        with self._io_lock:
            req_id = self._next_id
            self._next_id += 1
            event = threading.Event()
            slot: list[Any] = []
            self._pending[req_id] = (event, slot)

        frame: dict[str, Any] = {
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        frame["meta"] = self._meta_for(approved_app)

        try:
            self._write(frame)
        except HelperError:
            with self._io_lock:
                self._pending.pop(req_id, None)
            raise

        remaining = max(0.0, deadline - time.monotonic())
        if not event.wait(remaining):
            with self._io_lock:
                self._pending.pop(req_id, None)
            raise HelperError(
                f"helper call timed out after {remaining:.1f}s: {method}"
            )

        if not slot:
            raise HelperError(f"helper closed before responding: {method}")

        msg = slot[0]

        # The helper asks for consent by returning an approvalRequest on a
        # failed frame. Surface it distinctly so callers can prompt the user.
        approval = msg.get("approvalRequest")
        if isinstance(approval, dict):
            raise ApprovalRequired(approval)

        if not msg.get("ok", False):
            err = msg.get("error")
            if isinstance(err, dict):
                err = err.get("message") or json.dumps(err)
            raise HelperError(str(err or f"helper call failed: {method}"))
        return msg.get("result")

    # -- diagnostics -------------------------------------------------------

    def stderr_tail(self, limit: int = 20) -> list[str]:
        with self._lock:
            return list(self._stderr[-limit:])

    def is_alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

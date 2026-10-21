"""Collaborative Windows computer-use MCP server.

Backed by the *local* Codex computer-use helper: no cloud, no account, no
usage limit. Every action names a target window and is delivered to it
without raising it, so the human and the agent can use the PC at the same
time.
"""

from .client import CuaClient, WindowRef
from .transport import (
    ApprovalRequired,
    HelperError,
    HelperNotFoundError,
    HelperTransport,
    find_codex_cli,
    find_helper,
)

__version__ = "0.1.0"

__all__ = [
    "ApprovalRequired",
    "CuaClient",
    "HelperError",
    "HelperNotFoundError",
    "HelperTransport",
    "WindowRef",
    "__version__",
    "find_codex_cli",
    "find_helper",
]

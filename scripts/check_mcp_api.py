"""What does mcp 2.x offer? Check the MCPServer API surface.

Run with:  uv run --no-project --python 3.14 --with "mcp>=2" -- python -I scripts/check_mcp_api.py
"""

from __future__ import annotations

import importlib.metadata as md
import inspect

try:
    print("mcp version:", md.version("mcp"))
except Exception as exc:  # noqa: BLE001
    print("mcp version: unknown", exc)

import mcp  # noqa: E402

print("mcp package path:", mcp.__file__)
print()

try:
    from mcp.server.mcpserver import MCPServer

    print("MCPServer OK")
    print("  methods:", [m for m in dir(MCPServer) if not m.startswith("_")])
    print()
    for name in ("tool", "run", "run_stdio_async", "add_tool", "elicitation", "session"):
        fn = getattr(MCPServer, name, None)
        if fn is None:
            print(f"  MCPServer.{name}: ABSENT")
            continue
        try:
            print(f"  MCPServer.{name}{inspect.signature(fn)}")
        except (TypeError, ValueError):
            print(f"  MCPServer.{name}: present")
    print()
    print("  run sig:", inspect.signature(MCPServer.run))
except Exception as exc:  # noqa: BLE001
    print("MCPServer import failed:", exc)

print()
# Context type
for mod, attr in (
    ("mcp.server.mcpserver", "Context"),
    ("mcp.server.fastmcp", "Context"),
):
    try:
        m = __import__(mod, fromlist=[attr])
        ctx = getattr(m, attr, None)
        if ctx is None:
            print(f"{mod}.{attr}: ABSENT")
            continue
        print(f"{mod}.{attr}: OK ->", [x for x in dir(ctx) if not x.startswith("_")])
        for name in ("elicit", "request_elicitation", "session", "report_progress"):
            fn = getattr(ctx, name, None)
            if fn is None:
                print(f"    .{name}: ABSENT")
                continue
            try:
                print(f"    .{name}{inspect.signature(fn)}")
            except (TypeError, ValueError):
                print(f"    .{name}: present")
    except Exception as exc:  # noqa: BLE001
        print(f"{mod}: {exc}")

print()
try:
    from mcp.types import ElicitRequest, ElicitResult

    print("ElicitRequest fields:", list(ElicitRequest.model_fields))
    print("ElicitResult fields:", list(ElicitResult.model_fields))
except Exception as exc:  # noqa: BLE001
    print("elicitation types unavailable:", exc)

print()
print("--- mcp.server submodules ---")
import pkgutil  # noqa: E402

import mcp.server as _s  # noqa: E402

print([m.name for m in pkgutil.iter_modules(_s.__path__)])

"""End-to-end smoke test: start the server, list tools, call a read-only tool.

Run with:
  uv run --no-project --python 3.14 --with "mcp>=2" -- python -I scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


async def main() -> int:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "codex_cua_mcp.server"],
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.server_info.name, init.server_info.version)

            tools = await session.list_tools()
            print(f"\n{len(tools.tools)} tools:")
            for t in tools.tools:
                print(f"  - {t.name}: {(t.description or '').splitlines()[0][:90]}")

            print("\n--- call list_windows ---")
            r = await session.call_tool("list_windows", {})
            text = "\n".join(
                c.text for c in r.content if getattr(c, "type", "") == "text"
            )
            print(text[:1200])

            print("\n--- call list_apps ---")
            r = await session.call_tool("list_apps", {})
            text = "\n".join(
                c.text for c in r.content if getattr(c, "type", "") == "text"
            )
            print(text[:1200])

            print("\n--- call status ---")
            r = await session.call_tool("status", {})
            text = "\n".join(
                c.text for c in r.content if getattr(c, "type", "") == "text"
            )
            print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

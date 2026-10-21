"""Check that both backends expose the same sixteen MCP tools.

The promise of the two-backend design is that a harness configuration
does not change when you swap drivers. That promise is only worth
something if it is checked, so this compares the two tool surfaces
directly.

The Codex backend is built against the real helper, and the cua backend
against the real driver, so this needs both binaries present. It does
not perform any action -- only the MCP handshake and `tools/list`.

Run with:  .venv\\Scripts\\python.exe scripts\\check_tool_surfaces.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.server import build_server  # noqa: E402


def tool_names(server) -> dict[str, list[str]]:
    """Every registered tool, with its parameter names, sorted."""
    out: dict[str, list[str]] = {}
    for tool in server._tool_manager.list_tools():
        schema = tool.parameters or {}
        out[tool.name] = sorted((schema.get("properties") or {}).keys())
    return out


async def main() -> int:
    print("building the codex backend...")
    codex_server = build_server(backend="codex")
    codex = tool_names(codex_server)

    print("building the cua backend...")
    cua_server = build_server(backend="cua")
    cua = tool_names(cua_server)

    codex_names = set(codex)
    cua_names = set(cua)

    ok = True
    if codex_names != cua_names:
        ok = False
        print("\n[FAIL] the tool names differ")
        for name in sorted(codex_names - cua_names):
            print(f"    only in codex: {name}")
        for name in sorted(cua_names - codex_names):
            print(f"    only in cua:   {name}")
    else:
        print(f"\n[PASS] both backends expose the same {len(codex)} tools")

    for name in sorted(codex_names & cua_names):
        if codex[name] != cua[name]:
            ok = False
            only_codex = sorted(set(codex[name]) - set(cua[name]))
            only_cua = sorted(set(cua[name]) - set(codex[name]))
            print(f"[FAIL] {name} takes different arguments")
            if only_codex:
                print(f"    only in codex: {only_codex}")
            if only_cua:
                print(f"    only in cua:   {only_cua}")

    if ok:
        print("[PASS] every tool takes the same arguments in both backends")

    print(f"\n{len(codex_names)} tools: {', '.join(sorted(codex_names))}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

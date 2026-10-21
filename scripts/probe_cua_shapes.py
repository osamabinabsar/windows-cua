"""Print the *raw* shapes the `trycua/cua` driver returns.

This is a shape probe, not an action probe: it launches Character Map
(a Windows utility that cannot hold a user document), reads the driver's
own JSON for `list_windows` and `get_window_state`, and prints the keys
and the first couple of entries. Nothing is typed, clicked, or changed.

The point is to check the client's assumptions against the real response
before wiring any action through it.

Run with:  .venv\\Scripts\\python.exe scripts\\probe_cua_shapes.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.cua_backend import CuaDriverTransport, find_cua_driver  # noqa: E402


def _show(label: str, value: object) -> None:
    print(f"--- {label} ---")
    print(json.dumps(value, indent=2, default=str)[:2500])
    print()


def main() -> int:
    driver = find_cua_driver()
    print(f"driver: {driver}\n")

    transport = CuaDriverTransport(driver_path=driver)
    transport.start()

    tools = transport.list_tools()
    print(f"{len(tools)} tools available:")
    for tool in tools:
        if isinstance(tool, dict) and tool.get("name"):
            print(f"  {tool['name']}")
    print()

    # The full schema, descriptions and all, for the tools whose
    # semantics differ from the Codex helper's.
    interesting = {"invoke_menu", "click", "set_value", "verify_state", "debug_window_info"}
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("name") not in interesting:
            continue
        print(f"=== {tool['name']} ===")
        print(json.dumps(tool.get("inputSchema"), indent=2, default=str)[:3000])
        print()
    # `invoke_menu` has no inputSchema property in some versions; print
    # the whole tool object in that case.
    for tool in tools:
        if isinstance(tool, dict) and tool.get("name") == "invoke_menu" and not tool.get("inputSchema"):
            print("=== invoke_menu (raw tool object) ===")
            print(json.dumps(tool, indent=2, default=str)[:3000])
            print()

    # Launch Character Map: it cannot hold a user's document, so probing
    # against it can never touch personal data.
    print("launching Character Map...")
    transport.call_tool("launch_app", {"name": "charmap.exe"})

    windows = transport.call_tool("list_windows", {})
    if isinstance(windows, dict):
        print(f"list_windows top-level keys: {sorted(windows.keys())}")
        for key, value in windows.items():
            if isinstance(value, list):
                print(f"    {key}: list[{len(value)}]")
                if value and isinstance(value[0], dict):
                    print(f"        [0] keys: {sorted(value[0].keys())}")
        print()
    _show("list_windows", windows)

    entries: list[dict] = []
    if isinstance(windows, dict):
        for value in windows.values():
            if isinstance(value, list):
                entries.extend(e for e in value if isinstance(e, dict))
    target = next(
        (e for e in entries if "character" in str(e.get("title", "")).lower()), None
    )
    if target is None:
        print("no Character Map window found; aborting")
        return 1

    pid = int(target["pid"])
    window_id = int(target["window_id"])
    print(f"target: pid={pid} window_id={window_id} title={target.get('title')!r}\n")

    state = transport.call_tool(
        "get_window_state",
        {
            "pid": pid,
            "window_id": window_id,
            "include_accessibility_tree": True,
        },
    )
    if isinstance(state, dict):
        print(f"state keys: {sorted(state.keys())}")
        elements = state.get("elements")
        print(f"elements: {type(elements).__name__} len={len(elements) if isinstance(elements, list) else 'n/a'}")
        if isinstance(elements, list):
            _show("elements[0:3]", elements[:3])
        tree = state.get("tree_markdown")
        if isinstance(tree, str):
            print(f"tree_markdown: {len(tree)} chars")
            print(tree[:600])
            print()
    else:
        _show("get_window_state (raw)", state)

    tree_only = transport.call_tool(
        "get_accessibility_tree", {"pid": pid, "window_id": window_id}
    )
    if isinstance(tree_only, dict):
        print(f"get_accessibility_tree keys: {sorted(tree_only.keys())}")
        for key, value in tree_only.items():
            if isinstance(value, str):
                print(f"    {key}: str[{len(value)}] {value[:120]!r}")
            else:
                print(f"    {key}: {type(value).__name__}")
        # The window entries carry the tree; print their keys and the
        # first 400 characters of the tree itself.
        for entry in tree_only.get("windows") or []:
            if not isinstance(entry, dict):
                continue
            print(f"    window entry keys: {sorted(entry.keys())}")
            for key, value in entry.items():
                if isinstance(value, str) and len(value) > 50:
                    print(f"        {key}: str[{len(value)}]")
                    print(f"        {value[:400]}")
        for entry in tree_only.get("processes") or []:
            if not isinstance(entry, dict):
                continue
            print(f"    process entry keys: {sorted(entry.keys())}")
            for key, value in entry.items():
                if isinstance(value, str) and len(value) > 50:
                    print(f"        {key}: str[{len(value)}]")
                    print(f"        {value[:300]}")
                elif isinstance(value, list):
                    print(f"        {key}: list[{len(value)}]")
                    if value and isinstance(value[0], dict):
                        print(f"            [0] keys: {sorted(value[0].keys())}")
    else:
        print(f"get_accessibility_tree: {type(tree_only).__name__}")
    print()

    transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

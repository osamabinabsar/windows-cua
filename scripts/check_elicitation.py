"""Introspect mcp 2.x elicitation + content types at runtime.

Run with:  uv run --no-project --python 3.14 --with "mcp>=2" -- python -I scripts/check_elicitation.py
"""

from __future__ import annotations

import inspect
import typing

import mcp.types as T

print("--- content block types ---")
for name in ("TextContent", "ImageContent", "AudioContent", "EmbeddedResource", "ContentBlock"):
    obj = getattr(T, name, None)
    if obj is None:
        print(f"  {name}: ABSENT")
        continue
    if hasattr(obj, "model_fields"):
        print(f"  {name}: {list(obj.model_fields)}")
    else:
        print(f"  {name}: {obj}")

print()
print("--- elicitation types ---")
for name in dir(T):
    if "Elicit" in name:
        obj = getattr(T, name)
        if hasattr(obj, "model_fields"):
            print(f"  {name}: {list(obj.model_fields)}")
        else:
            print(f"  {name}: {obj}")

print()
from mcp.server.mcpserver import Context  # noqa: E402

print("--- Context.elicit signature ---")
print(" ", inspect.signature(Context.elicit))
print("  doc:", (Context.elicit.__doc__ or "")[:1500])

print()
print("--- where is ElicitationResult defined? ---")
import mcp.server.mcpserver as ms  # noqa: E402

for modname, mod in [("mcp.types", T), ("mcp.server.mcpserver", ms)]:
    for name in dir(mod):
        if "ElicitationResult" in name:
            obj = getattr(mod, name)
            print(f"  {modname}.{name}")
            if hasattr(obj, "model_fields"):
                print("    fields:", list(obj.model_fields))
            try:
                print("    origin:", typing.get_origin(obj))
                print("    args:", typing.get_args(obj))
            except Exception:  # noqa: BLE001
                pass

print()
print("--- Context.session / request_context ---")
print("  session:", type(Context.session))
print("  request_context:", type(Context.request_context))

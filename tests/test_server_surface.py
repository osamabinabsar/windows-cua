"""The published package must import, and both backends must agree.

Two things went wrong once already, and neither was caught:

* ``server.py`` imported a module that was never committed. Every fresh
  clone died at startup with a ``ModuleNotFoundError`` while CI stayed
  green, because no test imported the server.
* The two backends are documented as presenting "the same sixteen tools,
  the same names". That is a claim about two separate functions in one
  file, and nothing checked it.

So: import the server, and compare the two tool surfaces directly.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_SERVER_PY = Path(__file__).resolve().parents[1] / "src" / "codex_cua_mcp" / "server.py"
_PKG_DIR = Path(__file__).resolve().parents[1] / "src" / "codex_cua_mcp"

#: Win32 entry points that would take the machine away from the user.
#:
#: The whole claim of this project is that it is *collaborative*: actions
#: are delivered to a named window, and the user's own mouse, keyboard and
#: focus are left alone. The only way to break that promise is to start
#: driving the input queue directly, and that needs one of these.
#:
#: ``RegisterHotKey`` is deliberately absent from the list: the
#: take-over chord is a system-wide registration, but it *stops* the
#: agent -- it is the way out, not the way in.
_FORBIDDEN_IN_THE_PACKAGE = (
    "SetWindowsHookEx",
    "SetWindowsHookExA",
    "SetWindowsHookExW",
    "SendInput",
    "mouse_event",
    "keybd_event",
    "BlockInput",
    "SetCursorPos",
    "ClipCursor",
)


def test_the_package_does_not_seize_the_machine():
    """The collaboration promise, enforced.

    If this ever fails, the project has become the thing it was built to
    be different from, and every document that says so is now a lie.
    """
    offenders: list[str] = []
    for path in sorted(_PKG_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name in _FORBIDDEN_IN_THE_PACKAGE:
            if name in text:
                offenders.append(f"{path.name}: {name}")
    assert not offenders, (
        "the published package must not drive the input queue directly: "
        + ", ".join(offenders)
    )


#: The tool surface, as documented in the README.
EXPECTED_TOOLS = {
    "list_apps",
    "list_windows",
    "get_window",
    "get_window_state",
    "read_accessibility_tree",
    "click",
    "click_element",
    "type_text",
    "press_key",
    "scroll",
    "drag",
    "set_value",
    "perform_secondary_action",
    "launch_app",
    "activate_window",
    "status",
}


def _tool_names_in(builder_name: str) -> set[str]:
    """The names of the ``@server.tool`` functions inside one builder.

    Read statically rather than by calling the builder: building a real
    server needs a driver binary on the machine, and a test that needs a
    driver binary is a test that skips itself exactly where it matters.
    """
    tree = ast.parse(_SERVER_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != builder_name:
            continue
        names: set[str] = set()
        for inner in ast.walk(node):
            if not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in inner.decorator_list:
                # @server.tool(...) or @server.tool
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "tool"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "server"
                ):
                    names.add(inner.name)
                    break
        return names
    raise AssertionError(f"{_SERVER_PY.name} has no function named {builder_name!r}")


def test_the_server_module_imports():
    """Catches a tracked module importing something that was never published.

    This is the test that would have caught the untracked ``takeover`` and
    ``overlay`` modules: both are imported by ``server.py``, so a clone
    without them dies on the first import, and only this test looks.
    """
    from codex_cua_mcp import server

    assert server.SERVER_NAME == "codex-cua-mcp"


def test_the_codex_backend_exposes_the_documented_tools():
    assert _tool_names_in("build_server") == EXPECTED_TOOLS


def test_the_cua_backend_exposes_the_same_tools():
    """Switching backends must not change the harness's tool list."""
    assert _tool_names_in("_build_cua_server") == EXPECTED_TOOLS


def test_the_two_backends_expose_identical_tool_names():
    assert _tool_names_in("build_server") == _tool_names_in("_build_cua_server")


def _normalise_chord(text: str) -> str:
    """Fold the two ways a chord gets written down onto one spelling.

    The instructions say "Control+Alt+Shift+Backspace"; the constant says
    "Ctrl+Alt+Shift+Backspace". Same chord, two spellings.
    """
    folded = text.lower().replace("control", "ctrl").replace("menu", "alt")
    return folded.replace(" ", "").replace("-", "")


def test_the_take_over_chord_is_named_in_the_server_instructions():
    """The agent has to be told the chord, or it cannot explain itself."""
    from codex_cua_mcp import server
    from codex_cua_mcp.takeover import DEFAULT_TAKEOVER_CHORD

    for instructions in (server.INSTRUCTIONS, server.CUA_INSTRUCTIONS):
        assert _normalise_chord(DEFAULT_TAKEOVER_CHORD) in _normalise_chord(
            instructions
        )

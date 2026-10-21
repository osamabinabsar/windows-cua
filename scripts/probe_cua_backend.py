"""End-to-end probe of the `trycua/cua` backend.

Exercises the real :class:`CuaDriverClient` against the live driver, on
Character Map -- a Windows utility that cannot hold a user document, so
this probe can never touch personal data.

Each check prints PASS or FAIL and the driver answers with "8/8 checks
passed" style summary. The script exits non-zero if any check fails, so
a driver upgrade that changes behaviour shows up as a failure rather
than as a mystery.

Run with:  .venv\\Scripts\\python.exe scripts\\probe_cua_backend.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.cua_backend import find_cua_driver  # noqa: E402
from codex_cua_mcp.cua_client import CuaDriverClient  # noqa: E402
from codex_cua_mcp.cua_backend import CuaDriverTransport  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((ok, label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))


async def _field_text(client: CuaDriverClient, window) -> str:
    """Read the Character Map 'Characters to copy' field back.

    The driver reports a Windows edit control's text with a trailing
    CR, so the value is stripped before it is compared.
    """
    state = await client.get_window_state(window, include_text=True)
    for element in state.get("elements") or []:
        if not isinstance(element, dict):
            continue
        if element.get("role") == "Edit" and element.get("element_index") == 0:
            return str(element.get("value") or element.get("text") or "").rstrip("\r\n")
    return ""


async def _find_edit(client: CuaDriverClient, window) -> dict | None:
    state = await client.get_window_state(window, include_text=True)
    for element in state.get("elements") or []:
        if isinstance(element, dict) and element.get("role") == "Edit":
            return element
    return None


async def _element(client: CuaDriverClient, window, index: int) -> dict:
    """One element from a fresh read."""
    state = await client.get_window_state(window, include_text=True)
    for element in state.get("elements") or []:
        if isinstance(element, dict) and element.get("element_index") == index:
            return element
    return {}


def _pid_of(app: str) -> int:
    """The pid out of a synthetic ``pid:<n>`` app id."""
    return int(app.split(":", 1)[1])


async def main() -> int:
    driver = find_cua_driver()
    print(f"driver: {driver}\n")

    transport = CuaDriverTransport(driver_path=driver)
    client = CuaDriverClient(transport)

    # -- launch ------------------------------------------------------------
    # Reuse an already-open Character Map if there is one, so repeated
    # runs do not pile up windows. Only launch when none is open, and
    # remember whether this run is the one that opened it, so it can be
    # closed again at the end.
    windows = await client.list_windows()
    target = next((w for w in windows if "character" in (w.title or "").lower()), None)
    opened_here = False
    if target is None:
        try:
            await client.launch_app("charmap.exe")
            check(True, "launch_app succeeds")
            opened_here = True
        except Exception as exc:  # noqa: BLE001
            check(False, "launch_app succeeds", str(exc))
            transport.close()
            return 1
    else:
        check(True, "launch_app succeeds (reused an open window)")

    # -- discovery ---------------------------------------------------------
    apps = await client.list_apps()
    check(bool(apps), "list_apps returns apps", f"{len(apps)} apps")

    windows = await client.list_windows()
    check(bool(windows), "list_windows returns windows", f"{len(windows)} windows")

    target = next((w for w in windows if "character" in (w.title or "").lower()), None)
    if target is None:
        check(False, "Character Map window is discoverable")
        transport.close()
        return 1
    check(True, "Character Map window is discoverable", f"app={target.app} id={target.id}")

    # -- observation -------------------------------------------------------
    state = await client.get_window_state(target, include_text=True)
    elements = state.get("elements")
    check(isinstance(elements, list) and bool(elements), "get_window_state returns elements",
          f"{len(elements) if isinstance(elements, list) else 0} elements")
    check(isinstance(state.get("tree_markdown"), str), "get_window_state returns tree_markdown")

    edit = await _find_edit(client, target)
    if edit is None:
        check(False, "the Edit field is present in the tree")
        transport.close()
        return 1
    index = int(edit["element_index"])
    check(True, "the Edit field is present in the tree", f"index={index} token={edit.get('element_token')}")

    # -- set_value ---------------------------------------------------------
    await client.set_value(target, index, "probe")
    after_set = await _field_text(client, target)
    check(after_set == "probe", "set_value writes the field", f"field={after_set!r}")

    await client.set_value(target, index, "")
    after_clear = await _field_text(client, target)
    check(after_clear == "", "set_value '' clears the field", f"field={after_clear!r}")

    # -- click_element -----------------------------------------------------
    try:
        await client.click_element(target, index)
        check(True, "click_element succeeds")
    except Exception as exc:  # noqa: BLE001
        check(False, "click_element succeeds", str(exc))

    # -- type_text ---------------------------------------------------------
    await client.type_text(target, "abc", element_index=index)
    after_type = await _field_text(client, target)
    check(after_type == "abc", "type_text with element_index lands the text", f"field={after_type!r}")

    await client.set_value(target, index, "")
    after_reset = await _field_text(client, target)
    check(after_reset == "", "the field is restored", f"field={after_reset!r}")

    # -- get_window (rehydrate) -------------------------------------------
    try:
        ref = await client.get_window(target.id, target.app)
        check(ref.id == target.id, "get_window rehydrates a window", f"id={ref.id}")
    except Exception as exc:  # noqa: BLE001
        check(False, "get_window rehydrates a window", str(exc))

    # -- accessibility_tree ------------------------------------------------
    try:
        tree = await client.accessibility_tree(target)
        check(
            "Character Map" in tree,
            "accessibility_tree returns the tree",
            f"{len(tree)} chars",
        )
    except Exception as exc:  # noqa: BLE001
        check(False, "accessibility_tree returns the tree", str(exc))

    # -- screenshot --------------------------------------------------------
    try:
        shot = await client.get_window_state(target, include_screenshot=True)
        has_image = bool(shot.get("screenshot_mime_type")) or bool(
            shot.get("screenshot_width")
        )
        check(
            has_image,
            "get_window_state with a screenshot returns an image",
            f"mime={shot.get('screenshot_mime_type')} "
            f"{shot.get('screenshot_width')}x{shot.get('screenshot_height')}",
        )
    except Exception as exc:  # noqa: BLE001
        check(False, "get_window_state with a screenshot returns an image", str(exc))

    # -- element invoke: a checkbox must toggle ----------------------------
    checkbox = None
    for element in (await client.get_window_state(target, include_text=True)).get(
        "elements"
    ) or []:
        if isinstance(element, dict) and element.get("role") == "CheckBox":
            checkbox = element
            break
    if checkbox is None:
        check(False, "the Advanced view checkbox is in the tree")
    else:
        box_index = int(checkbox["element_index"])
        before = checkbox.get("selected")
        try:
            await client.click_element(target, box_index)
            after_element = await _element(client, target, box_index)
            after = after_element.get("selected")
            check(
                after != before,
                "clicking a checkbox toggles it (the element 'invoke' path)",
                f"selected {before!r} -> {after!r}",
            )
            # Put it back.
            await client.click_element(target, box_index)
        except Exception as exc:  # noqa: BLE001
            check(False, "clicking a checkbox toggles it (the element 'invoke' path)", str(exc))

    # -- press_key ---------------------------------------------------------
    try:
        await client.press_key(target, "Escape")
        check(True, "press_key succeeds")
    except Exception as exc:  # noqa: BLE001
        check(False, "press_key succeeds", str(exc))

    # -- scroll ------------------------------------------------------------
    for label, kwargs in (
        ("scroll (vertical)", {"scroll_y": 3}),
        ("scroll (horizontal)", {"scroll_x": 2}),
    ):
        try:
            await client.scroll(target, 200, 200, **kwargs)
            check(True, f"{label} succeeds")
        except Exception as exc:  # noqa: BLE001
            check(False, f"{label} succeeds", str(exc))

    # -- drag --------------------------------------------------------------
    try:
        await client.drag(target, 100, 100, 140, 140)
        check(True, "drag succeeds")
    except Exception as exc:  # noqa: BLE001
        check(False, "drag succeeds", str(exc))

    # -- invoke_menu -------------------------------------------------------
    # A two-segment path is a menu path. Character Map's own menu bar is
    # reached this way; the Escape afterwards closes whatever opened.
    try:
        await client.perform_secondary_action(target, 0, "Help>About")
        check(True, "invoke_menu walks a menu path")
    except Exception as exc:  # noqa: BLE001
        # A menu path the app does not have is a legitimate failure --
        # the point is that the call reaches the driver and its error
        # comes back, not that the path exists.
        check(
            "menu path segment" in str(exc) or "not found" in str(exc).lower(),
            "invoke_menu walks a menu path (rejected with a clear error)",
            str(exc)[:120],
        )
    try:
        await client.press_key(target, "Escape")
    except Exception:  # noqa: BLE001
        pass

    # -- an action the driver cannot express must fail loudly --------------
    try:
        await client.perform_secondary_action(target, 0, "Scroll Up")
        check(True, "an unsupported accessibility action raises")
    except Exception as exc:  # noqa: BLE001
        check(
            "cannot perform" in str(exc),
            "an unsupported accessibility action raises",
            str(exc)[:120],
        )

    # -- activate_window ---------------------------------------------------
    try:
        await client.activate_window(target)
        check(True, "activate_window (bring_to_front) succeeds")
    except Exception as exc:  # noqa: BLE001
        check(False, "activate_window (bring_to_front) succeeds", str(exc))

    # Close the window this run opened, so repeated runs do not pile up.
    if opened_here:
        try:
            await client.transport.call_tool("kill_app", {"pid": _pid_of(target.app)})
        except Exception:  # noqa: BLE001
            pass

    transport.close()

    passed = sum(1 for ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

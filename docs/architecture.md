# Architecture

## The problem

Windows-MCP is *exclusive*. While the agent drives the machine, the human
cannot: the tool moves the real cursor and installs low-level Win32 input
hooks (`WH_MOUSE_LL` / `WH_KEYBOARD_LL`) that swallow the user's own
input so the two do not fight over the mouse.

ChatGPT's Codex computer use does not have that problem. Both the agent and
the human can drive the PC at once.

## Why Codex is collaborative, and Windows-MCP is not

The difference is **where input is delivered**.

Windows-MCP clicks raw screen coordinates. To click a button, it must move
the system cursor there and click — which requires the target window to be
on top and the user not to be using the mouse. So it takes the desktop over.

The Codex helper does not. It delivers input *to a specific window*:

- **Per-window UI Automation actuation** — for elements that expose
  `InvokePattern` / `ValuePattern`, it invokes the control directly
  through UIA. The click never touches the global cursor or input queue.
- **Coordinate-routed background injection** — for coordinates, it uses
  synthetic pointer injection (`CreateSyntheticPointerDevice` /
  `InjectSyntheticPointerInput`) aimed at the target window's region,
  under a `NoActivateGuard` (`WS_EX_NOACTIVATE`) so the window does not
  take focus.
- **Foreground only on explicit escalation** — when background delivery
  genuinely cannot work, it swaps the foreground window, sends the input,
  and restores the previous foreground window.

Confirmed by string extraction from the shipped helper binary:

```
background input requires a valid target window
background input target moved to another window before injection
SetForegroundWindow(restore background input foreground)
background input foreground changed before restoration
) is over the target window
) is over another window; take a fresh screenshot before retrying
```

That last pair is the tell: the helper *knows* the input is going to the
wrong place and refuses rather than clicking blind. Windows-MCP has no
equivalent concept.

## The architecture of this project

```
  agent harness                    this server                 the helper
 ┌──────────────┐   MCP/stdio  ┌─────────────────┐   JSON-RPC  ┌──────────────────┐
 │ Claude Code  │─────────────▶│  codex-cua-mcp  │───stdio────▶│ codex-computer-  │
 │ OpenCode     │              │                 │             │ use.exe          │
 │ Codex / etc  │◀─────────────│  15 MCP tools   │◀──stdio────│ (local, no net)  │
 └──────────────┘   responses  └─────────────────┘             └──────────────────┘
                                          │                             │
                                   elicitation │                  UI Automation
                                 (user consent)│                Windows.Graphics.Capture
                                          ▼                    background injection
                                     ┌─────────┐
                                     │  user   │
                                     └─────────┘
```

The helper is a **local Windows binary**. It does not call a model. The
model is whatever your harness already talks to. That is the whole trick
behind "no usage limits": the thing that costs money (the model) is not
part of this tool.

The `codex` CLI is needed only to broker the approval prompt. It is never
used to run inference.

## Layers

### `transport.py` — the wire

Owns the helper subprocess and speaks newline-delimited JSON-RPC.

Request frame:

```json
{"id": 1, "method": "list_windows", "params": {},
 "meta": {"x-oai-cua-request-budget-ms": 30000}}
```

Success frame:

```json
{"id": 1, "ok": true, "result": [...]}
```

Approval frame (the helper asking for consent):

```json
{"id": 1, "ok": false,
 "approvalRequest": {"app": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App",
                      "displayName": "Notepad",
                      "riskLevel": "low",
                      "allowPersistentApproval": true}}
```

The reference client (shipped in `@oai/sky`) handles that frame by
eliciting the user, then **retrying the same request with the approved app
id in `meta`**:

```json
{"id": 2, "method": "click", "params": {...},
 "meta": {"x-oai-cua-approved-app": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App",
          "x-oai-cua-request-budget-ms": 30000}}
```

This project implements exactly that, with one hardening: the
approved-app key is written **only** from `HelperTransport.approve()`, which
is called **only** after the user accepts the elicitation. There is no
code path that sets it from anything else.

> The helper also supports a *native pipe* transport
> (`SKY_CUA_NATIVE_PIPE=1`) where approvals arrive as a server-initiated
> `requestComputerUseApproval` request instead of inline on the response.
> This project uses stdio, because a stdio MCP server can be launched by
> any harness without extra environment wiring.

### `client.py` — the API

Wraps the transport in the vocabulary an agent uses: apps, windows, and
window-relative actions. Validates arguments before they reach the helper,
and shapes the helper's replies into a stable form (the helper's
`list_windows` result is a bare array; `list_apps` entries are richer).

`CuaClient._act` is where the approval handshake lives: send → if the
helper asks for consent → consult the `on_approval` hook → on accept,
record the approval and retry.

### `server.py` — the MCP surface

Registers 15 tools and implements the approval prompt as an MCP
**elicitation** (`Context.elicit`), so the harness asks the user and the
user's answer is what authorises the action.

Read-only tools (`list_apps`, `list_windows`, `get_window`, `status`) never
trigger a prompt.

## Protocol reference

Methods, with the parameter shapes the helper expects:

| Method | Params |
|---|---|
| `list_windows` | `{}` |
| `list_apps` | `{}` |
| `get_window` | `{id, app?}` |
| `get_window_state` | `{window, include_screenshot, include_text}` |
| `click` | `{window, x?, y?, element_index?, mouse_button, click_count, screenshotId?}` |
| `click_element` | `{window, element_index, click_count, mouse_button}` |
| `type_text` | `{window, text}` |
| `press_key` | `{window, key}` |
| `scroll` | `{window, x, y, scrollX, scrollY, screenshotId?}` |
| `drag` | `{window, from_x, from_y, to_x, to_y, screenshotId?}` |
| `set_value` | `{window, element_index, value}` |
| `perform_secondary_action` | `{window, element_index, action}` |
| `activate_window` | `{window}` |
| `launch_app` | `{app}` |

`window` is always `{"app": "<canonical app id>", "id": <int>}`. The `id`
is opaque and only valid for windows returned by a prior call.

## Constraints worth knowing

- **Element indexes are only valid for the observation that produced them.**
  Re-read state after anything that might change the tree.
- **Coordinates are window-relative**, not screen-relative.
- **`activate_window` is the only focus-taking operation.** Everything else
  leaves the user's foreground window, focus, and cursor alone.
- **A blocked action reports honestly.** If the helper cannot deliver
  background input, it returns an error rather than silently clicking the
  wrong window.
- **The Escape key still stops everything.** The helper watches for a
  physical Escape press and aborts the turn. That behaviour is inherited
  and is deliberately not overridden.

## Upstream

- The helper ships with [Codex CLI](https://github.com/openai/codex) and
  the `@oai/sky` package. The protocol above was reverse-read from the
  shipped JavaScript client and confirmed against the live binary.
- [`trycua/cua`](https://github.com/trycua/cua) is an independent,
  MIT-licensed Windows computer-use driver (Rust) with a comparable
  feature set. It is a good alternative foundation — see
  [the alternatives doc](alternatives.md).

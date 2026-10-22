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
 │ Codex / etc  │◀─────────────│  16 MCP tools   │◀──stdio────│ (local, no net)  │
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

### The second backend

The same sixteen tools also run on the MIT-licensed
[`trycua/cua`](https://github.com/trycua/cua) driver, which is itself an
MCP server. The shape of the system does not change:

```
 ┌──────────────┐   MCP/stdio  ┌─────────────────┐   MCP/stdio  ┌──────────────────┐
 │ Claude Code  │─────────────▶│  codex-cua-mcp  │─────────────▶│  cua-driver.exe  │
 │ OpenCode     │              │  16 MCP tools   │◀─────────────│  (MIT, Rust)     │
 └──────────────┘   responses  └─────────────────┘   responses  └──────────────────┘
```

`build_server(backend="cua")` swaps the transport. Both backends
implement the same `sky`-shaped API — apps, windows, and window-relative
actions — so the sixteen MCP tools are written once and the harness
configuration does not change when you switch drivers. See
[the alternatives doc](alternatives.md) for the differences that are real
rather than cosmetic.

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

Registers 16 tools and implements the approval prompt as an MCP
**elicitation** (`Context.elicit`), so the harness asks the user and the
user's answer is what authorises the action.

Read-only tools (`list_apps`, `list_windows`, `get_window`, `status`) never
trigger a prompt.

### `takeover.py` — the way out

The user's side of the contract. A single chord —
`Ctrl+Alt+Shift+Backspace` by default — is registered with
`RegisterHotKey` (`MOD_NOREPEAT`), with a `GetAsyncKeyState` polling
fallback for when another application already owns the combination.

It sits *above* the drivers rather than inside them, and that placement is
the point: the lever that stops the agent must not depend on the agent,
the driver, or the harness agreeing to stop. The controller exposes one
gate, and both backends call it before every request — so there are two
choke points rather than one, and neither backend can be driven past it.

Pressing the chord does four things, in this order: signal the helper's
`Local\CodexComputerUseTurnEnded` event so it dismisses its own overlay
cleanly, terminate the driver process, fail every pending request, and
revoke every approval. The MCP server stays alive, so the harness sees
failed calls rather than a dead server, and the agent can pick up again
when the user presses the chord a second time.

### `overlay.py` — the banner text

The Codex helper draws a banner while it drives, and it reads its own
text from `%USERPROFILE%\.codex\computer-use\config.json`. This module
reads and writes that file, so the two sentences in the banner are the
user's to choose without patching a binary. It knows nothing about the
helper's internals — only the file's shape.

### `cua_backend.py` and `cua_client.py` — the second driver

`cua_backend.py` owns the `cua-driver.exe` subprocess. The driver is
itself an MCP server, so this is a small MCP client: `initialize`, then
`tools/call`, over newline-delimited JSON-RPC.

`cua_client.py` maps the same `sky` API onto the driver's tools. The
mapping is not one-to-one, and the differences are worth stating:

- **Windows are addressed by pid + window_id**, not by an app id. The
  driver has no app-id concept, so the client synthesises one as
  `pid:<pid>` and keeps the pid alongside it.
- **Delivery mode is explicit.** Every input carries `delivery_mode`, and
  `"background"` keeps the target window from taking focus. This client
  always asks for background, and escalates to foreground *only* when the
  driver says background is impossible for that window class — which it
  performs as a brief focus swap that restores the previous foreground.
- **Element handles are tokens, not indexes.** The driver returns both
  `element_index` and `element_token`, and it will not accept a bare
  index for a value-setting action. The client resolves the index to a
  token from a fresh read.

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

### Three behaviours the helper does not advertise

These were found by probing the live Windows binary, not by reading the
type definitions. Each one has bitten a working client, so each has a
defensive wrapper in `client.py`.

**1. On Windows, `click` requires `x` and `y`.** The type definitions
allow `element_index` in place of coordinates, but the Windows helper
rejects that with `parse click params: missing field 'x'`. The
element-targeted path is a separate method, `click_element`, which
actuates the control through UI Automation and needs no coordinates at
all. `CuaClient.click` therefore routes to `click_element` whenever it
is given an `element_index`.

**2. An element-targeted action needs a prior observation *with a
screenshot*.** Without one the helper refuses with `coordinate input
geometry is unavailable`, or `call get_window_state before using this
window`. The captured geometry is what lets it route the input.
`CuaClient._ensure_observed` re-observes the window with a screenshot
when the caller has not already done so.

**3. `type_text` and `press_key` go to whatever holds focus — and
silently vanish if nothing does.** The helper reports success either
way. Measured against Character Map: after `type_text "0042"`, the field
read empty unless the field had been clicked first. This is the most
dangerous of the three, because the failure is invisible. Both methods
now take an optional `element_index` that clicks the target first, and
`set_value` — which needs no focus at all — is the reliable way to fill a
field. An empty `set_value` clears it.

### Four behaviours the cua driver does not advertise

The same exercise, run against the `trycua/cua` driver. Each of these
was found by running the driver, not by reading its documentation, and
each is handled defensively in `cua_client.py`.

**1. `set_value` refuses a bare `element_index`.** It wants an
`element_token`, or a `snapshot_id` alongside the index. The client
resolves the index against a fresh read and sends the token.

**2. `get_accessibility_tree` returns a desktop-wide view**
(`{"processes": [...], "windows": [...]}`) whose window entries carry
geometry but no tree. The per-window tree is `get_window_state`'s
`tree_markdown`, and that is what the client uses.

**3. Some window classes refuse background input.** A dialog will not
take a background scroll. The driver detects this, says so, and asks
for `delivery_mode: "foreground"` — which it performs as a brief focus
swap that restores the previous foreground window afterwards. The client
takes that escalation and never fronts a window on a guess.

**4. There is no accessibility-action tool.** The closest is
`invoke_menu`, which walks an application *menu* path. So a menu path
(`File>Save As`) goes to `invoke_menu`; a plain action name (`Invoke`,
`Expand`, `Toggle`) is honoured by clicking the element; and anything the
driver cannot express raises rather than silently doing nothing.

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
  and is deliberately not overridden. The take-over chord is an addition,
  not a replacement — see `takeover.py` above.
- **An app is named two ways, and both must be approved.** `launch_app`
  takes a bare executable name (`charmap.exe`); window-targeted calls
  carry the window's own app id, which for a non-packaged app is a
  process path (`process:C:\Windows\System32\charmap.exe`). The helper
  treats those as different apps and would ask for consent again on
  every call. `HelperTransport._app_identity` reduces both to the
  executable name, so one approval covers the session.
- **Windows 11 Notepad is single-instance with tabs.** A "new" Notepad
  can share a window — and a tab — with the document the user already
  has open. Never point a test or a bulk action at Notepad; use an app
  that cannot hold a user document.

## Upstream

- The helper ships with the **Codex desktop app**, under
  `@oai/cua` and `@oai/sky` in the app's runtime. The
  [Codex CLI](https://github.com/openai/codex) does *not* ship it — the
  CLI on its own is not enough. The protocol above was reverse-read from
  the shipped JavaScript client and confirmed against the live binary.
- [`trycua/cua`](https://github.com/trycua/cua) is an independent,
  MIT-licensed Windows computer-use driver (Rust) with a comparable
  feature set. It is a good alternative foundation — see
  [the alternatives doc](alternatives.md).

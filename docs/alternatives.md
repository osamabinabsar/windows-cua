# Alternatives, and the REA track

The original plan was two tracks: a personal build on the existing Codex
helper, and a second, independent build produced by reverse-engineering
(REA) the helper so it could be hosted publicly.

Track A is built and working — see the [README](../README.md). This document
records the reasoning for Track B, including the licensing problem that
makes a literal "rebuild and publish" not straightforward.

## What the REA track actually found

REA (Ghidra-backed) was pointed at `codex-computer-use.exe` (1.5 MB) and the
Swift binary `codex-computer-use-swift.exe` (56 MB). Both exceeded the
provider's 330-second analysis budget: Ghidra's decompiler spent the window
emitting pcode warnings and never finished. The 56 MB binary was worse.

What *did* work was reading the shipped `@oai/sky` JavaScript client, which
is the authoritative statement of the protocol and is not minified away
beyond recovery. The `.d.ts` type definitions alongside it gave exact
parameter shapes for every method. From those two sources plus live probes
against the binary, the entire stdio protocol was recovered — including the
approval handshake, the meta keys, the request-budget key, and the
per-method timeouts.

That is the honest result of the reverse-engineering effort: **the protocol
is fully recovered, and it is now implemented in this repo.**

## The licensing problem

The helper is OpenAI's, and the `computer-use` Codex plugin declares its
licence as:

```json
"license": "Proprietary"
```

So there are three distinct things, and they must not be conflated:

1. **The helper binary** — proprietary, redistributed by OpenAI with Codex
   CLI. It is not ours to publish.
2. **The wire protocol** — the request/response shapes. A protocol is
   interface, not expression; reimplementing an interface is normal and
   legal. This repo is a client of that protocol and is MIT-licensed.
3. **A from-scratch reimplementation of the Windows automation behaviour**
   — this is where a REA-derived rebuild would live.

A public repo must therefore contain (3) — an independent implementation of
the *behaviour* — and must not ship (1). Concretely, a public
"our-own version" would need to reimplement:

- per-window UI Automation actuation (`InvokePattern`, `ValuePattern`)
- Windows.Graphics.Capture for occluded-window screenshots
- background synthetic pointer injection
  (`CreateSyntheticPointerDevice` / `InjectSyntheticPointerInput`)
- the `NoActivateGuard` (`WS_EX_NOACTIVATE`) focus-preservation discipline
- foreground escalation and restoration
- the honest-failure behaviour: report "that point is over another window"
  rather than clicking the wrong thing

That is a substantial body of Windows work — and it is a body of work that
**already exists, MIT-licensed, with public source**: [`trycua/cua`](https://github.com/trycua/cua).

## The `trycua/cua` option

`trycua/cua` was already installed on this machine
(`~/.cua-driver/`, v0.28.2, Rust, MIT). It is not a wrapper around
anything proprietary — it is a real driver with its own:

- `crates/platform-windows/src/input/delivery.rs` — the delivery-mode
  ladder (`background` vs `foreground` escalation, and
  `would_be_silently_dropped(target, event_kind)`)
- `crates/platform-windows/src/input/inject.rs` — the
  "Universal background mouse actuator", using synthetic pointer devices
  and `NoActivateGuard`
- `crates/platform-windows/src/input/keyboard.rs`, `mouse.rs`
- `crates/cua-driver-contract/` — typed request/response contracts

Its `launch_app` uses `SW_SHOWNOACTIVATE` — "the driver never brings the
target to the foreground" — the same collaborative discipline.

### What it actually is

The driver is **itself an MCP server**, not a library to link against.
`cua-driver mcp` speaks MCP over stdio and exposes **60 tools**:

```
bring_to_front  browser_click  browser_navigate  browser_type  click
clipboard_read  clipboard_write  double_click  drag  get_accessibility_tree
get_desktop_state  get_screen_size  get_window_state  hotkey  invoke_menu
kill_app  launch_app  list_apps  list_sessions  list_windows  move_cursor
press_key  right_click  run_script  run_steps  scroll  set_value
set_window_frame  start_session  end_session  type_text  verify_state  ...
```

Two details make it a good fit for the "second version" the user asked
for:

**It already covers our tool surface.** `list_windows`, `get_window_state`,
`get_accessibility_tree`, `click`, `type_text`, `press_key`, `set_value`,
`scroll`, `drag`, `launch_app`, `kill_app` — every action this repo
exposes, with the same names.

**It fixes the three problems we had to work around.** The driver's
`ClickInput` accepts `element_token` *or* `x`/`y` — so there is no
"missing field x" trap. It requires an explicit
`delivery_mode: "background"` on every input, so the collaborative
discipline is enforced by the type system rather than by convention. And
its `element_token` is a stable handle rather than a per-observation
index, so the "element indexes are only valid for the observation that
produced them" hazard does not exist.

It also has `--claude-code-computer-use-compat`, which selects a
Claude-Code-shaped tool surface from the same binary.

### Wiring it up

The driver ships the config generation itself, for 12 harnesses — including
the ones named in the original request:

```bash
cua-driver mcp-config --client claude-code   # Claude Code
cua-driver mcp-config --client codex
cua-driver mcp-config --client opencode
cua-driver mcp-config --client hermes
```

`--client` accepts `claude`/`claude-code`, `codex`, `cursor`, `openclaw`,
`opencode`, `hermes`, `pi`, `prime-agent`, `antigravity`/`gemini`,
`qwen`/`qwen-code`, `droid`/`factory`, `zcode`, and `generic`.

This makes the "own version we can host on GitHub" goal **already
satisfied** — with a far more reliable implementation than a rebuild
would produce. What this repo adds on top is the parts a raw driver does
not give you:

- a **uniform tool surface** across the two backends, so a harness
  config does not change when you swap drivers;
- the **elicitation-based approval flow** — the driver has an
  `ElicitRequest` in its schema, but the per-app consent model this
  project exposes is worth keeping;
- the **collaboration guarantees** stated in one place, and a `status`
  tool that says which backend is actually in use.

### Comparison

| | Track A (this repo) | `trycua/cua` driver | REA rebuild from scratch |
|---|---|---|---|
| Licence | MIT (client only) | MIT | MIT (our code) |
| Depends on OpenAI binaries | Yes — needs Codex CLI | No | No |
| Works if Codex is uninstalled | No | Yes | Yes |
| Publishable as-is | Yes (client) | Yes | Yes |
| Effort to reach parity | Done | Done | Weeks |
| Tool count | 16 | 60 | ? |
| Rust toolchain needed to build | No | Yes (no toolchain here) | Yes |
| Maintenance burden | Low (protocol wrapper) | Low (upstream maintains) | High (we maintain) |
| Element addressing | Per-observation index (stale-prone) | Stable `element_token` | ? |
| Focus discipline | Convention + docs | Enforced by the input type | ? |

## The recommendation

**Do not rebuild what is already MIT-licensed and better maintained.**

The case for the REA track was "we want our own version we can host on
GitHub for others to use". But:

- Track A *is* already hostable. It is MIT, it is a client of a documented
  interface, and it works today. It does require Codex CLI on the user's
  machine, which is a reasonable prerequisite for a tool whose selling point
  is "the same computer-use as Codex, without the usage limits".
- Track A + `trycua/cua` covers both cases. Users with Codex CLI get the
  Codex helper's implementation; users without it get the MIT Rust driver.
  Both are collaborative; both are local; neither has usage limits.
- A from-scratch REA rebuild would be the *most* work, the *most* risk, and
  the *least* reliable — all three of which are against the stated priority
  of "quality and reliability".

What REA *should* be used for, then, is verification and targeted
questions about the helper's behaviour, not as a code-generation engine
for a ground-up rewrite.

### Suggested next steps, in order

1. ~~**Register Track A in Claude Code** and use it daily. That is the
   fastest path to knowing whether it is reliable enough.~~ **Done.**
2. ~~**Add a `trycua/cua` backend** as a fallback driver, selectable by
   environment variable. The client layer is already a clean seam: both
   backends implement the same `sky`-shaped API.~~ **Done** — see below.
3. **Use REA narrowly** — e.g. to answer "does the helper's background
   injection ever raise the window?" with evidence rather than inference.
4. **Only then** consider a from-scratch build, and only if 1–3 show a
   specific capability the existing stack cannot provide.

---

## Track B, as built: the `cua` backend

Step 2 is done. The `trycua/cua` driver is now a first-class backend in
this repo, selected with `--backend cua` or `WINDOWS_CUA_BACKEND=cua`.

```
src/codex_cua_mcp/
  cua_backend.py   a small MCP client for the driver (it is itself an MCP server)
  cua_client.py    the same `sky`-shaped API, over the cua driver
  server.py        build_server(backend=...) picks the transport
```

The driver version installed here is **v0.28.2** and it exposes **57
tools** (the count moves as the driver ships). The sixteen tools this
server exposes are a subset, with the same names and the same arguments
as the Codex backend, so a harness config does not change when you swap
drivers.

### What the live driver taught us

Every mapping was checked against the running driver, not against a
guess. `scripts/probe_cua_driver.py` reads the driver's own `tools/list`
schemas; `scripts/probe_cua_backend.py` runs twenty-three behavioural
checks against Character Map. Six things were wrong or missing in the
first implementation, and all six are now handled:

1. **`set_value` refuses a bare `element_index`.** The driver wants an
   `element_token`, or a `snapshot_id` alongside the index. The client
   resolves the index to a token from a fresh read, so the handle is
   never stale when it is used.
2. **`get_accessibility_tree` returns a desktop-wide view**
   (`{"processes": [...], "windows": [...]}`) whose window entries carry
   geometry but no tree. The per-window tree is what `get_window_state`
   returns as `tree_markdown`, and that is what the client uses.
3. **Some window classes refuse background input.** A dialog will not
   take a background scroll. The driver detects this, says so, and asks
   for `delivery_mode: "foreground"` — which it performs as a brief
   focus swap that restores the previous foreground afterwards. The
   client takes that escalation, and never fronts a window on a guess.
4. **There is no accessibility-action tool.** The closest is
   `invoke_menu`, which walks an application *menu* path. So a menu path
   (`File>Save As`) goes to `invoke_menu`; a plain action name (`Invoke`)
   is honoured by clicking the element; and anything the driver cannot
   express **raises** rather than silently doing nothing.
5. **A launch target's shape decides the field.** `charmap.exe` is an
   installed app's file name and goes in `name`; `C:\Windows\...` is a
   path and goes in `path`.
6. **The driver reports a window's edit-control text with a trailing
   CR.** A field set to `probe` reads back as `probe\r`.

### The two backends are not identical, and the differences are stated

`perform_secondary_action` is the one tool whose meaning genuinely
differs between the drivers, and the difference is in the tool
description, the `CUA_INSTRUCTIONS` block, and the README table — not
hidden behind a flag.

### Licensing

The `cua` backend is the one to use if the point of a public repo is that
it stands on its own. The driver is MIT-licensed, the client is
MIT-licensed, and neither has an OpenAI dependency. The `codex` backend
is a client of a documented interface and is also MIT-licensed, but it
does require Codex CLI on the machine.

### Why this is still not a "rebuild"

The user's ask was "a version of our own, hosted on GitHub for others to
use". This delivers that: `--backend cua` is a self-contained, MIT-only
installation with no OpenAI binary, no account, and no usage limit. What
it does *not* deliver is a reimplementation of the driver from scratch —
that is upstream's job, maintained upstream, and reimplementing it would
have been the least reliable option against a stated priority of quality
and reliability.

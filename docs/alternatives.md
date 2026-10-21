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

and it exposes **57 MCP tools** over stdio, with a `mcp-config --client`
helper that emits ready-to-paste config for 12 harnesses (claude, codex,
cursor, hermes, antigravity, openclaw, opencode, pi, prime-agent, qwen,
droid, zcode).

Its `launch_app` uses `SW_SHOWNOACTIVATE` — "the driver never brings the
target to the foreground" — the same collaborative discipline.

### Comparison

| | Track A (this repo) | `trycua/cua` driver | REA rebuild from scratch |
|---|---|---|---|
| Licence | MIT (client only) | MIT | MIT (our code) |
| Depends on OpenAI binaries | Yes — needs Codex CLI | No | No |
| Works if Codex is uninstalled | No | Yes | Yes |
| Publishable as-is | Yes (client) | Yes | Yes |
| Effort to reach parity | Done | Done | Weeks |
| Tool count | 15 | 57 | ? |
| Rust toolchain needed to build | No | Yes (no toolchain here) | Yes |
| Maintenance burden | Low (protocol wrapper) | Low (upstream maintains) | High (we maintain) |

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

1. **Register Track A in Claude Code** and use it daily. That is the
   fastest path to knowing whether it is reliable enough.
2. **Add a `trycua/cua` backend** as a fallback driver, selectable by
   environment variable. The client layer is already a clean seam: both
   backends implement the same `sky`-shaped API.
3. **Use REA narrowly** — e.g. to answer "does the helper's background
   injection ever raise the window?" with evidence rather than inference.
4. **Only then** consider a from-scratch build, and only if 1–3 show a
   specific capability the existing stack cannot provide.

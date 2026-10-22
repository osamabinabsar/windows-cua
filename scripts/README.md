# scripts/

Everything here is a **probe or a tool** — a script that talks to a real
binary on a real machine and reports what it found. None of them are
part of the shipped package; the package is `src/codex_cua_mcp/`.

Three groups, in the order you are most likely to want them.

---

## The tool itself

| Script | What it does |
|---|---|
| `install.ps1` | The installer. Creates a virtualenv, installs the package, registers the server with your harness, and verifies it. `-Uninstall` removes what it added. |
| `smoke_test.py` | End-to-end: start the server, list the tools, call a read-only one. |
| `test_elicitation.py` | Verifies the approval path — a tool call that needs consent, answered by an MCP client that actually answers the elicitation. |
| `check_tool_surfaces.py` | Checks that both backends expose the same sixteen tools. The whole point of the two-backend design is that a harness config does not change when you switch. |
| `check_elicitation.py`, `check_mcp_api.py` | Introspect the `mcp` 2.x API at runtime — what elicitation and content types the installed version actually offers. |
| `probe_helpers.py` | Re-verify the Codex helper's undocumented Windows behaviours. Eight checks against the live helper. The target is Character Map, so it cannot damage your work. |
| `probe_cua_backend.py` | End-to-end probe of the `trycua/cua` backend. Twenty-four checks against the live driver. |
| `probe_cua_driver.py` | Print the `trycua/cua` driver's own tool schemas. |
| `probe_cua_shapes.py` | The *raw* shapes the cua driver returns, before this project maps them onto its own API. |
| `probe_protocol.py` | Learn the exact wire shapes of `codex-computer-use.exe` on stdio. Does **not** forge the approval key. |
| `probe_approval.py` | What an approval request looks like on the wire. |
| `_probe.py` | The shared harness for the live protocol probes: subprocess, JSON-RPC framing, and the redaction rules. |

## Taking the computer back

| Script | What it does |
|---|---|
| `probe_takeover.py` | Verify the take-over chord arms, gates, and stops the helper. Uses the server's own wiring. Synthesises no key and moves no pointer. |
| `probe_takeover_event.py` | Can an external process end the Codex helper's computer-use turn? One `SetEvent` on a named event, and nothing else. |
| `set-overlay-text.py` | Set the banner the Codex helper shows while it controls the PC. |

## The invisible cursor

These belong to one investigation: the pointer vanishing over parts of
the screen on Windows 11. The write-up is
[`docs/troubleshooting/invisible-cursor.md`](../docs/troubleshooting/invisible-cursor.md),
and it records what was measured, what was ruled out, and how to
reproduce the measurements.

They are kept **in the order they were written**, because that order is
the argument: each script asks the question the previous one left open.
Some of them are superseded — the doc says which, and so does each
script's own docstring.

### The instruments

| Script | What it does |
|---|---|
| `cursorframe.py` | Cursor-in-framebuffer test. Decides, at the pointer's exact position, whether the arrow is composited into the screen image or lives on the adapter's hardware cursor plane. This is the library the other cursor scripts import. |
| `cursor_shapes.py` | Resolve the live cursor to a *shape*, and separate "the app suppressed the cursor" from "the cursor is not being drawn". The most-cited instrument in the doc. |
| `cursor_is_composited.py` | Is the pointer composited into the screen image, or on the hardware plane? The decisive experiment. |
| `cursor_software_probe.py` | The earlier A/B of the same question. **Superseded by `cursor_is_composited.py`.** |
| `cursor_handle_map.py` | Map shared cursor HANDLE values to system cursor names. The cursors the OS draws are shared objects, so the same handle means the same cursor in every process. |
| `show_cursor_handles.py` | Render a cursor handle as a text picture. |
| `diagnose_cursor.py` | First pass. Is the pointer genuinely "not showing" per the OS, or is the OS drawing a transparent one? And is an invisible top-level window sitting over it? |
| `diagnose_cursor2.py` | Identify the overlay processes; read the live cursor state; check for a stuck mouse button. |
| `diagnose_cursor3.py` | The two decisive questions: hardware cursor or software? How many cursor planes does the system have? |
| `diagnose_cursor4.py` | Per-display hardware cursor plane support. |
| `diagnose_cursor5.py` | Per-display cursor plane, and Chromium GPU feature flags. |
| `diagnose_cursor6.py` | Identify the *live* cursor, and the per-display plane. |
| `diagnose_cursor7.py` | Is the pointer in the framebuffer, or on a hardware plane? |
| `diagnose_cursor8.py` | What kind of surface is each affected region? |
| `diagnose_cursor9.py` | The visible/hidden split should follow surface type. |
| `diagnose_cursor10.py` | The visible/hidden split, proved by geometry. |
| `diagnose_cursor11.py` | HDR state and the per-display hardware cursor plane. |
| `diagnose_cursor12.py` | The third-party transparent overlays. |
| `diagnose_cursor13.py` | Grab the composited framebuffer fast, and look for the overlay's painted cursor. |
| `diagnose_cursor14.py` | Static geometric hit-test of the affected windows. Needs no mouse movement. |
| `diagnose_cursor15.py` | Probe the graphics stack for a cursor-plane API. This is the pass that overturned the "XAML island hides the pointer" story. |
| `diagnose_cursor16.py` | The last diagnose: composited into the image, or on the hardware plane? |
| `diagnose_display_split.py` | Which display does each affected window actually live on? |
| `diagnose_owl_cursor.py` | Byte-scan the OpenAI Codex "Owl" app bundle for a CSS cursor injector. |
| `watch_cursor.py` | Correlate *where* the pointer is with *what* cursor is set. |
| `watch_cursor2.py` | What is under the pointer, and does that window class even have a cursor? |
| `watch_cursor3.py` | The decider. |
| `watch_cursor4.py` | Per-surface hardware-vs-software cursor determination. |
| `watch_cursor5.py` | Chase the one region never sampled — the Explorer nav pane. |
| `mpo_per_source.py` | Read the MPO capability of each display output, per VidPN source. `dxdiag` reports it per output; this reports it per source. |
| `enum_explorer_windows.ps1` | Enumerates Explorer child window class names and geometry. Read-only. |
| `fix-mouse-cursor.ps1` | Reports the MPO overrides and, with Administrator and a full power cycle, applies them. |

---

## A note on the cursor probes

None of them sends input, activates, moves or closes a window, or
captures your screen beyond the pointer's own neighbourhood. The
framebuffer readers (`cursor_is_composited.py`, `diagnose_cursor13.py`,
`diagnose_cursor16.py`) crop to the pointer's position to answer a
question about the pointer.

Two of them do have visible side effects, and they are deliberate:

- `cursor_is_composited.py` and `cursor_software_probe.py` call
  `jiggle(x, y)`, which moves the pointer **one pixel** right and back
  to the exact position it was already at. It is always called with the
  pointer's current position, so it cannot park the pointer somewhere
  else — but you will see it twitch.
- The same two scripts temporarily replace the system cursor with a
  magenta one, to test whether the framebuffer reader can see a
  software-drawn cursor at all. The original cursor is restored in a
  `finally` block, so it comes back whether the experiment succeeds or
  throws.

Everything else — the `diagnose_cursor*` series, the `watch_cursor*`
series, `cursor_shapes.py`, `cursor_handle_map.py`, `mpo_per_source.py`
— is a pure read. The `watch_cursor*.py` scripts need the pointer to
*move*, because the whole point is to sample many surfaces; they watch
and report, and they never drive the pointer themselves.

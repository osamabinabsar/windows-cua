# Taking the computer back

The agent can be stopped at any moment, from outside the harness, by
pressing one chord:

```
Ctrl + Alt + Shift + Backspace
```

Press it once and the agent loses the computer. Press it again and it
gets it back. Nothing else is required — no panel, no tray icon, no
replying to a prompt in a chat window.

This is the same lever [Windows-MCP](https://github.com/tristanbatchman/Windows-MCP)
offers, for the same reason: a tool that drives your mouse and keyboard
has to have a way out that does not depend on the agent agreeing to
provide one.

---

## What one press does

| | |
|---|---|
| **Stops the action in flight** | The driver process is terminated. A click that was halfway through does not finish. |
| **Dismisses the on-screen banner** | The helper is told to end its turn, so its overlay is taken down cleanly rather than killed with the process. |
| **Revokes every approval** | Every app the agent had been cleared to touch is cleared again. The next app needs a fresh yes. |
| **Keeps the server alive** | The MCP server does not exit. The harness sees the tool calls fail with a clear message, and can carry on when you press the chord again. |

While it is held, **every** computer-use tool call fails immediately with:

> The user took control back (pressed the take-over chord). Computer Use
> is paused. Do not call any further computer-use tools; tell the user
> what you were doing and wait for their next instruction.

That message goes to the agent, not to you. The agent is expected to
stop, say what it was doing, and wait. It is a tool error like any
other, so a harness that shows you the call log will show you the
take-over happening in real time.

---

## The two backends, in detail

### `codex` (the default)

The Codex helper is a separate process that the server owns.

1. The server signals the helper's turn-ended event,
   `Local\CodexComputerUseTurnEnded`, so the helper dismisses its own
   overlay and shuts the current turn down tidily.
2. The helper process is terminated.
3. Any request that was in flight fails with `stopped by the user`.
4. The approved-app list is emptied, so nothing stays pre-approved.
5. The next tool call starts a fresh helper.

### `cua` (the MIT driver)

The server spawns its own `cua-driver.exe mcp` child. The take-over
terminates that child.

The agent cursor you see on screen — the one-hand index-finger hand —
is a window *inside* that process, of class `Cua.AgentCursorOverlay`.
Killing the process destroys the window with it, so the cursor
disappears along with the agent.

The `trycua/cua` driver has no per-app approval store of its own, so
there is nothing to revoke there; the driver applies its own permission
model to each action instead. Everything else — the action in flight
dies, pending requests fail, the next call respawns the driver — is the
same.

> **Note.** The `cua-driver stop` and `cua-driver revoke` commands
> target the long-lived `cua-driver serve` daemon, which is a *different*
> process from the one this server spawns. They will not stop this
> server's agent. The take-over deliberately uses the server's own
> child process, which is the one actually driving the screen.

---

## Changing the chord

```bash
python -m codex_cua_mcp.server --takeover-key "Ctrl+Alt+Shift+Pause"
```

Modifiers are `Ctrl`/`Control`, `Alt`/`Menu` and `Shift`; at least one
is required, and at least one non-modifier key is required after it. A
bare key is rejected — it would fire while you were typing.

Any key name Win32 understands works (`Backspace`, `Pause`, `F9`,
`Escape`, `OemTilde`…). The chord is registered with
`RegisterHotKey` and `MOD_NOREPEAT`, so holding it down does not
toggle repeatedly.

### If another application already owns the chord

`RegisterHotKey` fails if the combination is already taken. The
controller notices, falls back to polling `GetAsyncKeyState`, and
keeps working — it just costs a little CPU while the server runs.

`status` tells you which mode is active:

```
take-over : Ctrl+Alt+Shift+Backspace (hotkey)
```

`hotkey` means Windows owns the chord for us. `polling` means we fell
back. `none` means the take-over is switched off.

### Switching it off

```bash
python -m codex_cua_mcp.server --no-takeover
```

Then the only way to stop the agent is the one the harness gives you.
The flag says so in its own help text: the user then cannot stop the
agent from outside the harness.

---

## The banner text

While the Codex helper is driving, it draws a banner on screen. Both
sentences in it are user-configurable, and they live in a JSON file the
helper already reads:

```
%USERPROFILE%\.codex\computer-use\config.json
```

```json
{
  "accentColor": "#339cff",
  "direction": "ltr",
  "locale": "en-GB",
  "strings": {
    "usingComputer": "AI is controlling this computer",
    "escToCancel": "Press Ctrl+Alt+Shift+Backspace to take over"
  }
}
```

Change it with the helper script:

```bash
python scripts\set-overlay-text.py --show
python scripts\set-overlay-text.py \
    --using "AI is controlling this computer" \
    --hint "Press Ctrl+Alt+Shift+Backspace to take over"
```

`--show` prints the current file. `--defaults` restores the values
above. `--accent`, `--direction` and `--locale` set the other three
keys.

Only the keys you pass are changed; anything else in the file — including
keys this project does not know about — is left alone. The file is
created if it does not exist.

The helper reads it when it starts an overlay, so **restart the MCP
server** (or the harness session) to see the new text.

### The one rule about that file

`escToCancel` is a **label**. It does not make a key work.

The key the Codex helper watches by itself is the physical **Escape**.
`Ctrl+Alt+Shift+Backspace` works because this project registers it (see
above). If you point the label at a key nothing is watching, the banner
is making a promise the machine cannot keep — so only name a key that
actually stops the agent.

`tests/test_takeover.py` enforces this: the default hint must name
exactly the chord the server arms, so the two cannot drift apart.

### There is no banner on the `cua` backend

The `cua` driver draws the agent cursor and nothing else — its overlay
is a cursor window, not a text banner. The two strings above configure
the *Codex* helper's banner and have no effect there.

---

## Checking it works

```bash
.venv\Scripts\python.exe scripts\probe_takeover.py
```

The probe arms the chord, opens the gate, pauses, and shows that the
approvals were revoked — using the server's own wiring, not a
reimplementation of it. It reads no screen content, moves no pointer,
and presses no key: it calls the same code path the key press would.

Expected output:

```
chord: Ctrl+Alt+Shift+Backspace

1. arming the hotkey
   mode = hotkey
   RegisterHotKey succeeded -- Windows accepted the chord
...
3. pausing (what the key press does)
   approved apps before : ['some.app']
   approved apps after : []
   approvals revoked
```

If `mode` is `none`, the chord could not be registered and the polling
fallback also failed — that is worth reporting.

---

## Why the physical Escape still works too

The Codex helper watches the physical **Escape** key itself. That has
not been changed: pressing Escape during a turn still stops the helper
and shows you its own message.

The chord exists because Escape is a key you press *by accident*. It is
one keystroke away from almost anything. `Ctrl+Alt+Shift+Backspace` is
four modifiers and a key you have to mean.

Both work. Use whichever you remember.

---

## Files

| File | What it is |
|---|---|
| `src/codex_cua_mcp/takeover.py` | The controller, the chord parser, the Win32 hotkey registration. |
| `src/codex_cua_mcp/overlay.py` | Reading and writing the helper's banner config. |
| `src/codex_cua_mcp/server.py` | Wires the controller to both backends and to the tool gate. |
| `scripts/probe_takeover.py` | The live check described above. |
| `scripts/probe_takeover_event.py` | Checks whether the helper's turn-ended event exists. |
| `scripts/set-overlay-text.py` | The banner-text helper. |
| `tests/test_takeover.py` | The guarantees the lever has to make. |

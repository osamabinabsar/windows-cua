# For new users

This page is for someone who found this repository on GitHub and has never
used it before. It assumes you have a Windows PC, a terminal you are
comfortable in, and an AI harness that speaks MCP (Claude Code, Codex CLI,
OpenCode, Cursor, Gemini CLI, or anything else). If you do not know what
MCP is, the short version is: MCP is a way for a program to hand a set of
tools to an AI, and this project is one such program.

## What this is, in three sentences

1. `codex-cua-mcp` is a local MCP server that lets an AI agent in your
   harness look at your Windows desktop and drive applications in it.
2. It talks to a driver that already runs on your machine, so there is no
   service of ours in the middle, no sign-in, and no per-request fee.
3. It is built to work *alongside* you rather than instead of you: every
   action names one target window and is delivered to that window only, so
   you keep your focus, your mouse and your keyboard while the agent works.

## What it does NOT do

- **It does not take over your PC.** It does not install a low-level
  keyboard hook, it does not seize the input queue, and it does not stop
  you from using the machine while the agent is working. The only thing it
  registers system-wide is the take-over chord, so you can stop the agent.
  Windows-MCP and similar tools do the blocking kind; this is the main
  difference.
- **It does not move your real mouse.** Input is delivered to the target
  window through Windows UI Automation and background injection, so your
  pointer stays exactly where you left it. You will see a separate agent
  cursor on screen when the `cua` backend is driving.
- **Nothing leaves your machine because of this server.** It starts a local
  subprocess and speaks JSON-RPC to it over stdio. There is no HTTP client
  in it, no telemetry, and no API key.
  *Two honest caveats.* First, the accessibility tree and the screenshots
  it reads are returned to your harness, and your harness sends them to
  whichever model provider you use — so what leaves your machine is
  whatever your model already sees. This project does not add to that.
  Second, the drivers are not this project. The MIT `cua` driver has a
  telemetry switch of its own, on by default; turn it off with
  `cua-driver telemetry disable` if you want it off.
- **There is no account, no login, no API key and no usage limit.** The
  model is whatever one your harness already talks to.
- **It is not a general "control everything" agent.** The tool surface is
  sixteen fixed tools. There is no shell tool, no file tool and no
  arbitrary-code tool; the agent can only do what those sixteen tools
  allow, against the windows it names.

## Requirements

- **Windows 10 or 11** with UI Automation. The code checks `sys.platform`,
  not the Windows build number, so it runs on either, but UI Automation
  behaves differently on older builds. Windows 11 is what this is
  developed and tested against.
- **Windows PowerShell** (the one built into Windows) or PowerShell 7+.
  The installer is a `.ps1` script.
- **Git**, to clone the repository. There is no PyPI package — you clone
  the repo.
- **[uv](https://docs.astral.sh/uv/)**, which the installer uses to create
  a virtualenv and fetch Python for you. Install it with either of:

  ```powershell
  winget install --id=astral-sh.uv
  ```

  ```powershell
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

  Python 3.11 or newer is needed; `uv` will fetch it (the installer asks
  for 3.14).
- **A driver.** Pick one; you only need one:

  | Backend | What you need | Notes |
  |---|---|---|
  | `codex` (default) | The Codex **app** on Windows (or a standalone helper package), plus the `codex` CLI | The app is what ships `codex-computer-use.exe`. `npm i -g @openai/codex` installs the CLI but **not** the helper, so the CLI alone is not enough. No OpenAI account is required. |
  | `cua` | The MIT-licensed [`trycua/cua`](https://github.com/trycua/cua) driver | No OpenAI binary, no account, no proprietary anything. Install it with `irm https://cua.ai/driver/install.ps1 \| iex`. |

  Both backends expose the same sixteen tools with the same names and the
  same arguments, so your harness config does not change when you switch.

## Install in one command

Clone, then run the installer. The installer creates a virtualenv,
installs the package, registers the server with your harness, and runs
`--check` to confirm it can find the driver binary.

Every command below runs from the repository root — the folder you just
cloned.

```powershell
git clone https://github.com/osamabinabsar/windows-cua
cd windows-cua
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Harness claude
```

- `-Harness` takes `claude` (the default), `codex`, `opencode`, or `all`.
- `-Backend` takes `codex` (the default) or `cua`. Use
  `-Backend cua` if you installed the MIT driver instead — otherwise you
  silently get the OpenAI binary whichever one you meant.
- `-ExecutionPolicy Bypass` is scoped to that one command. Windows ships
  with a `Restricted` policy that blocks local `.ps1` files; if the
  shorter form `.\scripts\install.ps1 -Harness claude` works for you, use
  that instead. If your organisation manages the policy, ask it to allow
  signed local scripts.
- The script is idempotent. Run it again after a `git pull` and it
  updates the registration in place.
- The registration points at your checkout by absolute path. If you move
  or rename the folder, re-run the script so the path is corrected.
- To undo it later:

  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Uninstall
  ```

  That removes the `codex-cua` entry from `~/.claude.json`,
  `~/.codex/config.toml` and `~/.config/opencode/opencode.json`, and
  leaves everything else in those files alone.

Check that the pieces are present:

```powershell
.\.venv\Scripts\codex-cua-mcp.exe --check
```

```
backend   : codex
helper    : C:\Users\you\AppData\Local\OpenAI\Codex\runtimes\cua_node\...\codex-computer-use.exe
codex cli : C:\Users\you\AppData\Local\OpenAI\Codex\bin\...\codex.exe
```

`--check` is a discovery check, not an end-to-end test. A clean report
means "the binaries are here", not "the server works".

Then confirm the harness sees it: `claude mcp list` shows `codex-cua`, and
`/mcp` inside a Claude Code session lists the sixteen tools.

## Try it in two minutes

Start your harness in any folder. Open Notepad yourself so there is
something to act on, and leave it in the background — that is the point.

**Step 1 — look around (no prompt).** Say:

> List the open apps on my PC.

The agent calls `list_apps`, which is read-only and never asks permission.
You get back something like:

```
- Notepad  [id=notepad]  (running)
    window id=12345 title='Untitled - Notepad'
```

**Step 2 — read the window (this is the first prompt).** Say:

> Read the state of the Notepad window with id 12345.

The agent calls `get_window_state(app="notepad", window_id=12345)`, using
the `app` id and the window id that `list_apps` gave it. Before it can,
your harness shows you an approval prompt:

```
Allow Computer Use to control 'Notepad'?
App id: notepad

The agent will be able to read this window and send input to it without bringing it to the foreground.
```

The helper reports a risk level for some apps; when it does, the prompt
carries a `(risk: ...)` note on the first line.

Approve it. The tool returns the accessibility tree for that window, with
numbered elements.

**Step 3 — one action.** Say:

> Click the element with index 12.

The agent calls `click_element(app="notepad", window_id=12345,
element_index=12)`. Your cursor does not move, and Notepad does not jump
to the front.

**Step 4 — put text in.** Say:

> Put the text "hello from the agent" into element 14.

On the `codex` backend, `set_value(element_index=14, value="hello from the
agent")` is the reliable path: it sets the control's value without needing
focus. `type_text` sends keystrokes to whatever holds focus in the target
window, so it needs an `element_index` too, and if nothing holds focus
there the characters are lost while the call still reports success.

**Step 5 — verify.** Say:

> Read the state of the Notepad window again.

Three rules for the whole session:

- **Element indexes are only valid for the observation that produced
  them.** Observe, act once, observe again. The same applies to
  coordinates, which are only valid for the screenshot they came from.
- **Your foreground window, focus and mouse are yours.** Every tool
  except `activate_window` leaves them alone. The one exception is the
  `cua` backend, which may briefly swap focus when a window refuses
  background input, and it restores your previous window afterwards.
- **You can stop the agent at any moment** with `Ctrl+Alt+Shift+Backspace`.
  See the next section.

The sixteen tools are `list_apps`, `list_windows`, `get_window`,
`get_window_state`, `read_accessibility_tree`, `click`, `click_element`,
`type_text`, `press_key`, `scroll`, `drag`, `set_value`,
`perform_secondary_action`, `launch_app`, `activate_window` and `status`.
The full table is in the [README](../README.md#tools).

## How it differs from Windows-MCP and other take-over tools

[Windows-MCP](https://github.com/CursorTouch/Windows-MCP) is a good project
with a different design: it takes the machine while the agent works, so
the agent's inputs are the real ones.

| | Windows-MCP | codex-cua-mcp |
|---|---|---|
| While the agent acts | Small mouse movements stay blocked — its README says "During AI control, small mouse movements remain blocked without pausing the current tool" | Your input keeps working |
| Mouse | Moves the real on-screen pointer | Stays where you left it. The `cua` backend draws a separate agent cursor; the `codex` backend draws a text banner |
| Focus | The agent's window takes it | Left alone, except `activate_window` |
| Screenshots of covered windows | Not supported | Supported (Windows.Graphics.Capture) |
| Taking back control | `Ctrl+Alt+Shift+Backspace` returns control to you, until 10 seconds of inactivity | `Ctrl+Alt+Shift+Backspace` pauses the agent; press again to resume |
| Per-app consent | App-level | App-level, surfaced as an MCP elicitation you answer (on the `codex` backend) |

The other difference is where the model comes from. Windows-MCP exposes
its tools to a connected MCP client, so it needs the same LLM setup you
already have. This server is the tool layer only: it has no model, so it
works with whatever your harness already talks to, and it adds no network
traffic of its own.

One thing both share is the chord, `Ctrl+Alt+Shift+Backspace`, and both
mean it as "I am taking over". Here it pauses rather than cancels, so you
can hand control back.

## Safety and the approval model

**What you will be asked.** On the `codex` backend, the first action against
any app raises one prompt (the text is in the previous section). You answer
it, and the answer decides. The approved-app key is only written to the
wire after you accept.

- `list_apps`, `list_windows`, `get_window` and `status` never prompt.
  They are read-only.
- Approvals last for the session, so you are asked once per app, not once
  per action.
- If your harness does not support MCP elicitation, the action **fails**
  with an error from the harness, and the app is never approved. It does
  not proceed unapproved.

**How to say no.** Three ways:

1. Decline the prompt in your harness. The action fails; the agent is
   told the app was refused.
2. Accept the prompt, then set `allow` to `false` in the form it shows.
   The `reason` field is a free-text note back to the agent — use it to
   explain ("this is my bank window, do not touch it").
3. Press `Ctrl+Alt+Shift+Backspace`. That abandons the action in flight
   and takes down the on-screen overlay. On the `codex` backend it also
   **revokes every approval** the agent had been given, including the
   ones you already accepted. Press it again to resume; the agent then
   has to ask you from scratch.

**When you want no prompts at all.** `--auto-approve` skips the approval
step entirely. It is not recommended: the agent may then act on any app on
your machine without asking. If you use it, keep the take-over chord
armed.

**The banner.** While the `codex` helper is driving, it draws a banner on
screen. Both sentences are yours to set, in a JSON file the helper already
reads — no binary is patched:

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

The helper's own built-in first line is "Codex is using your computer";
this file replaces it with whatever you put in `usingComputer`.

Or, from the repository root, using the virtualenv the installer created:

```powershell
.\.venv\Scripts\python.exe .\scripts\set-overlay-text.py --using "AI is controlling this computer" --hint "Press Ctrl+Alt+Shift+Backspace to take over"
```

`--show` prints the current file instead of changing it.

The helper reads the file when it starts an overlay, so restart the MCP
server to see the change. One rule: `escToCancel` is a *label*. It does
not make a key work — point it at a key that really stops the agent.

There is no text banner on the `cua` backend: that driver draws an agent
cursor and nothing else. The file above only changes the `codex` helper's
banner.

**The `cua` backend, honestly.** The MIT driver has no per-app approval
store, so on that backend this server does **not** raise the per-app
prompt. The driver applies its own permission model to each action
instead. The take-over chord still works and still stops everything. If
the per-app prompt matters to you, use the `codex` backend.

## Where to go next

| Doc | What it is for |
|---|---|
| [The README](../README.md) | The whole project: install, both backends, the full tool table, the troubleshooting table. |
| [Using it in other harnesses](using-other-harnesses.md) | Pointing Claude Code, OpenCode, Codex CLI, Cursor, Gemini CLI or anything else at this server, the config format for each, and how to tell an agent the tools exist. |
| [Installing on another PC](deploying-to-other-pcs.md) | Putting this on a second machine, a friend's PC, or someone else's PC. |
| [Taking the computer back](takeover-hotkey.md) | The take-over chord in full, and the banner text. |
| [Architecture](architecture.md) | How the pieces fit together, and the three undocumented helper behaviours this project works around. |
| [Alternatives](alternatives.md) | The other projects in this space, and how they differ. |
| [The invisible cursor](troubleshooting/invisible-cursor.md) | If the mouse pointer disappears over parts of the screen, this is a Windows display-driver fault, not this server. |

**On another PC:** clone the repository there and run the same installer.
The one thing that does not copy across is the helper binary — it comes
from the Codex app's runtime, not from this repository — so install the
Codex app on the new machine, or use the `cua` backend, which has no such
dependency. See [Installing on another PC](deploying-to-other-pcs.md) for
the full procedure.

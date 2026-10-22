# codex-cua-mcp

**Collaborative Windows computer use for any MCP agent harness — no cloud, no
account, no usage limits.**

Windows-MCP and similar tools take over your PC: while the agent drives the
mouse and keyboard, you can't. This server does the opposite. Every action
names a target window and is delivered to *that window only*, without raising
it, so you keep your focus, your mouse, and your keyboard while the agent
works alongside you.

It is backed by the **local** Codex computer-use helper — a Windows binary
that ships with the Codex app. No model runs through it, no request leaves your
machine, and there is nothing to be rate-limited by. The model is whatever
your harness already talks to: Claude Code, OpenCode, Hermes, Codex, Gemini
CLI, Cursor — anything that speaks MCP.

> "No cloud" describes *this server*: it makes no network calls of its own.
> The accessibility tree and the screenshot it returns go to your harness,
> which passes them to your model provider — so what leaves your machine is
> whatever your model already sees.

It also has a **second backend** that needs no OpenAI binary at all: the
MIT-licensed [`trycua/cua`](https://github.com/trycua/cua) driver. Same
sixteen tools, same names, same arguments — a harness config does not
change when you switch. See [Two backends](#two-backends).

---

## Why this is different from Windows-MCP

| | Windows-MCP | codex-cua-mcp |
|---|---|---|
| Focus during agent action | Taken from you (low-level input hooks block you) | Left alone — actions are routed to the target window |
| Mouse | Moves the real cursor | Stays where you left it |
| Screenshots of occluded windows | No | Yes (Windows.Graphics.Capture) |
| Cloud / usage limits | No, but no model either | No — the model is your harness's |
| Per-app consent | App-level | App-level, surfaced as an MCP elicitation you answer |

The collaboration is not a wrapper trick. It is a property of how the helper
delivers input: per-window UI Automation actuation and coordinate-routed
background injection, instead of a global cursor-and-keyboard takeover.

---

## Install

### One machine

```powershell
git clone https://github.com/osamabinabsar/windows-cua
cd windows-cua
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Harness claude
```

> Windows ships with a `Restricted` execution policy, which blocks local
> `.ps1` files. The `-ExecutionPolicy Bypass` above is scoped to that
> one command. If your organisation manages the policy, ask it to allow
> signed local scripts instead.

The installer creates a virtualenv, installs the package, registers the
server with your harness, and verifies it can find the helper. It is
idempotent — run it again after a `git pull` and it will update in place.

`-Harness` accepts `claude`, `codex`, `opencode`, or `all`.
`-Backend` accepts `codex` (the default) or `cua`.

To install on **another PC**, clone the repo there and run the same
script. See [Deploying to other PCs](docs/deploying-to-other-pcs.md).
The one thing that does not copy across is the helper binary: it comes
from the Codex app's runtime, not from this repo.

### Manually

`uv pip` installs into a virtualenv, so make one first:

```bash
uv venv
uv pip install -e .
```

Or run it straight from the checkout, without a separate install step — `uv`
makes a virtualenv for it and installs the package into that:

```bash
uv run --with "mcp>=2" python -m codex_cua_mcp.server
```

**Requirements**

- Windows 10 or 11 with UI Automation. (The code checks `sys.platform`,
  not the Windows version — it runs on either, but UI Automation
  behaves differently on older builds.)
- Python 3.11+, or [`uv`](https://docs.astral.sh/uv/), which will fetch
  one. `uv` is what the installer uses.
- Git, to clone the repo. There is no PyPI package yet.
- For the `codex` backend: the Codex **app** (or a standalone helper
  package), which supplies `codex-computer-use.exe`; and the `codex`
  CLI, used for the approval handshake.

  > The Codex **CLI** on its own is not enough. `npm i -g @openai/codex`
  > ships the CLI but not the helper. The helper arrives with the Codex
  > desktop app, which installs a runtime the server searches:

  ```
  %LOCALAPPDATA%\OpenAI\Codex\runtimes\cua_node\*\bin\node_modules\@oai\cua\bin\windows\codex-computer-use.exe
  %LOCALAPPDATA%\OpenAI\Codex\runtimes\cua_node\*\bin\node_modules\@oai\sky\bin\windows\codex-computer-use.exe
  ```

  A current install carries **both** package names, and the server
  searches both. Override with `CODEX_COMPUTER_USE_BIN` if yours lives
  elsewhere.

  If you would rather not depend on an OpenAI binary at all, use the
  `cua` backend below — it needs neither the app nor an account.

Verify the setup:

```bash
.venv\Scripts\codex-cua-mcp.exe --check
```

`codex-cua-mcp` is on your `PATH` only while the virtualenv is
activated; the full path works from anywhere. `--check` reports which
binaries it found — it is a *discovery* check, not a test of the whole
chain, so read a clean report as "the pieces are present", not "the
server works".

### The MIT backend (no OpenAI binary)

If you would rather not depend on a proprietary binary, install the
[`trycua/cua`](https://github.com/trycua/cua) driver instead and run the
server with `--backend cua`:

```bash
codex-cua-mcp --backend cua --check
```

The driver is MIT-licensed and Rust; it needs no Codex CLI and no OpenAI
account. See [Two backends](#two-backends).

---

## Register with your harness

The installer does this for you
(`powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Harness claude`).
To do it by hand:

> The examples below start the **default `codex` backend**. If you
> installed the MIT driver instead, add `--backend cua` to the server's
> arguments — otherwise you silently get the OpenAI binary whichever one
> you meant. `WINDOWS_CUA_BACKEND=cua` in the server's environment does
> the same thing.

### Claude Code

```bash
claude mcp add --scope user codex-cua -- \
  C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server
```

Or as JSON (`~/.claude.json` / `.mcp.json`):

```json
{
  "mcpServers": {
    "codex-cua": {
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"],
      "env": {}
    }
  }
}
```

### OpenCode

`~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "codex-cua": {
      "type": "local",
      "command": ["C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe", "-m", "codex_cua_mcp.server"],
      "enabled": true
    }
  }
}
```

### Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.codex-cua]
command = 'C:\path\to\windows-cua\.venv\Scripts\python.exe'
args = ['-m', 'codex_cua_mcp.server']
```

### Any other MCP client

The server speaks MCP over stdio. Point any client at:

```
C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server
```

If you did not use the installer and have no virtualenv, the equivalent is:

```
uv run --project <path> python -m codex_cua_mcp.server
```

---

## Uninstalling

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Uninstall
```

That removes the `codex-cua` entries from `~/.claude.json`,
`~/.codex/config.toml` and `~/.config/opencode/opencode.json` — and
nothing else. The rest of each file is edited in place, byte for byte,
so your other settings and their formatting survive. The virtualenv,
the repository and every other setting are left alone.

It is safe to run twice, and safe to run when you never installed
anything. If it cannot remove one of the entries it says which file and
leaves that file unchanged rather than risking it, and exits non-zero.

---

## Taking the computer back

One chord stops the agent, from outside the harness, at any moment:

```
Ctrl + Alt + Shift + Backspace
```

Press it once and the agent loses the computer. Press it again and it
gets it back. No panel, no tray icon, no prompt to answer.

One press stops the action in flight, takes the on-screen banner down,
revokes every approval the agent had been given, and leaves the server
running so it can be resumed. While it is held, every computer-use call
fails with a message telling the agent to stop and wait for you.

The chord is armed automatically whenever the server starts. Change it
with `--takeover-key "Ctrl+Alt+Shift+Pause"`, or switch it off with
`--no-takeover`.

[Full documentation →](docs/takeover-hotkey.md)

---

## The banner

While the Codex helper is driving, it draws a banner on screen. Both
sentences in it are yours to set, in a JSON file the helper already
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

```bash
python scripts\set-overlay-text.py --show
python scripts\set-overlay-text.py \
    --using "AI is controlling this computer" \
    --hint "Press Ctrl+Alt+Shift+Backspace to take over"
```

The helper reads it when it starts an overlay, so restart the MCP
server to see the change. One rule: `escToCancel` is a *label*. It does
not make a key work — point it at a key that actually stops the agent.

[Full documentation →](docs/takeover-hotkey.md)

---

## Tools

| Tool | Purpose |
|---|---|
| `list_apps` | Installed apps and the windows each owns. Start here. |
| `list_windows` | Every open, targetable window. |
| `get_window` | Rehydrate a window by id after a lost binding. |
| `get_window_state` | Accessibility tree (with element indexes) and/or screenshot. |
| `read_accessibility_tree` | Just the tree text — cheaper than a full state read. |
| `click` | Click at window-relative coordinates. |
| `click_element` | Click an element by index, via UI Automation. **Prefer this.** |
| `type_text` | Type literal text. Pass `element_index` to say where it goes. |
| `press_key` | Key or chord (`Return`, `Control_L+a`, …). Also takes `element_index`. |
| `scroll` | Scroll from a window-relative point. |
| `drag` | Drag between two window-relative points. |
| `set_value` | Replace an editable element's value by index. Empty clears it. |
| `perform_secondary_action` | `Expand`, `Collapse`, `Raise`, `Scroll Up`, … |
| `launch_app` | Launch an app by id or `.exe` path. |
| `activate_window` | Brings a window to the front. The one tool that takes focus on purpose. |
| `status` | Which binaries are in use, what is approved, helper log. |

Every action except `activate_window` leaves your foreground window, your
focus, and your mouse cursor exactly where they were — with one
exception. On the `cua` backend, a window that refuses background input
makes the driver ask for `delivery_mode: "foreground"`, which it
performs as a brief focus swap that restores your previous window
afterwards. The server never fronts a window on a guess.

---

## Two backends

The server speaks to one of two drivers. Both present the same sixteen
tools, so **your harness config does not change when you switch**.

| | `codex` (default) | `cua` |
|---|---|---|
| Driver | `codex-computer-use.exe`, ships with the Codex app | `cua-driver.exe`, from [trycua/cua](https://github.com/trycua/cua) |
| Licence | Proprietary (OpenAI) | MIT |
| Needs the Codex app | Yes | No |
| Needs an OpenAI account | No | No |
| Approval | Per-app MCP elicitation you answer | The driver's own permission model |
| Element handles | Per-observation indexes | Stable `element_token`s |
| Input delivery | Per-window UI Automation | Explicit `delivery_mode`, background first |

Switch with a flag:

```bash
python -m codex_cua_mcp.server --backend cua
```

or the `WINDOWS_CUA_BACKEND=cua` environment variable. `--driver` (or
`CUA_DRIVER_BIN`) points at a specific `cua-driver.exe` if the
auto-detection misses it.

### The two differ in three places

Both backends are honest about where they are not equivalent:

1. **`perform_secondary_action`.** The Codex helper has a real
   accessibility-action tool (`Expand`, `Collapse`, `Toggle`, `Invoke`).
   The cua driver does not — its closest is `invoke_menu`, which walks an
   application *menu* path. So on the cua backend, `File>Save As` goes
   to `invoke_menu`, a plain action name like `Invoke` is honoured by
   clicking the element, and anything it cannot express **raises** rather
   than silently doing nothing.
2. **Background delivery.** Some window classes (dialogs, for instance)
   refuse background input. The cua driver detects that, says so, and
   asks for `delivery_mode: "foreground"` — which it performs as a brief
   focus swap that **restores your previous foreground window** after.
   The client takes that escalation; it never fronts a window on a guess.
3. **Text delivery.** The Codex helper's `type_text` goes to whatever
   holds focus, so it needs an `element_index` to be reliable. The cua
   driver routes keystrokes to the target window itself, so it works
   without one — but pass `element_index` when you mean a specific
   control.

---

## Where the text goes

On the `codex` backend, `type_text` and `press_key` send keystrokes to
the control that holds focus in the target window. **If nothing holds
focus there, the characters are lost and the call still reports
success.** So always pass `element_index`:

```
type_text(text="hello", element_index=3)   # clicks element 3, then types
set_value(element_index=3, value="hello")   # no focus needed — the reliable one
```

`set_value` with an empty value clears the field. On Windows, `click`
needs coordinates and `click_element` is the element-targeted path.

The `cua` driver routes keystrokes to the target window itself, so
`type_text` works there without an `element_index` — but pass one anyway
when you mean a specific control.

---

## Approval model

The helper gates every app behind per-app consent. This server surfaces
that as an MCP **elicitation**: your harness shows you a prompt, you answer,
and the answer decides.

- The approved-app key is written to the wire **only** after you accept.
- Approvals persist for the session.
- `list_apps`, `list_windows`, `get_window`, and `status` never prompt —
  they are read-only.

If your harness does not support elicitation, the action fails with a clear
error naming the app, rather than proceeding unapproved.

To skip prompts entirely (not recommended):

```bash
python -m codex_cua_mcp.server --auto-approve
```

---

## Workflow that works

Element indexes are only valid for the observation that produced them, and
coordinates are only valid for the screenshot they came from. Observe, act
once, re-observe.

```
1. list_apps              -> find the app, get its window id
2. get_window_state       -> read the tree, note element indexes
3. click_element(index=…) -> exactly one action
4. get_window_state       -> verify what changed
```

---

## Layout

```
src/codex_cua_mcp/
  transport.py     JSON-RPC over the helper's stdio; approval handshake
  client.py        the sky Windows API, typed (Codex backend)
  cua_backend.py   a small MCP client for the trycua/cua driver
  cua_client.py    the same sky API, over the cua driver
  server.py        MCP surface + elicitation-based approval
  takeover.py      the take-over chord, and the gate it closes
  overlay.py       the helper's banner text
scripts/           protocol probes and a smoke test
tests/             the test suite; CI runs it on every push
```

Every script in there is indexed in
[`scripts/README.md`](scripts/README.md), with what it touches and what
it does not.

---

## Checking the drivers

Codex ships a new helper build now and then, and the helper's
undocumented behaviours are the fragile part of this project. When it
updates, re-check that the workarounds still hold:

```bash
.venv\Scripts\python.exe scripts\probe_helpers.py
```

It runs eight checks against the live helper and reports pass/fail for
each. The target is Character Map, which cannot hold a user document, so
running it cannot damage your work.

The cua backend has its own probe, with the same shape:

```bash
.venv\Scripts\python.exe scripts\probe_cua_backend.py
```

Twenty-four checks against the live `cua-driver.exe`. Run it after
updating the driver — the driver moves faster than the helper, and its
error messages are specific enough to tell you exactly what changed.

See [the architecture doc](docs/architecture.md) for what the three
workarounds are and why each is needed.

---

## Troubleshooting

| Symptom | Where to look |
|---|---|
| `ModuleNotFoundError: No module named 'codex_cua_mcp.takeover'` on a fresh clone | You are running an old checkout against new code, or the clone is missing files. Re-clone, or `git pull`. |
| `Could not find codex-computer-use.exe` | The Codex **CLI** is not enough — the helper ships with the Codex app. See [Requirements](#install). Or use `--backend cua`. |
| `failed to launch codex app-server: program not found` | The helper could not find the `codex` CLI. Set `CODEX_CLI_PATH` to its full path, or put `codex` on `PATH`. |
| `codex-cua-mcp` is not recognised | The virtualenv is not activated. Use `.venv\Scripts\codex-cua-mcp.exe`. |
| The mouse pointer disappears over parts of the screen (Explorer nav pane, VS Code file tree, Windows Search list, chat messages) — the item still highlights, but the arrow is gone | [The invisible cursor](docs/troubleshooting/invisible-cursor.md) |
| The agent did not stop when you pressed the chord | `status` reports the take-over mode. `polling` means another app owns the chord and the fallback is in use; `none` means it is switched off. See [Taking the computer back](docs/takeover-hotkey.md). |

The invisible cursor is a Windows 11 / display-driver compositing
fault, not this server and not the MCP tooling. The doc records what
was measured on the machine, what was ruled out, the three candidate
mechanisms, and the by-eye tests that separate them — plus
`scripts/fix-mouse-cursor.ps1`, which reports and (with Administrator,
and a full power cycle) applies the MPO overrides.

## More

| Doc | What it is for |
|---|---|
| [Using it in other harnesses](docs/using-other-harnesses.md) | Pointing Claude Code, OpenCode, Codex CLI, Cursor, Gemini CLI or anything else at this server, and telling an agent it exists. |
| [Deploying to other PCs](docs/deploying-to-other-pcs.md) | Installing on a second or third machine, and what does not copy across. |
| [For new users](docs/for-new-users.md) | The short version, if you have never set up an MCP server before. |
| [Taking the computer back](docs/takeover-hotkey.md) | The take-over chord, and the banner text. |
| [Architecture](docs/architecture.md) | How the pieces fit together, and the three undocumented helper behaviours this project works around. |
| [Alternatives](docs/alternatives.md) | The other projects in this space, and how they differ. |

## Licence

MIT. See [LICENSE](LICENSE).

# codex-cua-mcp

**Collaborative Windows computer use for any MCP agent harness — no cloud, no
account, no usage limits.**

Windows-MCP and similar tools take over your PC: while the agent drives the
mouse and keyboard, you can't. This server does the opposite. Every action
names a target window and is delivered to *that window only*, without raising
it, so you keep your focus, your mouse, and your keyboard while the agent
works alongside you.

It is backed by the **local** Codex computer-use helper — a Windows binary
that ships with Codex CLI. No model runs through it, no request leaves your
machine, and there is nothing to be rate-limited by. The model is whatever
your harness already talks to: Claude Code, OpenCode, Hermes, Codex, Gemini
CLI, Cursor — anything that speaks MCP.

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
.\scripts\install.ps1 -Harness claude
```

The installer creates a virtualenv, installs the package, registers the
server with your harness, and verifies it can find the helper. It is
idempotent — run it again after a `git pull` and it will update in place.

`-Harness` accepts `claude`, `codex`, `opencode`, or `all`.

To install on **another PC**, clone the repo there and run the same script.
The only thing each machine needs is [Codex CLI](https://github.com/openai/codex)
installed, because that is what supplies the helper binary.

### Manually

```bash
uv pip install -e .
```

Or run it without installing:

```bash
uv run --with "mcp>=2" python -m codex_cua_mcp.server
```

**Requirements**

- Windows 11 (or Windows 10 with UI Automation)
- [Codex CLI](https://github.com/openai/codex) installed — it ships
  `codex-computer-use.exe` and provides the `codex` binary used for the
  approval handshake. The server finds both automatically:

  ```
  %LOCALAPPDATA%\OpenAI\Codex\runtimes\cua_node\*\bin\node_modules\@oai\sky\bin\windows\codex-computer-use.exe
  ```

  Override with `CODEX_COMPUTER_USE_BIN` if yours lives elsewhere.

- Python 3.11+ (or `uv`, which will fetch one)

Verify the setup:

```bash
codex-cua-mcp --check
```

---

## Register with your harness

The installer does this for you (`.\scripts\install.ps1 -Harness claude`).
To do it by hand:

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
| `activate_window` | **The only tool that takes focus.** Explicit escape hatch. |
| `status` | Which binaries are in use, what is approved, helper log. |

Every action except `activate_window` leaves your foreground window, your
focus, and your mouse cursor exactly where they were.

### Where the text goes

`type_text` and `press_key` send keystrokes to the control that holds focus
in the target window. **If nothing holds focus there, the characters are
lost and the call still reports success.** So always pass `element_index`:

```
type_text(text="hello", element_index=3)   # clicks element 3, then types
set_value(element_index=3, value="hello")   # no focus needed — the reliable one
```

`set_value` with an empty value clears the field. On Windows, `click`
needs coordinates and `click_element` is the element-targeted path.

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
  transport.py   JSON-RPC over the helper's stdio; approval handshake
  client.py      the sky Windows API, typed
  server.py      MCP surface + elicitation-based approval
scripts/         protocol probes and a smoke test
```

---

## Checking the helper

Codex ships a new helper build now and then, and the helper's
undocumented behaviours are the fragile part of this project. When it
updates, re-check that the workarounds still hold:

```bash
.venv\Scripts\python.exe scripts\probe_helpers.py
```

It runs eight checks against the live helper and reports pass/fail for
each. The target is Character Map, which cannot hold a user document, so
running it cannot damage your work.

See [the architecture doc](docs/architecture.md) for what the three
workarounds are and why each is needed.

## Licence

MIT. See [LICENSE](LICENSE).

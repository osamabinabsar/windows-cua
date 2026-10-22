# Using codex-cua-mcp from other AI harnesses

You have `codex-cua-mcp` installed on one PC. This page answers four questions:

1. How do I use it from a different AI harness?
2. How do I use it on a different PC?
3. How do my friends use it?
4. How do strangers who find it on GitHub use it?

Short version: the server is a plain MCP process that speaks over stdio. Every
harness that can spawn a process can run it. The only thing that changes
between harnesses is the file format of the config that points at it.

---

## The one thing that does not change (the stdio command)

The server speaks MCP over stdio. It is a process your harness starts, talks
to, and shuts down. The command that starts it is the same on every harness
and every PC:

```powershell
C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server
```

On the machine this was written on, that path is:

```powershell
C:\Users\Anagh\Projects\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server
```

Variants you may need:

```powershell
C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server --backend cua
C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server --check
uv run --project C:\path\to\windows-cua python -m codex_cua_mcp.server
```

If you installed the package (the console script in `pyproject.toml` is
`codex-cua-mcp`), `codex-cua-mcp` works too, as long as the venv's `Scripts`
directory is on `PATH`. The `-m codex_cua_mcp.server` form is safer in a
harness config, because it names the interpreter explicitly and does not
depend on `PATH`.

### The flags the server actually implements

Read out of `main()` in `src/codex_cua_mcp/server.py`:

| Flag | Default | Environment variable | What it does |
|---|---|---|---|
| `--backend {codex,cua}` | `codex` | `WINDOWS_CUA_BACKEND` | Which driver to use. `codex` is the `codex-computer-use.exe` helper that ships with the Codex **app**. `cua` is the MIT-licensed trycua/cua driver. |
| `--helper PATH` | auto-detected | `CODEX_COMPUTER_USE_BIN` | Where `codex-computer-use.exe` lives. |
| `--codex-cli PATH` | auto-detected | `CODEX_CLI_PATH` | Where the `codex` binary lives. Used only for the approval handshake. |
| `--driver PATH` | auto-detected | `CUA_DRIVER_BIN` | Where `cua-driver.exe` lives. |
| `--auto-approve` | off | — | Approves app requests without asking you. Not recommended. |
| `--check` | — | — | Verify the driver is discoverable, print what it found, and exit. |
| `--no-takeover` | off | — | Do not arm the take-over hotkey. If you set this, you have no way to stop the agent from outside the harness. |
| `--takeover-key CHORD` | `Ctrl+Alt+Shift+Backspace` | — | The chord you press to take the PC back. |

`Ctrl+Alt+Shift+Backspace` is the default chord, defined as
`DEFAULT_TAKEOVER_CHORD` in `src/codex_cua_mcp/takeover.py`. It is
armed automatically whenever the server starts. See
[Taking the computer back](takeover-hotkey.md) for what one press does
and how to change the chord.

### Check the server before you point a harness at it

Run this first. It is a *discovery* check: it reports which binaries it found
and tests nothing else. A clean report means the pieces are in place, not
that the whole chain works. If the helper path and the codex CLI path
print, the next problem you hit is in the harness config rather than in the
driver lookup.

```powershell
C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server --check
```

On the `codex` backend it prints:

```
backend   : codex
helper    : C:\Users\you\AppData\Local\OpenAI\Codex\runtimes\cua_node\...\codex-computer-use.exe
codex cli : C:\Users\you\AppData\Local\OpenAI\Codex\bin\...\codex.exe
```

On the `cua` backend it prints `backend : cua` and the driver path instead.

If it cannot find the helper, it prints an error to stderr and exits with
code 2. That means the helper is not on this machine (or
`CODEX_COMPUTER_USE_BIN` points somewhere wrong), and no harness config
will fix it. The helper ships with the Codex **app** — the Codex CLI on
its own does not include it. Use `--backend cua` if you would rather
not depend on an OpenAI binary at all.

### The same server on a second PC

The server is per-machine. Each PC needs its own checkout, its own virtualenv
and its own driver binary. Nothing is shared over the network: the harness
spawns the server locally, the server drives the local Windows desktop, and
no request leaves the machine.

To install on another PC:

```powershell
git clone https://github.com/osamabinabsar/windows-cua
cd windows-cua
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Harness claude
```

Windows ships with a `Restricted` execution policy that blocks local
`.ps1` files, hence the `-ExecutionPolicy Bypass` above. It is scoped
to that one command.

The installer's `-Harness` accepts `claude`, `codex`, `opencode` or `all`
(`all` registers the first three). Every other harness on this page has to be
configured by hand. The installer also has a `-Backend` parameter, which
accepts `codex` (default) or `cua` and is passed through to the server as
`--backend`.

The installer is idempotent. Run it again after a `git pull` and it updates in
place. It also creates the virtualenv, installs the package, and verifies it
can find the helper.

What each machine needs:

- Windows 10 or 11 with UI Automation.
- The Codex **app** (or a standalone helper package), which supplies
  `codex-computer-use.exe`, and the [Codex CLI](https://github.com/openai/codex),
  which supplies the `codex` binary used for the approval handshake. The
  server auto-detects both; override with `CODEX_COMPUTER_USE_BIN` and
  `CODEX_CLI_PATH` if yours live elsewhere. The CLI on its own is not
  enough — `npm i -g @openai/codex` does not ship the helper.
  If you would rather not depend on a proprietary binary, install the
  MIT-licensed trycua/cua driver and run with `--backend cua` instead.
- Python 3.11+, or `uv`, which will fetch one.

Because the path to the virtualenv differs per machine and per username, each
PC's harness config needs its own absolute path. Write the path out in full
rather than a relative one — the child's working directory is not guaranteed
to be the repo, so a relative path can resolve somewhere else entirely.

---

## Per-harness configuration

The installer only writes config for Claude Code, Codex CLI and OpenCode.
Everything else is by hand.

### The table

| Harness | Config file | Top-level key | Shape |
|---|---|---|---|
| Claude Code | `~/.claude.json` (user), `.mcp.json` (project) | `mcpServers` | JSON, `command` + `args` |
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` | `mcpServers` | JSON, `command` + `args` |
| Codex CLI | `~/.codex/config.toml`, `.codex/config.toml` (project) | `[mcp_servers.<name>]` | TOML, `command` + `args` |
| OpenCode | `~/.config/opencode/opencode.json`, `opencode.json` (project) | `mcp` | JSON, `type: local`, `command` is an **array** |
| Cursor | `~/.cursor/mcp.json`, `.cursor/mcp.json` (project) | `mcpServers` | JSON, `command` + `args` |
| Windsurf | `%AppData%\devin\mcp_config.json` | `mcpServers` | JSON, `command` + `args` |
| Zed | `settings.json` (Command Palette: "zed: open settings file") | `context_servers` | JSON, `command` + `args` |
| Gemini CLI | `~/.gemini/settings.json`, `.gemini/settings.json` (repo) | `mcpServers` | JSON, `command` + `args` |
| Continue (VS Code, JetBrains) | `%USERPROFILE%\.continue\config.yaml` | `mcpServers` | YAML **list**, not a map |
| Cline | `~/.cline/data/settings/cline_mcp_settings.json` | `mcpServers` | JSON, `command` + `args` |
| Roo Code | `mcp_settings.json` (global), `.roo/mcp.json` (project) | `mcpServers` | JSON, `command` + `args` |
| Goose | `%APPDATA%\Block\goose\config\config.yaml` | `extensions` | YAML, `cmd` + `args` |
| OpenHands | `~/.openhands/mcp.json` | `mcpServers` | JSON, `command` + `args` |
| VS Code (Copilot agent mode) | `.vscode/mcp.json` (workspace), `mcp.json` (profile) | `servers` | JSON, `type: stdio` required |
| VS Code portable / Copilot | `.mcp.json` (workspace root), `~/.copilot/mcp-config.json` | `mcpServers` | JSON, `command` + `args` |
| LM Studio | `mcp.json` (Program tab → Install → Edit mcp.json) | `mcpServers` | JSON, `command` + `args` |
| AnythingLLM | `<ANYTHINGLLM_STORAGE_DIR>\plugins\anythingllm_mcp_servers.json` | `mcpServers` | JSON, `command` + `args` |
| LibreChat | `librechat.yaml` | `mcpServers` | YAML **map** |
| Kiro | `~/.kiro/settings/mcp.json`, `.kiro/settings/mcp.json` (workspace) | `mcpServers` | JSON, `command` + `args` |
| Amazon Q Developer CLI | `~/.aws/amazonq/mcp.json`, `.amazonq/mcp.json` (workspace) | `mcpServers` | JSON, `command` + `args` |
| JetBrains Junie | `~/.junie/mcp/mcp.json`, `.junie/mcp/mcp.json` (project) | `mcpServers` | JSON, `command` + `args` |
| Warp | `~/.warp/.mcp.json`, `.warp/.mcp.json` (repo root) | `mcpServers` | JSON, `command` + `args` |
| Crush (Charm) | `crush.json` / `.crush.json` (project), `~/.config/crush/crush.json` (user) | `mcp` | JSON, `type: stdio` |
| Qwen Code | `~/.qwen/settings.json`, `.qwen/settings.json` (project) | `mcpServers` | JSON, `command` + `args` |
| MCP Inspector | `~/.mcp-inspector/mcp.json` | `mcpServers` | JSON — this one is a test harness, not a client |
| Open WebUI | no config file | — | **HTTP only.** Needs a stdio→HTTP bridge. |
| Aider | — | — | **No MCP client at all.** |

### The snippets

Every snippet below uses the same command. Substitute the real path to the
virtualenv on your machine.

#### Claude Code

This is exactly what the repo's installer runs:

```powershell
claude mcp add --scope user codex-cua -- C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server
```

Scopes: `--scope local` (default; `~/.claude.json` under the current project),
`--scope project` (`.mcp.json` in the project root, shared through version
control), `--scope user` (`~/.claude.json`, all your projects).

As JSON, in `~/.claude.json` or `.mcp.json`:

```json
{
  "mcpServers": {
    "codex-cua": {
      "type": "stdio",
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"],
      "env": {}
    }
  }
}
```

#### Claude Desktop

File: `%APPDATA%\Claude\claude_desktop_config.json`. Open it with
Settings → Developer → Edit Config. Fully quit and restart Claude Desktop
after editing — it does not pick the change up otherwise.

```json
{
  "mcpServers": {
    "codex-cua": {
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"]
    }
  }
}
```

#### Codex CLI

File: `~/.codex/config.toml` (user) or `.codex/config.toml` (project, trusted
projects only). The table header is `[mcp_servers.<server-name>]`, `command` is
required for stdio, and `args`, `env`, `env_vars` and `startup_timeout_sec`
are optional. The default `startup_timeout_sec` is 10 seconds, which is short
if the helper has to cold-start.

```toml
[mcp_servers.codex-cua]
command = 'C:\path\to\windows-cua\.venv\Scripts\python.exe'
args = ['-m', 'codex_cua_mcp.server']
```

#### OpenCode

File: `~/.config/opencode/opencode.json` (global) or `opencode.json` in the
project root. JSON and JSONC (`opencode.jsonc`) both work, and configuration
files are merged rather than replaced.

Three things differ from the usual shape: the key is `mcp`, the type is
`local`, and `command` is an **array**.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "codex-cua": {
      "type": "local",
      "command": ["C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe", "-m", "codex_cua_mcp.server"],
      "enabled": true,
      "environment": {}
    }
  }
}
```

Check it with `opencode mcp list`; `opencode mcp debug codex-cua` shows more.

#### Cursor

File: `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` in the project root.
Both are merged, and the project file wins on a name collision. A local
server needs `"type": "stdio"` and `command`; `args`, `env` and `envFile`
(stdio only) are optional.

```json
{
  "mcpServers": {
    "codex-cua": {
      "type": "stdio",
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"],
      "env": {}
    }
  }
}
```

#### Windsurf

File: `%AppData%\devin\mcp_config.json` on Windows. That is the file
Cascade's "Open MCP config file" action opens, and Cascade has no
project-scope file. The editor separately discovers
`~/.codeium/windsurf/mcp_config.json`; editing that one does not change what
Cascade reads. The Devin Local agent uses `.devin/mcp_config.json` and
`.devin/mcp_config.local.json` in the project directory instead.

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

Cascade allows at most 100 tools in total across all servers. This server
exposes 16.

#### Zed

File: Zed's `settings.json` — Command Palette → "zed: open settings file".
The key is `context_servers`. Do not add a `"source": "custom"` field; Zed's
own example does not have one.

```json
{
  "context_servers": {
    "codex-cua": {
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"],
      "env": {}
    }
  }
}
```

#### Gemini CLI

File: `~/.gemini/settings.json` (personal) or `.gemini/settings.json`
(repository). The `gemini mcp add` command defaults to **project** scope; pass
`-s user` for the personal file.

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

The CLI form:

```powershell
gemini mcp add -s user codex-cua C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server
```

#### Continue (VS Code and JetBrains)

File: `%USERPROFILE%\.continue\config.yaml` — one file for both extensions.
Standalone per-server files go in `.continue/mcpServers/*.yaml` or
`~/.continue/mcpServers/*.yaml`; a JSON file with the Claude Desktop / Cursor
shape dropped in that folder is auto-detected.

**`mcpServers` here is a YAML list, not a map.** This is the single most
common way to get Continue wrong.

```yaml
mcpServers:
    - name: codex-cua
      type: stdio
      command: 'C:\path\to\windows-cua\.venv\Scripts\python.exe'
      args:
        - -m
        - codex_cua_mcp.server
      env: {}
```

MCP tools only appear in **Agent mode**. The remote transport is spelled
`streamable-http` (hyphenated).

#### Cline

File: `~/.cline/data/settings/cline_mcp_settings.json` for the CLI; the VS
Code extension has its own MCP settings JSON, edited from the UI (MCP Servers
icon → Configure → Configure MCP Servers).

```json
{
  "mcpServers": {
    "codex-cua": {
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"],
      "env": {},
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

#### Roo Code

File: `mcp_settings.json` in the Roo Code extension settings directory (open
with "Edit Global MCP"), and `.roo/mcp.json` in the project root ("Edit
Project MCP"). If the same server name appears in both, the project-level
definition wins.

```json
{
  "mcpServers": {
    "codex-cua": {
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"],
      "env": {},
      "alwaysAllow": [],
      "disabled": false
    }
  }
}
```

The same page also documents `cwd`, `timeout` (1–3600 seconds, default 60),
`watchPaths` and `disabledTools`.

Note: a `roo mcp add` CLI is **not** documented on Roo Code's MCP page and is
not in this repo's README. Use the JSON file.

#### Goose

File: `%APPDATA%\Block\goose\config\config.yaml` on Windows. The key is
`extensions`, the type is `stdio`, and the executable goes in `cmd` (not
`command`).

```yaml
extensions:
    codex-cua:
      type: stdio
      name: codex-cua
      enabled: true
      cmd: 'C:\path\to\windows-cua\.venv\Scripts\python.exe'
      args: ["-m", "codex_cua_mcp.server"]
      env_keys: []
      envs: {}
      timeout: 300
```

CLI: `goose configure` → Add Extension → Command-Line Extension, or
`goose session --with-extension "..."`.

#### OpenHands

File: `~/.openhands/mcp.json`, written by `openhands mcp add`. In the Agent
Canvas it is Customize → MCP Servers → Add custom server.

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

Current OpenHands releases do **not** read MCP servers from a `config.toml`
`[mcp]` section. That format belongs to legacy OpenHands (V0).

#### VS Code (Copilot agent mode)

There are two different schemas, and VS Code reads both. The VS Code schema
uses the top-level key `servers`; the portable Copilot schema uses
`mcpServers`.

VS Code schema — `.vscode/mcp.json` (workspace) and a profile-level
`mcp.json` (user). For stdio servers `type` and `command` are mandatory;
optional keys are `args`, `cwd`, `env`, `envFile`, `dev`, `sandboxEnabled` and
`sandbox`.

```json
{
  "servers": {
    "codex-cua": {
      "type": "stdio",
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"],
      "env": {}
    }
  }
}
```

Portable Copilot schema — `.mcp.json` at the workspace root, or
`$COPILOT_HOME/mcp-config.json` falling back to `~/.copilot/mcp-config.json`.
Copilot harness sessions consume the portable format directly.

```json
{
  "mcpServers": {
    "codex-cua": {
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"]
    }
  }
}
```

The CLI form adds to the VS Code user-profile configuration, not to the
portable file:

```powershell
code --add-mcp '{"name":"codex-cua","type":"stdio","command":"C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe","args":["-m","codex_cua_mcp.server"]}'
```

Palette commands: "MCP: Add Server", "MCP: Open Workspace Folder MCP
Configuration", "MCP: Open User Configuration", "MCP: List Servers".

#### LM Studio

File: `mcp.json`, opened with the **Program** tab in the right sidebar →
**Install → Edit mcp.json**. LM Studio's documentation states that it
supports both local and remote MCP servers and follows Cursor's `mcp.json`
notation, so the local shape is the Cursor shape. Its own page only shows a
remote example:

```json
{
  "mcpServers": {
    "codex-cua": {
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"]
    }
  }
}
```

#### AnythingLLM

File: `<ANYTHINGLLM_STORAGE_DIR>\plugins\anythingllm_mcp_servers.json`,
created on first use of the "Agent Skills" page. AnythingLLM supports stdio.

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

AnythingLLM also has a per-server `autoStart` option, nested under that
server's own `"anythingllm"` object.

#### LibreChat

File: `librechat.yaml`. File-based additions need a LibreChat restart;
entries added through the interface do not. The UI path is right sidebar →
"MCP Settings" → "+" → Create.

```yaml
mcpServers:
  codex-cua:
    command: 'C:\path\to\windows-cua\.venv\Scripts\python.exe'
    args:
      - -m
      - codex_cua_mcp.server
    env: {}
```

Remote servers use `type: streamable-http` plus a `url`.

#### Kiro

File: `~/.kiro/settings/mcp.json` (user) or `.kiro/settings/mcp.json`
(workspace). When the same server name occurs in both, the project-specific
definition takes precedence.

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

#### Amazon Q Developer CLI

File: `~/.aws/amazonq/mcp.json` (global), `.amazonq/mcp.json` (workspace), or
`~/.aws/amazonq/cli-agents/*.json`, which has an `mcpServers` field. The
supported per-server fields are `command`, `args`, `env` and `timeout`;
`timeout` is in milliseconds and defaults to 120000.

```json
{
  "mcpServers": {
    "codex-cua": {
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"],
      "env": {},
      "timeout": 120000
    }
  }
}
```

#### JetBrains Junie

File: `~/.junie/mcp/mcp.json` (user) or `.junie/mcp/mcp.json` (project root).

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

Note: JetBrains AI Assistant is configured only through a settings dialog and
stores no committable file. It does not share Junie's config.

#### Warp

File: `~/.warp/.mcp.json` (global) or `.warp/.mcp.json` at the repository
root. `command` and `args` are both required; `env` and `working_directory`
are optional.

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

Warp also accepts `mcp_servers`, `servers` and a nested `mcp.servers` as
alternative wrapper keys, but `mcpServers` is the documented one and the
one to use.

#### Crush (Charm)

File: `crush.json` or `.crush.json` (project), `~/.config/crush/crush.json`
(user). The top-level key is `mcp`, not `mcpServers`.

```json
{
  "$schema": "https://charm.land/crush.json",
  "mcp": {
    "codex-cua": {
      "type": "stdio",
      "command": "C:\\path\\to\\windows-cua\\.venv\\Scripts\\python.exe",
      "args": ["-m", "codex_cua_mcp.server"],
      "env": {}
    }
  }
}
```

Optional per server: `disabled`, `disabled_tools`, `enabled_tools`, `timeout`
(seconds), `oauth`, `oauth_client_id`, `oauth_client_secret`,
`oauth_callback_port`.

#### Qwen Code

File: `~/.qwen/settings.json` (user) or `.qwen/settings.json` (project).

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

Optional: `cwd`, `timeout` (milliseconds, default 600000), `trust`,
`includeTools`, `excludeTools`, `headers`.

CLI: `qwen mcp add [options] <name> <commandOrUrl> [args...]`, with
`-s/--scope user|project`, `-t/--transport stdio|sse|http` and `-e/--env`.

#### MCP Inspector (test the server before wiring a harness)

This one is not a client — it is the reference test client, and it is the
fastest way to prove the server works on a new machine before you start
debugging a harness.

```powershell
npx @modelcontextprotocol/inspector "C:\path\to\windows-cua\.venv\Scripts\python.exe" -m codex_cua_mcp.server
```

It needs Node 22.19.0 or newer. The Inspector's own catalog file is
`~/.mcp-inspector/mcp.json` and uses the standard `mcpServers` shape.

### Harnesses that will not work as-is

**Open WebUI — stdio is not supported.** Its documentation is explicit:
"Native MCP support in Open WebUI is Streamable HTTP only." There is no config
file either; servers are added by an administrator under Settings → Admin →
Integrations → External Tool Servers → "+" → Add Connection, connection type
**MCP (Streamable HTTP)**. You need a stdio→HTTP bridge in front of this
server. AnythingLLM, by contrast, does support stdio.

**Aider — no MCP client at all.** Aider's own options reference and YAML
config reference contain no MCP support, and its MCP pull requests are not
merged. There is no config to write. If Aider ever gains support, the command
is the standard one:

```
C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server
```

### Sharing the setup with other people

Three pieces, and they are separable.

**The repository.** `https://github.com/osamabinabsar/windows-cua`. Clone it
and run `powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
-Harness claude` (or `codex`, `opencode`, `all`) from the clone.

**The config.** Anyone can write the JSON or YAML themselves. The snippets
above are the whole thing: one command, one argument list. Nothing in the
server is machine-specific except the absolute path to the virtualenv.

**The prompt block.** The block in the next section is the part people get
wrong. It is the difference between an agent that drives Windows well and one
that clicks blindly. Copy it into your instruction file, or just paste it into
the first chat.

If you are handing this to a friend, the shortest useful message is:

> Clone `https://github.com/osamabinabsar/windows-cua`, run
> `powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Harness claude`
> from the clone, then paste the prompt block below into your `CLAUDE.md`. On
> your machine the server command is
> `C:\Users\<you>\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server`.

---

## Telling your agent to use it

The server already ships its own `instructions` string (the `INSTRUCTIONS`
constant for the codex backend, `CUA_INSTRUCTIONS` for the cua backend, both
in `src/codex_cua_mcp/server.py`). Claude Code and other MCP clients surface
that to the model automatically. The block below is the reinforcement you paste
into a project instruction file so the rules hold in clients that do not
surface server instructions, or when you want the behaviour stated
explicitly.

### Where the prompt goes

| Harness | Instruction file |
|---|---|
| Claude Code | `CLAUDE.md` (project `./CLAUDE.md` or `./.claude/CLAUDE.md`; user `~/.claude/CLAUDE.md`; rules `./.claude/rules/*.md`) |
| Codex CLI | `AGENTS.md` (global `~/.codex/AGENTS.md`; `AGENTS.override.md` takes precedence per directory) |
| OpenCode | `AGENTS.md` or `CLAUDE.md` |
| Cursor | `.cursor/rules/*.mdc` (Always Apply, Apply Intelligently, Apply to Specific Files, Apply Manually); root `CLAUDE.md` is always applied |
| GitHub Copilot / VS Code | `.github/copilot-instructions.md` |
| Gemini CLI | `GEMINI.md` (global `~/.gemini/GEMINI.md`) |
| Cline | `.clinerules/` or `AGENTS.md` |
| Roo Code | `.roo/rules/`, `.roorules`, or `AGENTS.md` |
| Crush | `AGENTS.md` or `CRUSH.md` |
| Goose | `.goosehints` or `AGENTS.md` |
| Warp | `AGENTS.md` or `WARP.md` |
| Windsurf | `.windsurf/rules/*.md`, `.windsurfrules`, or `AGENTS.md` |
| Zed | `AGENTS.md` |
| Qwen Code | `QWEN.md` |

### The block

Copy everything between the fences.

```markdown
## Windows computer use (MCP server `codex-cua`)

You have 16 tools that drive real Windows apps. In most clients they appear as
`mcp__codex-cua__<tool_name>`; in some they appear as plain `<tool_name>`.
Use whichever form your client shows you.

**The collaboration model.** Every action names a target window and is delivered to that
window WITHOUT raising it. The user keeps their mouse, their keyboard and their focus
while you work. `activate_window` is the ONLY tool that takes focus. Do not call it
unless the task genuinely cannot be done any other way, and tell the user you are
about to before you do.

**The loop — observe, act once, re-observe.**

    list_apps  ->  get_window_state  ->  ONE action  ->  get_window_state  ->  repeat

**Rules**

1. Never guess a window. Start with `list_apps` (installed apps and the window ids they
   own) or `list_windows` (every open window). Use `get_window(window_id, app)` to
   rehydrate a handle that went stale.
2. Read before you act. `get_window_state(app, window_id)` returns the accessibility
   tree with element indexes; `read_accessibility_tree(app, window_id)` is the cheaper
   tree-only read. Pass `include_screenshot=True` only when you genuinely need
   coordinates.
3. **Element indexes are only valid for the observation that produced them.** After any
   action that changes the window, re-read the state and take fresh indexes. Never reuse
   an index from an earlier read.
4. Prefer `click_element(element_index=...)` over `click(x, y)`. `click` needs
   window-relative coordinates and is the fallback for canvas or screenshot-only
   targets. Never move the real mouse cursor — you do not need to.
5. **Prefer `set_value(element_index=..., value=...)` for filling a field.** It needs no
   focus and is the reliable path. `type_text` without an `element_index` sends
   keystrokes to whatever happens to hold focus in the target window; if nothing holds
   focus the characters are lost and the call still reports success. So: always pass
   `element_index` to `type_text`, and use `set_value` when you can. An empty `value`
   clears the field.
6. Use `press_key` for Enter, Tab, arrows and shortcuts, with X keysym names: `Return`,
   `Tab`, `Escape`, `Control_L+a`, `Control_L+Shift_L+period`. Pass `element_index` to
   land the chord on a specific control.
7. One action per step, then verify with a fresh read. Do not chain a click + type +
   click against indexes taken before the first click.
8. `perform_secondary_action(element_index=..., action=...)` for `Expand`, `Collapse`,
   `Toggle`, `Raise`, `Scroll Up`. On the `cua` backend a `>` path such as
   `File>Save As` walks the application's menu, and a single word is honoured by
   clicking the element.
9. `launch_app(app)` starts an app by the id from `list_apps` or by an explicit `.exe`
   path. It does not return a window id — call `list_apps` or `list_windows` again
   after launching.
10. Approval is per app, once per session. The first action against an app raises a
    permission prompt for the user. If a call fails with an approval or permission
    error, STOP and tell the user which app needs approving. Do not retry in a loop
    and do not try to work around the prompt.
11. The user can take the computer back at any moment with `Ctrl+Alt+Shift+Backspace`.
    If a call says the user took control back, stop immediately, do not retry, and tell
    them what you were doing. Wait for their next instruction.
12. `status` reports the helper binary, the codex CLI, which apps are approved and
    whether computer use is paused. Use it to diagnose a problem, not as a step in a
    task.

**Do not**
- Do not call `activate_window` to make something easier.
- Do not act on element indexes from an older observation.
- Do not type into a field you have not read; read the tree, find the index, then act.
- Do not drive the machine when the user only asked for information — reading is enough.
- Do not run the same action twice after an ambiguous result; re-read the state first.
```

---

## A working session, step by step

Sixteen tools, identical names on both backends:

`list_apps` · `list_windows` · `get_window` · `get_window_state` ·
`read_accessibility_tree` · `launch_app` · `click` · `click_element` ·
`type_text` · `press_key` · `scroll` · `drag` · `set_value` ·
`perform_secondary_action` · `activate_window` · `status`

The example below is: *"Type `hello from the agent` into Notepad and save it
as `notes.txt` in Documents."* Element indexes are illustrative — your agent
reads the real ones out of the tree it observes.

**1. Find the app.**

```
list_apps
```

returns a line per app, with the window ids it owns:

```
- Notepad  [id=notepad]  (running)
    window id=41207 title='Untitled - Notepad'
```

`list_apps` and `list_windows` are read-only and never raise an approval
prompt.

**2. Read the window before touching it.**

```
get_window_state(app="notepad", window_id=41207)
```

returns the accessibility tree, with an index on every element:

```
[0] Window 'Untitled - Notepad'
  [1]   Edit  'Text editor'
  [2]   MenuBar
  [3]     MenuItem  'File'
```

`read_accessibility_tree(app="notepad", window_id=41207)` returns the same
tree without the surrounding state summary, and is cheaper. Add
`include_screenshot=True` only when you actually need coordinates.

**3. One action.**

```
click_element(app="notepad", window_id=41207, element_index=1)
```

returns `clicked element 1 in window 41207`.

The window was never raised. The user's mouse did not move.

**4. Re-read, because the click may have changed the tree.**

```
get_window_state(app="notepad", window_id=41207)
```

Take the new index from this read. The old index 1 is now void.

**5. Fill the field.**

```
set_value(app="notepad", window_id=41207, element_index=1, value="hello from the agent")
```

returns `set element 1 in window 41207`.

`set_value` needs no focus and needs no coordinates, which is why it is the
reliable path. `type_text(app="notepad", window_id=41207, text="hello from the agent", element_index=1)`
also works and is correct — but `type_text` without an `element_index` sends
the characters to whatever holds focus, and if nothing does they are lost
while the call still reports success.

**6. Re-read to verify the text landed.**

```
get_window_state(app="notepad", window_id=41207)
```

Check that the field now reads `hello from the agent`. If it does not, do not
type again — read the tree again and find the field's real index. Repeating a
blind action is how an agent ends up with the text typed twice.

**7. Open the Save As dialog.**

```
press_key(app="notepad", window_id=41207, key="Control_L+s")
```

The dialog is a *new* window. Find it:

```
list_windows
```

```
- id=41207  app=notepad  title='Untitled - Notepad'
- id=41855  app=notepad  title='Save As'
```

**8. Read the dialog, not the old window.**

```
get_window_state(app="notepad", window_id=41855)
```

```
[0] Window 'Save As'
  [1]   Edit  'File name:'
  [2]   Button 'Save'
  [3]   ComboBox 'Save as type:'
```

**9. Fill the filename.**

```
set_value(app="notepad", window_id=41855, element_index=1, value="C:\\Users\\you\\Documents\\notes.txt")
```

**10. Confirm, then verify.**

```
press_key(app="notepad", window_id=41855, key="Return")
```

```
list_windows
```

The title should now read `notes.txt - Notepad`. That is the verification
step. The point of the loop is that every action is followed by a read, so
the agent knows what actually happened instead of assuming it did.

### What approval looks like

The first action against an app raises a permission prompt through MCP
elicitation. On the `codex` backend the request reads roughly:

> Allow Computer Use to control 'Notepad'?
> App id: notepad
>
> The agent will be able to read this window and send input to it without
> bringing it to the foreground.

You approve or deny. Approvals persist for the session, so you are asked once
per app. `list_apps`, `list_windows`, `get_window` and `status` never prompt.

If your harness does not support elicitation, the action fails with an error
naming the app rather than proceeding unapproved. `--auto-approve` skips the
prompt entirely; it is not recommended.

### Taking the computer back

The user can interrupt at any moment with **Ctrl+Alt+Shift+Backspace**.

That abandons the action in flight, dismisses the on-screen banner, and
revokes every approval the agent had. The server stays alive. Press it again
to resume. While it is held, every computer-use call fails with a message
saying so — if the agent sees that message it stops, does not retry, and
tells you what it was doing.

The banner text itself is yours to set. The helper reads it from
`%USERPROFILE%\.codex\computer-use\config.json`:

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

`scripts\set-overlay-text.py` writes those two strings and leaves everything
else in the file alone. From the repository root, with the virtualenv's
interpreter:

```powershell
C:\path\to\windows-cua\.venv\Scripts\python.exe scripts\set-overlay-text.py --using "AI is controlling this computer" --hint "Press Ctrl+Alt+Shift+Backspace to take over"
```

The change takes effect the next time the helper starts an overlay, so
restart the MCP server or the harness session to see it.

Note that `escToCancel` is only a label. The key the helper actually watches
is the physical Escape; this project adds Ctrl+Alt+Shift+Backspace on top of
it, through `src/codex_cua_mcp/takeover.py`. Point the label at the key that
really works, or it is a promise the overlay cannot keep.

---

## What the agent must not do

**`activate_window` is the only focus-taking tool.** Every other action is
routed to the target window without raising it, so the user keeps their
foreground window, their focus and their mouse. `activate_window` breaks that
deliberately, and it is the one thing that makes this server different from
Windows-MCP. Do not call it to make something easier. If a task genuinely
cannot be done any other way, say so and ask first.

On the `cua` backend there is one other route to the foreground: a window
that refuses background input makes the driver ask for
`delivery_mode: "foreground"`, which it performs as a brief focus swap that
restores your previous foreground window afterwards. The server never fronts
a window on a guess.

**Element indexes expire.** An index is only valid for the observation that
produced it. After any action that changes the window, the indexes are stale
and using them targets the wrong control — or nothing. Re-read the state and
take fresh indexes. Never chain a click, a type and a second click against
indexes taken before the first click.

**`set_value` beats `type_text`.** `set_value(element_index=..., value=...)`
replaces an editable element's value through UI Automation. It needs no focus
and no coordinates, so it cannot be misdelivered. `type_text` delivers
keystrokes to whatever holds focus in the target window; if nothing holds
focus, the characters are lost and the call still reports success. So:
prefer `set_value`; when you must use `type_text`, always pass
`element_index`. An empty `value` clears a field.

**Never loop blindly.** If a call returns an ambiguous result, re-read the
state. Do not run the same action twice on the assumption that the first one
worked. And if a call fails with an approval or permission error, stop and
tell the user which app needs approving — do not retry, and do not try to
work around the prompt.

**Do not drive the machine when the user only asked for information.**
`list_apps`, `list_windows`, `get_window` and `status` are enough for most
questions, and they never prompt for approval. `get_window_state` and
`read_accessibility_tree` read the target window, so the first call against
an app still raises the approval prompt.

**Prefer `click_element` over `click`.** `click` needs window-relative
coordinates, which means you need a screenshot, which means you are working
off a picture of a window that may already have changed. `click_element`
actuates the control directly.

**Do not move the real mouse cursor.** You do not need to, and the user is
using it.

**On the `cua` backend**, `perform_secondary_action` takes two forms: a
single word (`Invoke`, `Expand`, `Collapse`, `Toggle`, `Check`, `Uncheck`) is
honoured by clicking the element, and a menu path (`File>Save As`) walks the
application's menu. The Codex helper has a real accessibility-action tool and
does not support the `>` form. Anything the driver cannot express returns an
error rather than silently doing nothing.

---

## Troubleshooting a harness that will not start the server

Work down this list. Each step rules out one layer.

**1. Does the server start at all, outside the harness?**

```powershell
C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server --check
```

If this fails, nothing about the harness config matters. It prints the reason
to stderr and exits 2 when it cannot find `codex-computer-use.exe` — that
means the helper is not on this machine. It ships with the Codex **app**;
the Codex CLI alone does not include it. Install the app, or point
`CODEX_COMPUTER_USE_BIN` at the binary, or switch to the MIT backend with
`--backend cua` and `CUA_DRIVER_BIN`.

**2. Can the server start with the exact command from the config?**

Copy the `command` and `args` out of the harness config and run them in
PowerShell yourself. If they fail there, the harness is innocent.

**3. Is the path right, and absolute?**

Copy the path out of the config and paste it into Explorer, or run:

```powershell
Test-Path 'C:\path\to\windows-cua\.venv\Scripts\python.exe'
```

A relative path is the usual culprit: the child's working directory is not
guaranteed to be the repo, so `.venv\Scripts\python.exe` can resolve
somewhere else entirely. Use the full absolute path.

Also check the backslashes. In JSON they must be doubled (`C:\\path\\to\\...`).
A single backslash is a JSON escape and will corrupt the path.

**4. Did the JSON parse?**

Many harnesses fail silently on a malformed config file. Check it:

```powershell
Get-Content 'C:\Users\you\.claude.json' -Raw | ConvertFrom-Json | Out-Null
```

A trailing comma is the usual cause.

**5. Is the top-level key right for this harness?**

This is the single most common failure. The key is not always `mcpServers`:

- OpenCode: `mcp`
- Crush: `mcp`
- Zed: `context_servers`
- VS Code native schema: `servers`
- VS Code portable / Copilot schema: `mcpServers`
- Codex CLI: TOML, `[mcp_servers.codex-cua]`
- Goose: `extensions`
- Continue: `mcpServers`, but as a YAML **list**

A harness reading `mcpServers` will silently ignore a server parked under
`mcp`, and vice versa.

**6. Is the shape right?**

- OpenCode's `command` is an **array**; everyone else's is a string.
- Continue's `mcpServers` is a YAML list, not a map.
- VS Code's native schema requires `"type": "stdio"`.
- Cursor's schema requires `"type": "stdio"`.
- Codex CLI is TOML, not JSON.

**7. Did you restart the harness?**

Claude Desktop must be fully quit and restarted. LibreChat needs a restart
after a file-based edit. Most CLI harnesses pick the config up on the next
run.

**8. Is the server timing out during startup?**

The Codex CLI's `startup_timeout_sec` defaults to 10 seconds. The helper has
to cold-start, and a cold Windows disk can eat that. Set it higher:

```toml
[mcp_servers.codex-cua]
command = 'C:\path\to\windows-cua\.venv\Scripts\python.exe'
args = ['-m', 'codex_cua_mcp.server']
startup_timeout_sec = 30
```

**9. Is the harness talking to a different Python?**

If `command` is `python` or `uv` rather than an absolute path, the harness
may resolve a different interpreter — one without `mcp` installed. Name the
interpreter explicitly.

**10. Test the server with the reference client.**

Bypass the harness entirely:

```powershell
npx @modelcontextprotocol/inspector "C:\path\to\windows-cua\.venv\Scripts\python.exe" -m codex_cua_mcp.server
```

If the Inspector shows all 16 tools, the server is fine and the problem is
the harness config. If it does not, the problem is the server or the
machine.

**11. Ask the server what it thinks.**

Once it is connected, the `status` tool reports the helper binary, the codex
CLI, whether the helper is alive, which apps are approved and whether
computer use is paused:

```
helper      : C:\Users\you\AppData\Local\OpenAI\Codex\runtimes\...\codex-computer-use.exe
codex cli   : C:\Users\you\AppData\Local\OpenAI\Codex\bin\...\codex.exe
alive       : True
approved    : notepad
auto_approve: False
take-over   : Ctrl+Alt+Shift+Backspace (hotkey)
```

`alive: False` means the helper is not running. `(not found)` next to
`codex cli` means the approval handshake will not work.

**12. Check the harness's own logs.**

- Claude Desktop: `%APPDATA%\Claude\logs\mcp.log`, and
  `mcp-server-<name>.log` for the per-server stderr.
- Claude Code: `claude mcp list` shows the configured servers; `claude` with
  `--debug` shows the spawn.
- OpenCode: `opencode mcp list` and `opencode mcp debug codex-cua`.
- Codex CLI: the server's stderr is surfaced in the session output.
- Anything stdio-based: the server writes its diagnostics to stderr, and
  stdio servers may use stderr for all their logging.

**13. The tools connect but every action fails with an approval error.**

The harness is not supporting MCP elicitation, so the per-app permission
prompt cannot be shown. Either use a harness with elicitation support, or run
the server with `--auto-approve` and accept that you are approving every app
in advance.

**14. The agent takes over the screen and you cannot stop it.**

Press **Ctrl+Alt+Shift+Backspace**. That is the whole point of the chord. If
it does not work, the server was started with `--no-takeover`, or the chord
is registered to another program — the watcher falls back to polling in that
case, which still works but is less responsive. Check `status`: it reports
the chord and its mode, and `-- PAUSED` next to it means the agent is already
stopped.

---

## Related documents

- [README.md](../README.md) — the tool list, the two backends, and the
  installer.
- [architecture.md](architecture.md) — how the collaboration model actually
  works, and the three helper workarounds.
- [deploying-to-other-pcs.md](deploying-to-other-pcs.md) — the long version
  of installing on a second machine.
- [troubleshooting/invisible-cursor.md](troubleshooting/invisible-cursor.md) —
  the Windows 11 display-driver fault that hides the mouse pointer over
  parts of the screen. It is not caused by this server.
- [alternatives.md](alternatives.md) — how this compares to other Windows
  computer-use tools.

# Installing on another PC

This is the procedure for putting `codex-cua-mcp` on a second or third
machine — your own other PC, a friend's PC, or somebody else's PC who
found the repository and wants to try it.

Two things do not travel with the repository, and both are handled below:

- the **driver binary** — the OpenAI helper or the `trycua/cua` driver.
  Each machine installs its own.
- the **harness registration** — the entry in your MCP client config
  points at an absolute path on the machine it was written on. Each
  machine registers its own.

Everything else — the Python package, the tool names, the arguments — is
the same on every machine, so a harness config written on one PC is valid
on another once you fix the path. The virtualenv is per-machine too: it
is created inside the checkout, wherever you clone the repository.

---

## Choose your backend first (codex vs cua)

The server has two interchangeable backends. They expose the same sixteen
tools with the same names and the same arguments, so a harness config does
not change when you switch. The installer picks one for you; you can
change your mind later with `--backend` (see
[Manual install](#manual-install-no-script)).

| | `codex` (the default) | `cua` |
|---|---|---|
| Driver | `codex-computer-use.exe`, from the Codex desktop app | `cua-driver.exe`, from [trycua/cua](https://github.com/trycua/cua) |
| Licence | Proprietary (OpenAI) | MIT |
| Extra software you must install | The Codex desktop app, plus the `codex` CLI | The cua driver only |
| OpenAI account needed | No | No |
| Approval model | Per-app, surfaced to you as an MCP elicitation you answer | The driver's own permission model |
| Best for | Machines that already run the Codex app | Anyone who wants no OpenAI binary at all |

**Use `cua` if you can.** It is MIT, it needs one script, and it has no
dependency on an OpenAI binary that only ships with a particular desktop
app. Use `codex` if you want the Codex helper's behaviour — in particular
its real accessibility actions (`Expand`, `Collapse`, `Toggle`, `Invoke`),
which the cua driver does not have.

---

## Prerequisites, per backend (with links)

### On every machine

| | |
|---|---|
| **Windows** | Windows 10 or 11. UI Automation is required; the code checks the platform, not the build, but it behaves differently on older releases. |
| **Git** | To clone the repository. There is no PyPI package yet. |
| **`uv`** | [astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/). The installer uses it to make the virtualenv and install the package. It also fetches Python for you, so you do not need Python installed separately. |

Install `uv` with either of these:

```powershell
winget install --id=astral-sh.uv
```

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Open a **new** PowerShell window afterwards, so the updated `PATH` is
picked up. Check with:

```powershell
uv --version
```

If `uv` is missing, the installer stops on the spot and says so — it does
not half-install.

### For the `codex` backend

| | |
|---|---|
| **The Codex desktop app** | This is where the helper comes from. See [chatgpt.com/codex](https://chatgpt.com/codex). |
| **The `codex` CLI** | Used for the approval handshake. [github.com/openai/codex](https://github.com/openai/codex) — `npm install -g @openai/codex`. |

The important trap: **the CLI on its own is not enough.**
`npm install -g @openai/codex` ships the CLI but *not*
`codex-computer-use.exe`. The helper arrives with the Codex desktop app,
which installs a runtime that the server searches automatically:

```
%LOCALAPPDATA%\OpenAI\Codex\runtimes\cua_node\*\bin\node_modules\@oai\cua\bin\windows\codex-computer-use.exe
%LOCALAPPDATA%\OpenAI\Codex\runtimes\cua_node\*\bin\node_modules\@oai\sky\bin\windows\codex-computer-use.exe
```

A current Codex install carries both package names, and the server looks
for both. If yours lives somewhere else entirely, set
`CODEX_COMPUTER_USE_BIN` to its full path.

### For the `cua` backend

| | |
|---|---|
| **The cua driver** | [trycua/cua](https://github.com/trycua/cua) — [driver quickstart](https://cua.ai/docs/cua-driver/quickstart). |

Install the driver:

```powershell
irm https://cua.ai/driver/install.ps1 | iex
```

Then start it, so the driver is running before the server needs it:

```powershell
cua-driver autostart kick
```

Check it:

```powershell
cua-driver status
```

Success looks like this:

```
Cua Driver daemon is running
  socket: \\.\pipe\cua-driver
```

Two things to know about that install:

- It registers a Windows Scheduled Task named `cua-driver-serve` that
  starts the driver at every interactive logon, in your interactive
  session. That task runs at the highest privilege level
  (`RunLevel=Highest`), so registering it makes the installer show **one
  UAC prompt**. To install without registering the task, pass
  `-NoAutoStart` — the `irm | iex` form takes no arguments, so use:

  ```powershell
  & ([scriptblock]::Create((irm https://cua.ai/driver/install.ps1))) -NoAutoStart
  ```

  With no task registered, the driver does not come back on its own after
  a logon. Register it when you want it with `cua-driver autostart
  enable`, then start it now with `cua-driver autostart kick` — `kick`
  fails if no task is registered. To run the daemon in the foreground
  instead, use `cua-driver serve`.
- The driver sends **anonymous telemetry by default**. See
  [what leaves the machine](#what-this-can-and-cannot-do-on-that-machine-permissions-approval-model-what-leaves-the-machine---nothing)
  and turn it off if you want.

---

## The install (one command, then what it does, then how to verify)

Clone the repository and run the installer:

```powershell
git clone https://github.com/osamabinabsar/windows-cua
cd windows-cua
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Harness claude
```

For the `cua` backend:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Harness claude -Backend cua
```

`powershell -ExecutionPolicy Bypass -File ...` is the form to use. Windows
ships with a `Restricted` execution policy that blocks local `.ps1` files,
and `-ExecutionPolicy Bypass` applies to that one command only. If your
organisation manages the policy, ask it to allow signed local scripts
instead of bypassing it.

The script's flags:

| Flag | Values | Default |
|---|---|---|
| `-Harness` | `claude`, `codex`, `opencode`, `all` | `claude` |
| `-Backend` | `codex`, `cua` | `codex` |
| `-RepoPath` | Path to the checkout | the repository root |
| `-User` | Local Windows account to install for | the current user |
| `-ConfigRoot` | Directory that holds the per-user config (`.codex`, `.config`) | `%USERPROFILE%` |
| `-Uninstall` | remove the registrations and stop | off |

`-User` and `-ConfigRoot` only change where the registrations are
written. You do not normally need them.

The two words overlap, so be careful: `-Harness codex` registers the
server with the **Codex CLI** harness, and `-Backend codex` selects the
**OpenAI helper** as the driver. They are unrelated choices. Use
`-Harness all` to register with all three harnesses at once.

### What it does, in order

1. **Checks prerequisites.** Looks for `uv` (and stops with exit code 1 if
   it is missing), then for a Python 3.11+ that `uv` can use. If there is
   none, `uv` downloads one on the next step.
2. **Locates the driver.** For `-Backend cua` it looks for
   `cua-driver.exe` under `%USERPROFILE%\.cua-driver\packages\current\`,
   then under `releases\`, then on `PATH`. For `-Backend codex` it
   searches `%LOCALAPPDATA%\OpenAI\Codex\runtimes\cua_node` recursively
   for `codex-computer-use.exe` and takes the newest, and separately
   looks for the `codex` CLI. A missing binary is a **warning**, not a
   stop — you can fix it and re-run.
3. **Installs the package.** Creates `.venv` with `uv venv --python 3.14`
   (reusing it if it is already there) and runs
   `uv pip install --python .venv\Scripts\python.exe -e <repo>`. The
   install is *editable*, so a later `git pull` updates the running code
   with no reinstall.
4. **Registers with the harness.** `-Harness claude` runs
   `claude mcp remove codex-cua --scope user` and then
   `claude mcp add --scope user codex-cua -- ...`, so re-running replaces
   the entry instead of failing on a duplicate. `-Harness codex` writes a
   `[mcp_servers.codex-cua]` table into `~\.codex\config.toml`, creating
   the file if it does not exist. `-Harness opencode` merges an entry
   into `~\.config\opencode\opencode.json`, and refuses to touch that
   file if it is not valid JSON.
5. **Verifies.** Runs the registered command with `--check` and prints the
   result.

Then it prints a summary. The exit code is `0` on success and `1` if
anything failed. If it printed `Installation INCOMPLETE.`, read the lines
above it: each one names a thing that did not work. The usual cause is
the post-install check, which means the server is registered but cannot
start until you fix the missing binary. It can also be a registration
that failed — for example the `claude` CLI was not on the machine.

### How to verify it right now

From the repository root:

```powershell
.\.venv\Scripts\codex-cua-mcp.exe --check
```

A clean report prints the backend and the path of each binary it found.
The full expected output, for both backends, is in
[Verifying the install](#verifying-the-install-codex-cua-mcp---check-and-the-cua-equivalent).

Then start your harness and ask it a read-only question:

```
List the open apps on my PC
```

---

## Manual install (no script)

If you would rather not run the installer — a locked-down machine, a
policy that forbids `ExecutionPolicy Bypass`, or you want to see every
step — do it by hand. It is four commands: the first two get you into
the checkout, the last two build it.

```powershell
git clone https://github.com/osamabinabsar/windows-cua
cd windows-cua
uv venv --python 3.14
uv pip install --python .venv\Scripts\python.exe -e .
```

`uv pip install -e .` also works if the virtualenv is active. The
`--python` form is more robust, because it does not depend on which
virtualenv happens to be active.

To run the server without running the installer at all, from the
repository root:

```powershell
uv run --with "mcp>=2" python -m codex_cua_mcp.server
```

`uv` finds the `pyproject.toml` in the current directory, creates a
`.venv` there if there is not one already, installs the package into it
and runs it. Nothing is registered with any harness. Add `--check` if you
want to see which driver it found instead of starting the server.

To run it from a checkout without creating a virtualenv by hand —
`--project` points `uv` at the checkout, and it manages that project's
`.venv` for you:

```powershell
uv run --project C:\path\to\windows-cua python -m codex_cua_mcp.server
```

That is the same fallback the installer registers when it cannot create a
`.venv` (it adds `--backend codex` or `--backend cua` to the same
command).

The server command, whichever way you installed it, is:

```
C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server
```

Add `--backend cua` for the MIT driver. Add `--backend codex` if you want
the OpenAI helper and the default ever stops being what you expect.
Without either flag the backend comes from the `WINDOWS_CUA_BACKEND`
environment variable, and defaults to `codex`.

The other flags, all of which you can put in the registered command:

| Flag | What it does |
|---|---|
| `--backend codex` / `--backend cua` | Which driver to use. |
| `--helper PATH` | Point at `codex-computer-use.exe` yourself. Also `CODEX_COMPUTER_USE_BIN`. |
| `--codex-cli PATH` | Point at the `codex` CLI. Also `CODEX_CLI_PATH`. |
| `--driver PATH` | Point at `cua-driver.exe` yourself. Also `CUA_DRIVER_BIN`. |
| `--auto-approve` | Approve app requests without asking. Not recommended. |
| `--no-takeover` | Do not arm the take-over chord. |
| `--takeover-key "Ctrl+Alt+Shift+Pause"` | Use a different chord. |
| `--check` | Report which binaries were found, then exit. |

---

## Registering with a harness (pointer to the other-harnesses doc)

The installer registers the server for you — but only with Claude Code,
the Codex CLI and OpenCode. If you registered by hand, or you want a
harness the installer does not know about, read
**[Using it in other harnesses](using-other-harnesses.md)**. It has the
per-client config snippets (Claude Code, Claude Desktop, Codex CLI,
OpenCode, Cursor, Windsurf, Zed, Gemini CLI, VS Code, Cline, Roo Code,
Goose, and a dozen more, plus a generic stdio block for everything
else), the flag reference, the harnesses that will not work as-is, and
the prompt block you can paste so an agent knows the server exists.

The short version: this server speaks MCP over stdio, so any client that
can spawn a child process and talk MCP to it can use it. Point it at:

```
C:\path\to\windows-cua\.venv\Scripts\python.exe -m codex_cua_mcp.server
```

One thing to check on every new machine: **the registered command is an
absolute path.** If you move or rename the repository folder, the
registration breaks silently — the harness starts a process that no
longer exists. Re-run the installer after a move; it rewrites the entry.

---

## Verifying the install (codex-cua-mcp --check and the cua equivalent)

`--check` is a **discovery** check. It resolves the paths and prints
them. It does not start the driver, does not move the pointer, and does
not prove the whole chain works. Read a clean report as "the pieces are
present", not "the server works".

Run the commands below from the repository root. The `codex` backend:

```powershell
.\.venv\Scripts\codex-cua-mcp.exe --check
```

```
backend   : codex
helper    : C:\Users\you\AppData\Local\OpenAI\Codex\runtimes\cua_node\...\codex-computer-use.exe
codex cli : C:\Users\you\AppData\Local\OpenAI\Codex\bin\...\codex.exe
```

The `cua` equivalent:

```powershell
.\.venv\Scripts\codex-cua-mcp.exe --backend cua --check
```

```
backend   : cua
driver    : C:\Users\you\.cua-driver\packages\current\cua-driver.exe
```

`codex-cua-mcp` is only on your `PATH` while the virtualenv is activated.
The full path works from anywhere, which is why the examples above use it.

**When a binary is missing**, the check writes to stderr and exits `2`:

```
error: Could not find codex-computer-use.exe.
Searched: Codex runtime installs and standalone @oai/sky / @oai/cua packages.
...
```

The installer treats a non-zero exit from this check as a failed
install, and says `Installation INCOMPLETE.` rather than reporting
success with a server that can never find its driver.

On the `cua` backend, check the driver itself too:

```powershell
cua-driver status
cua-driver doctor
```

`cua-driver status` reports whether the daemon is running. `cua-driver
doctor` probes the machine (session type, UI Automation, telemetry) and is
read-only.

To check that the take-over chord arms on the new machine, from the
repository root:

```powershell
.\.venv\Scripts\python.exe scripts\probe_takeover.py
```

It reads no screen content, moves no pointer and presses no key. See
[Taking the computer back](takeover-hotkey.md).

---

## What this can and cannot do on that machine (permissions, approval model, what leaves the machine - nothing)

### What it can do

- Read the accessibility tree and take a screenshot of a **named window**,
  and send clicks, keys, scrolls and drags to **that window**.
- Launch an app by id or by `.exe` path.
- Work while you keep using the machine. Every action except
  `activate_window` leaves your foreground window, your focus and your
  mouse pointer where they were. The one exception is the `cua` backend:
  a window that refuses background input makes the driver ask for
  `delivery_mode: "foreground"`, which it performs as a brief focus swap
  that restores your previous window afterwards.

### What it cannot do

- It cannot see the whole desktop. It only works with windows it can
  bind to, and it names them explicitly.
- It does not take the mouse away from you. It does not move your real
  pointer.
- On the `codex` backend, `type_text` and `press_key` go to whatever
  holds focus **in the target window**. If nothing there holds focus, the
  characters are lost and the call still reports success. Always pass
  `element_index`, or use `set_value`.

### The approval model

On the `codex` backend, every app is gated behind per-app consent. The
server surfaces that as an MCP **elicitation**: your harness shows you a
prompt, you answer, and the answer decides. The approved-app key is
written only after you accept, and approvals persist for the session.
`list_apps`, `list_windows`, `get_window` and `status` are read-only and
never prompt.

If your harness does not support elicitation, the action fails with a
clear error naming the app, rather than proceeding unapproved.

`--auto-approve` skips the prompts entirely. The flag says so in its own
help text, and it is not recommended.

On the `cua` backend, **this server has no per-app gate**. The driver
applies its own permission model to each action, and the driver's daemon
can be started in `standard`, `bounded` or `unrestricted` permission mode.
`unrestricted` is the one to be careful with; it is off by default.

### Taking the computer back

One chord stops the agent, from outside the harness, at any moment:

```
Ctrl + Alt + Shift + Backspace
```

Press it once and the agent loses the computer. Press it again and it
gets it back. One press stops the action in flight, takes the banner
down, revokes every approval, and leaves the server running so it can be
resumed. The chord is armed automatically whenever the server starts.

The physical **Escape** key still works too — the Codex helper watches it
itself. The chord exists because Escape is one keystroke away from
almost anything.

[Full documentation →](takeover-hotkey.md)

### What leaves the machine

**The server itself makes no network calls.** There is no cloud service,
no account, no usage limit, no telemetry in this project.

What does leave the machine is what your harness already sends to your
model provider: the accessibility tree and the screenshot the server
returns go to your harness, and from there to whichever model you use. If
your harness talks to a hosted model, that content goes to that model —
the same as any other tool output.

During installation, the installers do download from the internet: `uv`
fetches Python and the Python packages from PyPI, the cua driver
installer downloads the driver, and `winget` and `git clone` fetch from
their own sources.

**The cua driver sends anonymous telemetry by default.** That is a
separate thing from the server, and it is on unless you turn it off:

```powershell
cua-driver telemetry status
cua-driver telemetry disable
```

`cua-driver telemetry status` prints whether it is on and where the
setting came from. The driver also checks GitHub for updates when you ask
it to (`cua-driver check-update`).

### The banner

While the Codex helper is driving, it draws a banner on screen. Both
sentences in it are configurable, in a file the helper already reads:

```
%USERPROFILE%\.codex\computer-use\config.json
```

That file is per-user, so it does **not** copy to the new machine. The
new machine shows the helper's defaults until you set it. From the
repository root:

```powershell
.\.venv\Scripts\python.exe scripts\set-overlay-text.py --using "AI is controlling this computer" --hint "Press Ctrl+Alt+Shift+Backspace to take over"
```

There is no text banner on the `cua` backend. The cua driver draws an
agent cursor — a one-hand index-finger hand — and nothing else.

---

## Upgrading (git pull, re-run the installer)

```powershell
cd C:\path\to\windows-cua
git pull
```

Because the package is installed in editable mode, `git pull` updates the
running code. There is normally nothing to reinstall.

**Re-run the installer anyway**, because it is cheap and it fixes the
things a `git pull` cannot. From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Harness claude
```

It reuses the existing `.venv`, re-installs the package (fast, and it
picks up any new dependency in `pyproject.toml`), and rewrites the
harness registration so the absolute path and the command line match the
current checkout. Use the same `-Harness` and `-Backend` values you used
the first time.

If the driver itself is the thing that changed, update it separately:

```powershell
cua-driver check-update
cua-driver update --apply
```

The Codex helper ships with the Codex desktop app, so a helper update
means a desktop-app update. After one, re-run the probes — the helper's
undocumented behaviours are the fragile part of this project. Both run
from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\probe_helpers.py
```

```powershell
.\.venv\Scripts\python.exe scripts\probe_cua_backend.py
```

Restart the harness (or the MCP server) after any upgrade. The server is
spawned by the harness, so the old code keeps running until the harness
starts a new one.

---

## Uninstalling (exactly what to remove, and where)

### Step 1: remove the harness registrations

```powershell
cd C:\path\to\windows-cua
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Uninstall
```

That removes exactly three things, and nothing else:

| File | What is removed |
|---|---|
| `%USERPROFILE%\.claude.json` | the `mcpServers.codex-cua` entry |
| `%USERPROFILE%\.codex\config.toml` | the `[mcp_servers.codex-cua]` table |
| `%USERPROFILE%\.config\opencode\opencode.json` | the `mcp.codex-cua` entry |

The rest of each file is edited in place, byte for byte, so your other
settings and their formatting survive. The virtualenv, the repository and
every other setting are left alone. It is safe to run twice, and safe to
run when you never installed anything. If it cannot remove one of the
entries it says which file, leaves that file unchanged rather than
risking it, and exits non-zero.

### Step 2: remove what you want gone

Nothing below is required — the server is already unregistered after step
1. These are the leftovers, and each one is optional to remove.

| What | How to remove it |
|---|---|
| The repository and its `.venv` | Delete the folder. |
| The cua driver (if you installed it) | `irm https://cua.ai/driver/uninstall.ps1 \| iex` |
| The cua driver's scheduled task, if you want to keep the driver but stop it starting at logon | `cua-driver autostart disable` |
| The cua driver daemon, if it is running right now | `cua-driver stop` |
| The Codex desktop app (if you installed it only for this) | Windows Settings → Apps. |
| The `codex` CLI, if you installed it only for this | `npm uninstall -g @openai/codex` |
| The banner text file | `%USERPROFILE%\.codex\computer-use\config.json` |
| `uv` | `winget uninstall --id=astral-sh.uv` |
| Any Python `uv` downloaded | `uv python uninstall --all` (removes every Python `uv` manages) |

If you want to keep the driver but stop it collecting anything:

```powershell
cua-driver telemetry disable
```

---

## Troubleshooting a fresh machine (the things that actually go wrong, in order)

Work down this list; it is ordered by how often each one happens.

### 1. `uv not found. Install it with: winget install --id=astral-sh.uv`

The installer stops here and exits `1`. It does nothing else. Install `uv`,
open a **new** PowerShell window, and run the installer again.

### 2. `codex-computer-use.exe not found`

The single most common failure. The helper ships with the Codex **desktop
app**, not with the npm CLI. `npm install -g @openai/codex` is not enough
— it installs the CLI and no helper.

Check what is actually on the machine:

```powershell
Get-ChildItem -Path "$env:LOCALAPPDATA\OpenAI\Codex\runtimes" -Filter "codex-computer-use.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
```

Fix it by installing the Codex desktop app, or by pointing at a helper you
already have:

```powershell
$env:CODEX_COMPUTER_USE_BIN = "C:\path\to\codex-computer-use.exe"
```

Or switch to the `cua` backend, which needs no OpenAI binary at all.

### 3. `codex cli : (not found)`

The CLI is used for the approval handshake. Without it the helper cannot
broker the "allow Computer Use to use this app?" prompt, so nothing new
gets approved — read-only calls still work. Install it with
`npm install -g @openai/codex`, or point at it with `CODEX_CLI_PATH`.

### 4. `Installation INCOMPLETE.` / `server check FAILED (exit 2)`

The registration went in, but the server cannot start. The lines above
the message say why — usually a missing binary, in which case the check
printed `error: ...` on stderr. Fix that, then re-run the installer.
(`Installation INCOMPLETE.` on its own can also mean a registration
failed; the lines above it say which.)

### 5. `codex-cua-mcp` is not recognised

The virtualenv is not activated. Use the full path, from the
repository root:

```powershell
.\.venv\Scripts\codex-cua-mcp.exe --check
```

### 6. The server was registered but the harness cannot start it

The registered command is an **absolute path** to
`...\windows-cua\.venv\Scripts\python.exe`. If you moved or renamed the
repo folder, that path is dead. Re-run the installer to rewrite it.

### 7. `The scripts are blocked on this system` / `running scripts is disabled`

Windows ships with a `Restricted` execution policy. Use the `-File` form
with the bypass scoped to that one command, from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Harness claude
```

If your organisation manages the policy, ask it to allow signed local
scripts instead.

### 8. `ModuleNotFoundError: No module named 'codex_cua_mcp.takeover'`

The checkout is older than the code you are running. `git pull`, or
re-clone.

### 9. `Could not find cua-driver.exe`

On the `cua` backend, the driver is not installed or is somewhere the
search does not look. The search order is
`%USERPROFILE%\.cua-driver\packages\current\`, then
`%USERPROFILE%\.cua-driver\packages\releases\`, then `PATH`. Install it:

```powershell
irm https://cua.ai/driver/install.ps1 | iex
cua-driver autostart kick
```

Or set `CUA_DRIVER_BIN` to its full path.

### 10. `cua-driver` is installed but the daemon is not running

The server spawns `cua-driver.exe mcp`, which is a client of the
long-lived `cua-driver serve` daemon. If the daemon is not running, the
server has nothing to talk to.

```powershell
cua-driver status
cua-driver autostart kick
```

### 11. The opencode registration was refused

If `~\.config\opencode\opencode.json` is not valid JSON, the installer
refuses to touch it, says so, and exits `1`. Fix the JSON by hand (or
delete the file) and re-run. The installer never writes a config it
cannot read back.

### 12. The banner says something you did not choose

`%USERPROFILE%\.codex\computer-use\config.json` is per-user and does not
travel to a new machine. See what the file says on each machine, from
the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\set-overlay-text.py --show
```

To change it, pass `--using` and `--hint` — see
[The banner](#the-banner) above.

### 13. The agent did not stop when you pressed the chord

Call the `status` tool. It reports the take-over mode: `hotkey` means
Windows accepted the chord, `polling` means another application already
owned it and the server fell back to polling, `none` means it is
switched off. See [Taking the computer back](takeover-hotkey.md).

### 14. Everything works, but the driver is collecting telemetry

On the `cua` backend, telemetry is on by default:

```powershell
cua-driver telemetry disable
```

---

## See also

| Doc | What it is for |
|---|---|
| [Using it in other harnesses](using-other-harnesses.md) | Per-client config snippets, the flag reference, and the prompt block that tells an agent the server exists. |
| [Taking the computer back](takeover-hotkey.md) | The take-over chord and the banner text. |
| [For new users](for-new-users.md) | The short version, if you have never set up an MCP server before. |
| [Architecture](architecture.md) | How the pieces fit together. |

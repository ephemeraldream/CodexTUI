# CodexPlus

CodexPlus is an unofficial local workbench for the OpenAI Codex CLI.
It makes local Codex history easier to browse, search, inspect, and resume.
It delegates all model work to the official `codex` executable.

CodexPlus does not call private Codex or ChatGPT backend endpoints.
It reads local Codex session files, renders cleaner transcripts, and launches official Codex CLI commands when you resume or stream a turn.

## What You Get

- A small CLI named `cxp`.
- A terminal UI for browsing sessions and running follow-up prompts.
- Clean transcript views without system, developer, tool, and bootstrap noise.
- Search across clean user and assistant messages.
- Project-scoped history with `--here`.
- File-reference extraction from sessions, including optional jump-to-editor.
- Optional `fzf` pickers for fast keyboard navigation.
- CodexPlus-owned JSON streaming through `codex exec --json`.
- A `cxp doctor` command that checks CodexPlus, Codex CLI, login, history, and optional `fzf`.
- A bootstrap script for first-time setup from a cloned checkout.
- An optional `codex` shim for helper commands such as `codex h` and `codex view`.

Compression is intentionally not implemented in v0.1.
The planned design is a local summary sidecar that never rewrites Codex internal history.

## Fast Install

The shortest setup is clone, bootstrap, then open the TUI.

```bash
git clone https://github.com/ephemeraldream/CodexPlus.git
cd CodexPlus
scripts/bootstrap-codexplus.sh --yes
cxp tui
```

The bootstrap script checks Python, finds or installs Codex CLI, runs Codex login when needed, installs CodexPlus, and finishes with `cxp doctor`.
It uses `pipx install -e .` when `pipx` is available.
If `pipx` is not available, it creates `.venv` in the checkout and prints the exact `cxp` command to run.

If Codex is installed in a custom location, pass the executable path once.

```bash
scripts/bootstrap-codexplus.sh --codex-bin /path/to/codex --yes
```

CodexPlus stores that path in `~/.config/codexplus/config.json`.
Future `cxp stream`, `cxp tui`, and `cxp doctor` runs will use it automatically.

If you want API-key login instead of browser login, pass the key through the environment.

```bash
OPENAI_API_KEY=sk-... scripts/bootstrap-codexplus.sh --with-api-key
```

If you only want diagnostics and do not want the script to install anything, run this.

```bash
scripts/bootstrap-codexplus.sh --check-only
```

## Requirements

CodexPlus requires Python 3.11 or newer.
CodexPlus can browse existing local history without a logged-in Codex CLI, but streaming and new prompts require an authenticated official Codex CLI.

Official Codex setup references:

- https://developers.openai.com/codex/cli
- https://developers.openai.com/codex/auth

Install Codex manually if you do not want the bootstrap script to do it.

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

Other official install options include npm and Homebrew.

```bash
npm install -g @openai/codex
brew install --cask codex
```

Then authenticate Codex.

```bash
codex login
codex login status
```

Or authenticate with an API key.

```bash
printenv OPENAI_API_KEY | codex login --with-api-key
```

## Manual CodexPlus Install

Use `pipx` for an isolated local CLI install.

```bash
git clone https://github.com/ephemeraldream/CodexPlus.git
cd CodexPlus
pipx install -e .
```

Install directly from GitHub with HTTPS.

```bash
pipx install git+https://github.com/ephemeraldream/CodexPlus.git
```

Install directly from GitHub with SSH.

```bash
pipx install git+ssh://git@github.com/ephemeraldream/CodexPlus.git
```

If `pipx` is not available, use a virtual environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

## Verify Setup

Run the doctor first when setting up a new machine or debugging a user report.

```bash
cxp doctor
```

Emit JSON for scripts or bug reports.

```bash
cxp doctor --json
```

Use a one-off custom Codex binary without saving it.

```bash
cxp doctor --codex-bin /path/to/codex
```

The doctor checks:

- Python version.
- CodexPlus version.
- Official Codex binary path.
- Codex CLI version.
- Codex login status.
- Support for `codex exec --json` and `codex exec resume`.
- Local Codex history.
- Optional `fzf`.
- Whether `cxp` is on `PATH`.

## Quick Commands

```bash
cxp h
cxp tui
cxp list
cxp search "dividends"
cxp search "dividends" --here
cxp view last
cxp files last
cxp assistant last
cxp final 019f4bc1
cxp resume 019f4bc1
cxp stream "fix the failing test"
cxp doctor
```

Running `cxp` without a subcommand opens the same session browser as `cxp h`.
Running `cxp history` also opens the session browser.

## Terminal UI

Open the terminal UI.

```bash
cxp tui
```

The TUI shows a session list on the left and a clean preview on the right.
If no local Codex history exists yet, the TUI opens an empty state and lets you press `n` to start a fresh Codex prompt.

TUI keys:

| Key | Action |
| --- | --- |
| `Tab` | Switch focus between session list and preview. |
| `Up`, `Down`, `k`, `j` | Move the selected session or scroll the focused preview. |
| `PageUp`, `PageDown` | Move or scroll by a larger step. |
| `Enter` | Ask a follow-up prompt in the selected session. |
| `n` | Start a fresh Codex prompt through `codex exec --json`. |
| `r` | Refresh session metadata. |
| `v` | Show clean chat preview. |
| `a` | Show assistant-only preview. |
| `f` | Show final-answer preview. |
| `u` | Show user-turn preview. |
| `o` | Show mentioned-file preview. |
| `q` or `Esc` | Quit. |

Follow-up prompts run through `codex exec resume --json`.
Fresh prompts run through `codex exec --json`.
CodexPlus captures the stream inside the TUI instead of handing the terminal to Codex's interactive UI.

The stream pane renders submitted prompts, assistant text, task events, command calls, patch events, search activity, MCP tool calls, plan events, rollbacks, token counts, context compaction, rate-limit updates, and tool output.
After a stream finishes, use arrows or PageUp/PageDown to inspect earlier output before returning to the session dashboard.

## Browsing Sessions

Open the keyboard session picker.

```bash
cxp h
```

`cxp h` uses `fzf` when it is installed and the terminal is interactive.
Without `fzf`, use `cxp list`, `cxp view`, and `cxp resume` directly.

Inside `fzf`:

| Key | Action |
| --- | --- |
| `Enter` | Resume the selected session through official `codex resume`. |
| `Ctrl-V` | View clean history. |
| `Ctrl-F` | View the final answer. |
| `Ctrl-U` | View user turns. |
| `Ctrl-O` | View mentioned files. |
| `Ctrl-E` | Pick and edit a mentioned file. |
| `/` | Search inside the picker. |
| `Esc` | Cancel. |

List recent sessions.

```bash
cxp list --limit 20
```

Emit session rows as JSON lines.

```bash
cxp list --json
```

Filter sessions by metadata.

```bash
cxp list --query "kibana"
cxp list --source cli
cxp list --all
```

## Project-Scoped History

Use `--here` when you only want sessions from the current git workspace.

```bash
cxp h --here
cxp list --here
cxp search "rate limit" --here
cxp view --here
cxp files --here
```

Use `--cwd` when you want a specific workspace path.

```bash
cxp list --cwd /Users/alfa/work/CodexPlus
cxp search "doctor" --cwd /Users/alfa/work/CodexPlus
```

`--here` resolves to the nearest git root.
If no git root exists, it resolves to the current directory.

## Viewing Transcripts

Show the latest clean transcript.

```bash
cxp view last
```

Show a specific session by full id, prefix, title text, or rollout path.

```bash
cxp view 019f4bc1
cxp view "fix failing test"
cxp view ~/.codex/sessions/2026/07/16/rollout-example.jsonl
```

Show only assistant messages.

```bash
cxp assistant last
```

Show only the final answer.

```bash
cxp final last
```

Show only user turns.

```bash
cxp user last
```

Disable the pager when scripting.

```bash
cxp view last --no-pager
```

Disable color.

```bash
cxp view last --no-color
```

Filter assistant phases when you know the underlying Codex event phase.

```bash
cxp view last --phase final_answer
```

Print the rollout JSONL path for a session.

```bash
cxp path last
```

## Searching

Search clean transcript text.

```bash
cxp search "kibana"
```

Search only metadata such as title, preview, cwd, and id.

```bash
cxp search "CodexPlus" --metadata-only
```

Search within the current project.

```bash
cxp search "bootstrap" --here
```

Emit structured JSON lines.

```bash
cxp search "bootstrap" --json
```

Open matching sessions in the keyboard picker.

```bash
cxp search "bootstrap" --open
```

Select transcript mode for matching.

```bash
cxp search "final summary" --mode final
cxp search "user question" --mode user
cxp search "implementation detail" --mode assistant
```

## File References

List files mentioned in a session.

```bash
cxp files last
```

Emit file references as JSON lines.

```bash
cxp files last --json
```

Open a mentioned file through `fzf` and `$EDITOR`.

```bash
cxp files last --open
```

Choose an editor explicitly.

```bash
cxp files last --open --editor "code -g"
```

File detection is based on clean user and assistant messages.
It ignores hidden tool-call payloads so implementation details from raw Codex events do not pollute the file list.

## Resuming And Streaming

Resume a selected session through the official interactive Codex CLI.

```bash
cxp resume 019f4bc1
```

If no selector is provided, `cxp resume` opens the `fzf` picker when available.

```bash
cxp resume
```

Run a fresh non-interactive Codex turn through a CodexPlus-controlled JSON stream.

```bash
cxp stream "fix the failing test"
```

Resume an existing session through the same stream path.

```bash
cxp stream --resume 019f4bc1 "continue from here"
```

Read the prompt from stdin.

```bash
printf '%s\n' "summarize this repository" | cxp stream
```

Print raw Codex JSONL instead of the clean rendered stream.

```bash
cxp stream --raw-json "show the plan only"
```

Streaming requires a working official Codex CLI with `codex exec --json`.
Run `cxp doctor` if streaming fails.

## Optional `codex` Shim

CodexPlus can install an opt-in shim so selected helper commands work from `codex`.

```bash
cxp install-shim --target ~/.local/bin/codex --force
```

The shim routes helper commands to `cxp` and delegates everything else to the official Codex binary.

Examples:

```bash
codex h
codex search "dividends"
codex files last
codex view last
codex tui
codex stream "fix the failing test"
codex "regular prompt still goes to official Codex"
```

Use this only when you understand which `codex` binary your shell resolves first.
The bootstrap script can install the shim with `--install-shim`.

```bash
scripts/bootstrap-codexplus.sh --install-shim
```

## Configuration

CodexPlus detects the official Codex executable in this order:

1. `CODEX_REAL_BIN`.
2. `~/.config/codexplus/config.json`.
3. The standalone Codex path under `~/.codex/packages/standalone/current/bin/codex`.
4. `codex` on `PATH`.

Persist a custom Codex path during setup.

```bash
scripts/bootstrap-codexplus.sh --codex-bin /path/to/codex
```

The saved config is JSON.

```json
{
  "codex_bin": "/path/to/codex"
}
```

Use `CODEXPLUS_CONFIG_HOME` to move this config for tests or isolated environments.

```bash
CODEXPLUS_CONFIG_HOME=/tmp/cxp-config cxp doctor
```

Use `CODEX_HOME` when your Codex state directory is not `~/.codex`.

```bash
CODEX_HOME=/path/to/codex-home cxp list
```

## Troubleshooting

Run this first.

```bash
cxp doctor
```

If `cxp` is not found after installation, make sure the `pipx` binary directory is on `PATH`.
On many systems that directory is `~/.local/bin`.

If `cxp tui` says there are no sessions, press `n` to start a fresh Codex prompt or run Codex once directly to create local history.
History browsing depends on local Codex state files.

If streaming fails because Codex is not authenticated, run `codex login` or use `OPENAI_API_KEY=... scripts/bootstrap-codexplus.sh --with-api-key`.

If the wrong Codex binary is used, run the bootstrap script with `--codex-bin /path/to/codex`.
You can also set `CODEX_REAL_BIN=/path/to/codex` for one shell session.

If `cxp h` does not open the keyboard picker, install `fzf`.
The rest of the CLI works without `fzf`.

If Codex changes its local state schema, `cxp doctor` may still pass while specific history parsing needs an update.
Open an issue with the relevant command, error, and whether your history came from SQLite or JSONL.

## Safety Boundaries

CodexPlus is designed around these boundaries:

- Read Codex local state as read-only data.
- Do not modify `~/.codex/state_*.sqlite`.
- Do not modify rollout JSONL history.
- Do not include private prompts, `auth.json`, secrets, or local config in this repository.
- Do not reverse engineer private OpenAI services.
- Do not bypass rate limits or unsupported access controls.

## Development

Run tests with the standard library test runner.

```bash
python3 -m unittest discover -s tests
```

Run quick local smoke commands.

```bash
PYTHONPATH=src python3 -m codex_plus --version
PYTHONPATH=src python3 -m codex_plus doctor
PYTHONPATH=src python3 -m codex_plus list --limit 3
```

Build a local package when preparing a release.

```bash
python3 -m pip install build
python3 -m build
```

## Project Status

This project is early and intentionally conservative.
The current adapter is based on observed Codex CLI local state formats and may need updates when Codex changes its storage schema.

OpenAI and Codex are trademarks of OpenAI.
CodexPlus is not affiliated with, endorsed by, or sponsored by OpenAI.

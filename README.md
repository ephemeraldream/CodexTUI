# CodexPlus

CodexPlus is an unofficial local workbench for the OpenAI Codex CLI.
It improves local session navigation, transcript viewing, and resume ergonomics while delegating all model work to the official Codex CLI.

CodexPlus does not call private Codex or ChatGPT backend endpoints.
It reads local Codex session files, renders cleaner history, and resumes sessions through the installed `codex` binary.

## Why

The official Codex CLI is powerful, but local history navigation can be hard to scan.
Raw rollout JSONL files include system, developer, tool, and context events that are useful for debugging but noisy when you only want to find a past answer.

CodexPlus provides a small, installable CLI named `cxp`:

```bash
cxp h
cxp list
cxp search "dividends"
cxp search "dividends" --here
cxp view last
cxp files last
cxp assistant last
cxp final 019f4bc1
cxp resume 019f4bc1
cxp stream "fix the failing test"
```

## Current scope

CodexPlus v0.1 focuses on read-only local history and official session resume:

- List Codex sessions from `~/.codex/state_*.sqlite`.
- Fall back to scanning `~/.codex/sessions/**/*.jsonl`.
- Render clean transcripts without system, developer, and tool noise by default.
- Hide autonomous-run progress JSON from clean transcripts while keeping final structured answers visible.
- Render pure assistant JSON answers as pretty JSON in clean transcript views.
- Search across clean user and assistant messages.
- Scope list, browse, resume, and search commands to the current git workspace with `--here`.
- Scope single-session commands such as view, final, user, files, and path to the current git workspace with `--here`.
- Pick sessions and search matches through optional `fzf` previews with keyboard actions for resume, clean view, final answer, user turns, file references, and direct file editing.
- List files mentioned in clean session history and optionally jump to one in `$EDITOR`.
- Run Codex through `codex exec --json` and stream clean assistant text from CodexPlus instead of opening Codex's interactive TUI.
- Resume selected sessions through the official `codex resume` command.
- Install an optional shell shim for `codex h`, `codex view`, and related helper commands.

Compression is intentionally not implemented in v0.1.
The planned design is a local summary sidecar that never rewrites Codex internal history.

## Install from a checkout

Use `pipx` for an isolated CLI install:

```bash
git clone git@github.com:ephemeraldream/CodexPlus.git
cd CodexPlus
pipx install -e .
```

Install directly from GitHub:

```bash
pipx install git+ssh://git@github.com/ephemeraldream/CodexPlus.git
```

If `pipx` is not available, use a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

## Usage

Open the terminal session picker and resume a selected session:

```bash
cxp h
```

Inside `fzf`, press Enter to resume, Ctrl-V to view clean history, Ctrl-F for the final answer, Ctrl-U for user turns, Ctrl-O for files, or Ctrl-E to pick and edit a mentioned file.

List recent sessions:

```bash
cxp list --limit 20
```

Search clean transcript text:

```bash
cxp search "kibana"
```

Emit structured search matches for scripts or terminal pipelines:

```bash
cxp search "kibana" --json
```

Scope session browsing or search to the current git workspace:

```bash
cxp h --here
cxp search "kibana" --here
```

Show the latest clean transcript or file references from the current git workspace:

```bash
cxp view --here
cxp files --here
```

Pick a matching session from a keyboard-driven search surface:

```bash
cxp search "kibana" --open
```

Show only Codex messages from the latest session:

```bash
cxp assistant last
```

List files mentioned in the latest session:

```bash
cxp files last
```

Open a mentioned file through a keyboard picker when `fzf` is available:

```bash
cxp files last --open
```

Show the final answer from a session:

```bash
cxp final 019f4bc1
```

Resume a session directly:

```bash
cxp resume 019f4bc1
```

Run a new non-interactive Codex turn through a CodexPlus-controlled JSON stream:

```bash
cxp stream "fix the failing test"
```

Resume an existing session through the same stream path:

```bash
cxp stream --resume 019f4bc1 "continue from here"
```

## Optional Codex shim

CodexPlus can install an opt-in shim so selected helper commands work from `codex`:

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
codex stream "fix the failing test"
codex "regular prompt still goes to official Codex"
```

Use this only if you understand which `codex` binary your shell resolves first.

## Safety boundaries

CodexPlus is designed around these boundaries:

- Read Codex local state as read-only data.
- Do not modify `~/.codex/state_*.sqlite`.
- Do not modify rollout JSONL history.
- Do not include private prompts, `auth.json`, secrets, or local config in this repository.
- Do not reverse engineer private OpenAI services.
- Do not bypass rate limits or unsupported access controls.

## Development

Run tests with the standard library test runner:

```bash
python3 -m unittest discover -s tests
```

Run a quick local smoke command:

```bash
PYTHONPATH=src python3 -m codex_plus --version
PYTHONPATH=src python3 -m codex_plus list --limit 3
```

## Project status

This project is early and intentionally conservative.
The current adapter is based on observed Codex CLI local state formats and may need updates when Codex changes its storage schema.

OpenAI and Codex are trademarks of OpenAI.
CodexPlus is not affiliated with, endorsed by, or sponsored by OpenAI.

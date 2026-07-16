# CodexTUI Agent Instructions

These instructions apply to the whole repository.

## Project Purpose

CodexTUI is a terminal workbench for the official OpenAI Codex CLI.
It must keep Codex model execution delegated to the official `codex` binary.
It must make local Codex history, live streams, and follow-up prompts readable in a terminal UI.

## Product Direction

Prioritize a polished transcript and stream renderer over broad new command surface.
The target experience is a terminal approximation of the Codex VSCode plugin conversation view.
Readable role blocks, markdown, code blocks, tool activity, status lines, token usage, and scrollback behavior matter.
Raw Codex JSON must never leak into default user-facing views.
Raw JSON belongs only behind explicit flags such as `--raw-json`.

## Commands

Run the full test suite before committing behavior changes.

```bash
python3 -m unittest discover -s tests
```

Run focused stream renderer tests while working on live output.

```bash
python3 -m unittest discover -s tests -p 'test_codex_stream.py'
```

Run TUI unit tests while changing layout, keys, scrollback, or preview behavior.

```bash
python3 -m unittest discover -s tests -p 'test_tui.py'
```

Run CLI smoke checks after command, package, or rename work.

```bash
PYTHONPATH=src python3 -m codex_tui --version
PYTHONPATH=src python3 -m codex_tui doctor
```

Run bootstrap syntax checks after editing setup flow.

```bash
sh -n scripts/bootstrap-codextui.sh
```

Run the development context harness at the start and end of substantial work.

```bash
scripts/dev-context-harness.sh snapshot
```

## Local Harness

The local harness writes to `.codextui-harness/`.
That directory is intentionally ignored by git.
Use it for scratch notes, current task state, renderer observations, copied sample events, screenshots, and temporary comparison output.
Do not put secrets, auth files, private prompts from unrelated projects, or Codex account data in the harness.
Do not commit harness output.

## Changelog

`CHANGELOG.md` is a human-facing release log.
Do not edit it during routine feature work.
Update it only when the user explicitly asks for changelog or release-prep work.
Keep entries user-facing and grouped under `Added`, `Changed`, `Fixed`, and `Compatibility` when those sections apply.

## Renderer Rules

Renderer code must preserve a strict separation between event parsing and terminal presentation.
Parsing belongs in stream/transcript model code.
Terminal layout belongs in TUI rendering code.
Avoid making curses layout code understand raw Codex event schemas directly.

Add fixture-driven tests for every supported Codex JSON event shape.
Add an end-to-end reproduction note to `.codextui-harness/notes/renderer-notes.md` when a real Codex stream exposes a new event shape.
Unsupported JSON events should be hidden or rendered as a compact diagnostic, not printed as raw JSON.

## Compatibility

`ctui` is the primary command.
`cxp` and `codex_plus` are legacy compatibility shims.
Do not remove them without an explicit migration plan and release note.

## Markdown Style

Use one full sentence per physical line in long Markdown files.
Avoid the em dash character.
Use plain hyphen characters instead.

## Git Hygiene

Do not commit `dist/`, `.codextui-harness/`, `.lavish/`, caches, virtual environments, or local Codex state.
Do not include private prompts, `auth.json`, secrets, local API keys, or screenshots containing secrets.
Prefer small commits with one clear purpose.

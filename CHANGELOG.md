# Changelog

All notable user-facing changes to CodexTUI are recorded here.
This file follows a Keep a Changelog style.
Update it only during explicit changelog or release-prep work.

## [Unreleased]

### Planned

- Build a VSCode Codex plugin-like terminal renderer for transcripts and live streams.
- Add structured renderer models for messages, tool calls, status events, token usage, and errors.
- Add fixture coverage for current Codex CLI JSON stream event shapes.
- Add terminal layout tests for markdown, code blocks, wrapping, folding, and scrollback.

## [0.1.0] - 2026-07-16

### Added

- Added the `ctui` CLI for browsing, searching, viewing, resuming, and streaming local Codex sessions.
- Added a terminal UI with session navigation, preview modes, refresh, fresh prompts, follow-up prompts, and post-stream scrollback.
- Added clean transcript rendering that hides system, developer, tool, bootstrap, and autonomous progress noise by default.
- Added project-scoped browsing and search with `--here` and `--cwd`.
- Added file-reference extraction with optional editor jumping.
- Added optional `fzf` pickers for session, search, and file workflows.
- Added Codex JSON stream rendering through `codex exec --json`.
- Added `ctui doctor` diagnostics for Python, CodexTUI, Codex CLI, login, JSON streaming support, local history, `fzf`, and command path setup.
- Added a first-run bootstrap script at `scripts/bootstrap-codextui.sh`.
- Added custom Codex binary persistence through `~/.config/codextui/config.json`.

### Changed

- Renamed the project from CodexPlus to CodexTUI.
- Renamed the primary Python package from `codexplus` to `codextui`.
- Renamed the primary Python module from `codex_plus` to `codex_tui`.
- Rewrote the README as a complete installation and usage guide.

### Fixed

- Fixed current Codex CLI top-level JSON events leaking into the default stream renderer.
- Fixed first-run TUI behavior so an empty history can still start a fresh Codex prompt.
- Fixed persisted custom Codex binary detection for non-standard Codex installs.

### Compatibility

- Kept the legacy `cxp` command as an alias for existing local installs.
- Kept a minimal `codex_plus` Python module shim that dispatches to `codex_tui`.

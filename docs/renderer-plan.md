# VSCode-Like Renderer Plan

This plan defines the path from the current renderer work to a polished Codex VSCode plugin-like terminal conversation renderer.
The goal is not to copy VSCode UI pixel for pixel.
The goal is to bring the same information hierarchy, readability, and confidence to a terminal-first experience.

## Product Goal

CodexTUI should render Codex conversations as structured dialogue, not as event logs.
The user should see what they asked, what Codex answered, what Codex is doing, what tools ran, what changed, and whether the turn is still active.
The default view must never expose raw JSON.
Raw JSON must remain available only through explicit debug commands or `--raw-json`.

## Target Experience

The transcript should read as a conversation.
User messages should be visually distinct from assistant messages.
Assistant markdown should preserve paragraphs, lists, code fences, inline code, and compact tables where terminal width allows.
Tool activity should be folded into readable status blocks by default.
Long tool output should be collapsed behind an expandable summary in the TUI model, even if the initial implementation uses keyboard toggles rather than mouse interaction.
Token usage, rate limits, compaction, and turn state should appear as muted status lines.
Errors should be prominent, short, and actionable.

## Non-Goals

Do not implement a browser UI in this phase.
Do not rewrite Codex history files.
Do not depend on private Codex backend APIs.
Do not make terminal layout code parse raw Codex event schemas.
Do not make the default renderer show raw JSON for unsupported events.

## Current Problems

The default Textual TUI now renders historical transcripts as structured `TranscriptBlock` panels with Rich Markdown.
The legacy curses TUI still renders line arrays.
Historical transcript blocks and live stream blocks are visually closer, but they are not yet normalized through one typed event model.
Tool calls and outputs are grouped enough for folded output and patch expansion, but the grouping is still split between stream parsing and transcript block construction.
There is no snapshot harness for comparing visual output between changes.
There is unit coverage for Textual behavior, code fences, wrapping, and folding, but no golden visual renderer snapshots.

## Design Principles

Parse once into typed domain events.
Normalize historical transcript events and live stream events into the same render model.
Render from blocks, not raw strings.
Keep terminal presentation independent from Codex JSON schema details.
Make every lossy decision explicit and tested.
Prefer stable, boring terminal primitives before adding complex effects.
Make layout deterministic for tests.
Treat small terminals as first-class.
Keep the debug path available without compromising default readability.

## Architecture

### Layer 1: Event Normalization

Create a `src/codex_tui/render_model.py` module.
Define typed records for normalized conversation and activity events.
Suggested records:

- `UserMessage`
- `AssistantMessage`
- `ReasoningSummary`
- `ToolCall`
- `ToolOutput`
- `PatchSummary`
- `SearchSummary`
- `McpToolSummary`
- `PlanUpdate`
- `TokenUsage`
- `ContextCompaction`
- `TurnStarted`
- `TurnCompleted`
- `TurnFailed`
- `UnknownEvent`

Keep raw payloads out of these records by default.
Allow a debug-only `raw_preview` field with bounded text when needed.

### Layer 2: Block Builder

Create a `src/codex_tui/render_blocks.py` module.
Convert normalized events into stable display blocks.
Suggested block types:

- `RoleBlock`
- `MarkdownBlock`
- `CodeBlock`
- `StatusBlock`
- `ToolBlock`
- `DiffSummaryBlock`
- `ErrorBlock`
- `SeparatorBlock`

The block builder should group related tool call and tool output events by call id.
It should keep short successful tool activity compact.
It should surface failures and patch errors clearly.
It should mark large tool output as folded by default.

### Layer 3: Markdown-Aware Text Layout

Create a `src/codex_tui/terminal_markdown.py` module.
The default Textual TUI currently uses Rich Markdown for transcript panels.
Any shared terminal Markdown path should support a conservative subset first:

- paragraphs
- bullet lists
- numbered lists
- fenced code blocks
- inline code
- block quotes
- horizontal separators

Keep deterministic parsing and wrapping available for CLI and snapshot tests.
Preserve code fence indentation.
Wrap prose to terminal width.
Do not wrap code by default unless the line exceeds a hard safety width.

### Layer 4: Theme And Styling

Create a `src/codex_tui/theme.py` module.
Define semantic style names rather than curses attributes spread through the app.
Suggested styles:

- `user_header`
- `assistant_header`
- `assistant_final_header`
- `status_muted`
- `tool_header`
- `tool_success`
- `tool_warning`
- `tool_error`
- `code`
- `inline_code`
- `selection`

Support no-color mode from the same theme layer.
Keep color optional and never encode meaning only through color.

### Layer 5: TUI Integration

Keep terminal app modules responsible for layout, focus, scrolling, input, and pane lifecycle.
The default TUI lives in `tui_textual.py`.
The legacy curses runner remains in `tui.py` behind `run_curses_tui`.
Move transcript formatting out of `tui.py`.
Make preview rendering call a shared block renderer.
Make live stream rendering append blocks instead of appending raw strings.
Keep scroll position based on rendered visual rows, not source event count.

## Live Stream Behavior

When a turn starts, show a muted active-turn status line.
When an assistant message arrives, render it as assistant text.
When tool calls run, show a compact tool block.
When tool output is short, show a short preview.
When tool output is long, show a folded block with line count and byte count.
When a patch applies, show changed file basenames and success state.
When token usage arrives, show one compact muted line.
When the turn completes, update the turn status instead of duplicating noisy completion text.
When the turn fails, show a clear error block.

## Historical Transcript Behavior

Historical transcript rendering should use the same block model as live streams.
It should keep existing modes:

- `chat`
- `assistant`
- `final`
- `user`
- `files`

Mode filtering should happen before block building.
The final-answer view should preserve structured JSON answers as pretty JSON inside a code-like block.

## Keyboard Interaction

The first renderer phase remains keyboard-only.
`README.md` owns the current user-visible TUI key map.
Renderer-specific keys must stay visible in the footer and covered by tests.
The current expandable-block contract is that `Enter` or `t` toggles the selected expandable block, and `Ctrl-j`, `Ctrl-k`, `Alt-j`, or `Alt-k` scroll inside the selected long block.

## Test Strategy

Use fixture-driven tests for event normalization.
Use deterministic width-based tests for block layout.
Use regression tests for every Codex event shape observed in real streams.
Use golden text snapshots only for stable renderer outputs.
Keep golden snapshots small and focused.
Prefer semantic assertions for block models before snapshot assertions.

Required test groups:

- current top-level stream events
- legacy `event_msg` stream events
- legacy `response_item` stream events
- assistant markdown paragraphs and lists
- fenced code blocks
- long tool output folding
- patch summary rendering
- token usage rendering
- terminal widths below 80 columns
- no-color mode
- legacy `cxp` compatibility path

## Harness Usage

Run a local context snapshot before renderer work.

```bash
scripts/dev-context-harness.sh snapshot
```

Store local real-stream observations in `.codextui-harness/notes/renderer-notes.md`.
Store local scrubbed JSON samples in `.codextui-harness/fixtures/` until they are safe to commit.
Promote stable, non-private samples into `tests/fixtures/`.
Do not commit `.codextui-harness/`.

## Implementation Phases

### Phase 1: Renderer Model Foundation

Create normalized event records.
Route current stream parsing through those records.
Add tests for every event shape currently supported by `codex_stream.py`.
Keep existing user-visible output equivalent except where it currently leaks JSON.

Exit criteria:

- No default raw JSON leaks from supported or unknown JSON events.
- Full test suite passes.
- Real `ctui stream` smoke output is readable.

### Phase 2: Block Renderer

Build display blocks from normalized events.
Render blocks to plain terminal rows for CLI and TUI.
Keep color optional.
Add width-based wrapping tests.

Exit criteria:

- Transcript previews and live streams share the same block renderer.
- Markdown paragraphs, lists, and code fences render predictably.
- Existing transcript commands still work.

### Phase 3: Tool Folding And Status Hierarchy

Group tool call and output events by call id.
Fold long outputs by default.
Show patch, search, MCP, token, and compaction events as status blocks.
Preserve keyboard toggle support in the TUI for folded tool blocks.

Exit criteria:

- Long tool output no longer dominates the viewport.
- Failed tool calls and patch failures are easy to spot.
- The footer documents all new keys.

### Phase 4: Visual Polish

Add semantic themes.
Tune spacing, separators, role headers, and selected-row states.
Verify small terminal behavior.
Run manual TUI smoke checks with real Codex sessions.

Exit criteria:

- The default TUI view is comfortable for repeated daily use.
- Text does not overlap, truncate badly, or leak raw events.
- No-color mode remains readable.

### Phase 5: Regression Harness

Add a command or test helper that renders a fixture transcript at fixed widths.
Store stable golden outputs for representative cases.
Use the harness before substantial renderer rewrites.

Exit criteria:

- Renderer diffs are reviewable.
- Future agents can change presentation without guessing what broke.

## Acceptance Criteria

The default TUI stream must never show raw JSON.
The default transcript preview must never show raw JSON unless the actual assistant answer is JSON.
User and assistant messages must be visually distinct.
Code blocks must remain readable.
Long tool output must be foldable.
Token and rate-limit updates must be compact.
Errors must be prominent and actionable.
Renderer behavior must be covered by unit tests and at least one real-stream smoke note.

## Open Questions

How much of the legacy curses TUI should remain once the Textual path settles?
Should folded tool blocks persist state per session while the TUI is open?
Should historical transcript rendering expose debug-only raw event inspection?
How close should colors match the Codex VSCode plugin when terminal themes vary heavily?
Should renderer snapshots be plain text only, or include ANSI snapshots for theme validation?

#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
HARNESS_DIR=${CODEXTUI_HARNESS_DIR:-"$ROOT_DIR/.codextui-harness"}

usage() {
  cat <<'EOF'
Usage: scripts/dev-context-harness.sh [command]

Commands:
  snapshot   Refresh local ignored context files for the current checkout.
  show       Print the harness index path and current git status summary.
  clean      Remove generated harness snapshots, notes, scratch files, and logs.
  help       Show this help.

The harness writes to .codextui-harness by default.
Set CODEXTUI_HARNESS_DIR to use a different local directory.
EOF
}

ensure_dirs() {
  mkdir -p "$HARNESS_DIR/state" "$HARNESS_DIR/notes" "$HARNESS_DIR/scratch" "$HARNESS_DIR/logs" "$HARNESS_DIR/fixtures"
}

write_index() {
  now=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  branch=$(git -C "$ROOT_DIR" branch --show-current 2>/dev/null || printf 'unknown')
  commit=$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || printf 'unknown')
  cat >"$HARNESS_DIR/INDEX.md" <<EOF
# CodexTUI Local Harness

Generated: $now
Branch: $branch
Commit: $commit

This directory is intentionally ignored by git.
Use it to keep local development context between agent turns.
Do not store secrets, auth files, unrelated private prompts, or account data here.

## Files

- \`state/git-status.txt\` has the current branch and worktree status.
- \`state/recent-commits.txt\` has the latest commits.
- \`state/file-inventory.txt\` has a sorted repository file list.
- \`state/test-commands.md\` has the verification commands expected for this repository.
- \`state/search-markers.txt\` has TODO, FIXME, HACK, and renderer markers.
- \`notes/renderer-notes.md\` is for local observations about real Codex stream events.
- \`scratch/\` is for throwaway experiments.
- \`fixtures/\` is for local uncommitted renderer samples.
- \`logs/\` is for captured command output.
EOF
}

write_test_commands() {
  cat >"$HARNESS_DIR/state/test-commands.md" <<'EOF'
# Test Commands

Run the full suite before committing behavior changes.

```bash
python3 -m unittest discover -s tests
```

Run focused stream renderer tests while changing live output parsing.

```bash
python3 -m unittest discover -s tests -p 'test_codex_stream.py'
```

Run focused TUI tests while changing layout, keys, scrollback, or preview behavior.

```bash
python3 -m unittest discover -s tests -p 'test_tui.py'
```

Run command smoke checks after package, CLI, or rename work.

```bash
PYTHONPATH=src python3 -m codex_tui --version
PYTHONPATH=src python3 -m codex_tui doctor
```

Run setup syntax checks after editing bootstrap flow.

```bash
sh -n scripts/bootstrap-codextui.sh
```
EOF
}

write_renderer_notes() {
  if [ -f "$HARNESS_DIR/notes/renderer-notes.md" ]; then
    return
  fi
  cat >"$HARNESS_DIR/notes/renderer-notes.md" <<'EOF'
# Renderer Notes

Use this local ignored file for observations from real Codex streams.
Copy only minimal synthetic or scrubbed event samples into local fixtures.
Move stable, non-private event samples into tracked tests when they become regression coverage.

## Current Questions

- Which Codex CLI event shapes are still not represented by structured renderer tests?
- Which events should be visible as status lines?
- Which events should be folded by default?
- Which events should be hidden unless a debug mode is enabled?
EOF
}

snapshot() {
  ensure_dirs
  write_index
  write_test_commands
  write_renderer_notes
  git -C "$ROOT_DIR" status --short --branch >"$HARNESS_DIR/state/git-status.txt"
  git -C "$ROOT_DIR" log --oneline --decorate --max-count=25 >"$HARNESS_DIR/state/recent-commits.txt"
  if command -v rg >/dev/null 2>&1; then
    rg --files "$ROOT_DIR" \
      -g '!*__pycache__*' \
      -g '!dist/*' \
      -g '!.git/*' \
      -g '!.codextui-harness/*' \
      | sed "s#^$ROOT_DIR/##" \
      | sort >"$HARNESS_DIR/state/file-inventory.txt"
    rg -n "TODO|FIXME|HACK|renderer|Renderer|render_|stream" "$ROOT_DIR" \
      -g '!*__pycache__*' \
      -g '!dist/*' \
      -g '!.git/*' \
      -g '!.codextui-harness/*' \
      >"$HARNESS_DIR/state/search-markers.txt" || true
  else
    find "$ROOT_DIR" -type f \
      -not -path '*/.git/*' \
      -not -path '*/dist/*' \
      -not -path '*/__pycache__/*' \
      -not -path '*/.codextui-harness/*' \
      | sed "s#^$ROOT_DIR/##" \
      | sort >"$HARNESS_DIR/state/file-inventory.txt"
    : >"$HARNESS_DIR/state/search-markers.txt"
  fi
  printf 'Harness snapshot written to %s\n' "$HARNESS_DIR"
}

show() {
  ensure_dirs
  printf 'Harness: %s\n' "$HARNESS_DIR"
  if [ -f "$HARNESS_DIR/INDEX.md" ]; then
    printf 'Index: %s\n' "$HARNESS_DIR/INDEX.md"
  else
    printf 'Index: missing, run snapshot first.\n'
  fi
  git -C "$ROOT_DIR" status --short --branch
}

clean() {
  rm -rf "$HARNESS_DIR"
  printf 'Removed %s\n' "$HARNESS_DIR"
}

case "${1:-snapshot}" in
  snapshot)
    snapshot
    ;;
  show)
    show
    ;;
  clean)
    clean
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

from __future__ import annotations

from dataclasses import dataclass
import shlex
import shutil
import subprocess
import sys

from .file_nav import FileHit
from .models import SearchMatch, ThreadRow
from .transcript import format_ms, short_id, truncate


ACTION_KEYS = {
    "ctrl-v": "view",
    "ctrl-f": "final",
    "ctrl-u": "user",
    "ctrl-o": "files",
    "ctrl-e": "edit_file",
}
EXPECT_ACTION_KEYS = ",".join(ACTION_KEYS)


@dataclass(frozen=True)
class PickerSelection:
    action: str
    value: str


def is_available() -> bool:
    return shutil.which("fzf") is not None and sys.stdin.isatty() and sys.stdout.isatty()


def choose_thread(
    threads: list[ThreadRow],
    *,
    mode: str = "chat",
    allow_actions: bool = True,
) -> PickerSelection | None:
    rows = [row_for_thread(thread) for thread in threads]
    preview = preview_command(mode)
    args = [
        "fzf",
        "--ansi",
        "--delimiter",
        "\t",
        "--with-nth",
        "2..",
        "--no-sort",
        "--prompt",
        "cxp sessions> ",
        "--header",
        session_header(allow_actions),
        "--preview",
        preview,
        "--preview-window",
        "right,65%,wrap",
    ]
    if allow_actions:
        args.insert(1, f"--expect={EXPECT_ACTION_KEYS}")
    selected = subprocess.run(
        args,
        input="\n".join(rows),
        text=True,
        capture_output=True,
    )
    return parse_selection(selected.returncode, selected.stdout, ACTION_KEYS if allow_actions else {})


def choose_file(hits: list[FileHit]) -> FileHit | None:
    rows = [row_for_file(hit) for hit in hits]
    selected = subprocess.run(
        [
            "fzf",
            "--ansi",
            "--delimiter",
            "\t",
            "--with-nth",
            "3..",
            "--no-sort",
            "--prompt",
            "cxp files> ",
            "--header",
            "enter opens selected file, preview shows the target area, use / to search, esc cancels",
            "--preview",
            file_preview_command(),
            "--preview-window",
            "right,65%,wrap",
        ],
        input="\n".join(rows),
        text=True,
        capture_output=True,
    )
    if selected.returncode != 0 or not selected.stdout.strip():
        return None
    selected_path = selected.stdout.split("\t", 1)[0].strip()
    return next((hit for hit in hits if hit.resolved_path == selected_path), None)


def choose_search_match(matches: list[SearchMatch], *, mode: str = "chat") -> PickerSelection | None:
    rows = [row_for_search_match(match) for match in matches]
    selected = subprocess.run(
        [
            "fzf",
            f"--expect={EXPECT_ACTION_KEYS}",
            "--ansi",
            "--delimiter",
            "\t",
            "--with-nth",
            "2..",
            "--no-sort",
            "--prompt",
            "cxp search> ",
            "--header",
            action_header("selected match", search_word="refine"),
            "--preview",
            preview_command(mode),
            "--preview-window",
            "right,65%,wrap",
        ],
        input="\n".join(rows),
        text=True,
        capture_output=True,
    )
    return parse_selection(selected.returncode, selected.stdout, ACTION_KEYS)


def parse_selection(returncode: int, stdout: str, key_actions: dict[str, str]) -> PickerSelection | None:
    if returncode != 0 or not stdout.strip():
        return None
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    first = lines[0].strip()
    if first in key_actions:
        if len(lines) < 2:
            return None
        return PickerSelection(key_actions[first], selected_id(lines[1]))
    return PickerSelection("resume", selected_id(lines[0]))


def selected_id(row: str) -> str:
    return row.split("\t", 1)[0].strip()


def session_header(allow_actions: bool) -> str:
    if not allow_actions:
        return "enter resumes selected session, preview is clean history, use / to search, esc cancels"
    return action_header("selected session", search_word="search")


def action_header(target: str, *, search_word: str) -> str:
    return (
        f"enter resumes {target}, ctrl-v views, ctrl-f final, ctrl-u user turns, "
        f"ctrl-o files, ctrl-e edits a file, preview is clean history, use / to {search_word}, esc cancels"
    )


def preview_command(mode: str) -> str:
    executable = shlex.quote(sys.executable)
    return f"{executable} -m codex_plus preview {{1}} --mode {shlex.quote(mode)}"


def file_preview_command() -> str:
    executable = shlex.quote(sys.executable)
    return f"{executable} -m codex_plus file-preview {{1}} {{2}}"


def row_for_thread(thread: ThreadRow) -> str:
    title = truncate(thread.title or thread.first_user_message or thread.preview, 120)
    return "\t".join(
        [
            thread.id,
            format_ms(thread.recency_at_ms),
            thread.source or "?",
            short_id(thread.id),
            title,
            thread.cwd or "?",
        ]
    )


def row_for_file(hit: FileHit) -> str:
    line = str(hit.line) if hit.line is not None else "-"
    status = "ok" if hit.exists else "missing"
    return "\t".join(
        [
            hit.resolved_path,
            line,
            hit.display_path,
            f"{line:>5}",
            f"{hit.count:>3}",
            status,
            truncate(hit.context, 140),
        ]
    )


def row_for_search_match(match: SearchMatch) -> str:
    thread = match.thread
    title = truncate(thread.title or thread.first_user_message or thread.preview, 88)
    return "\t".join(
        [
            thread.id,
            format_ms(thread.recency_at_ms),
            f"{match.role:9}",
            short_id(thread.id),
            title,
            truncate(match.snippet, 140),
            thread.cwd or "?",
        ]
    )

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys

from .file_nav import FileHit
from .models import SearchMatch, ThreadRow
from .transcript import format_ms, short_id, truncate


def is_available() -> bool:
    return shutil.which("fzf") is not None and sys.stdin.isatty() and sys.stdout.isatty()


def choose_thread(threads: list[ThreadRow], *, mode: str = "chat") -> str | None:
    rows = [row_for_thread(thread) for thread in threads]
    preview = preview_command(mode)
    selected = subprocess.run(
        [
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
            "enter resumes selected session, preview is clean history, use / to search, esc cancels",
            "--preview",
            preview,
            "--preview-window",
            "right,65%,wrap",
        ],
        input="\n".join(rows),
        text=True,
        capture_output=True,
    )
    if selected.returncode != 0 or not selected.stdout.strip():
        return None
    return selected.stdout.split("\t", 1)[0].strip()


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


def choose_search_match(matches: list[SearchMatch], *, mode: str = "chat") -> str | None:
    rows = [row_for_search_match(match) for match in matches]
    selected = subprocess.run(
        [
            "fzf",
            "--ansi",
            "--delimiter",
            "\t",
            "--with-nth",
            "2..",
            "--no-sort",
            "--prompt",
            "cxp search> ",
            "--header",
            "enter resumes selected session, preview is clean history, use / to refine, esc cancels",
            "--preview",
            preview_command(mode),
            "--preview-window",
            "right,65%,wrap",
        ],
        input="\n".join(rows),
        text=True,
        capture_output=True,
    )
    if selected.returncode != 0 or not selected.stdout.strip():
        return None
    return selected.stdout.split("\t", 1)[0].strip()


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

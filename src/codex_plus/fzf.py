from __future__ import annotations

import shlex
import shutil
import subprocess
import sys

from .models import ThreadRow
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


def preview_command(mode: str) -> str:
    executable = shlex.quote(sys.executable)
    return f"{executable} -m codex_plus preview {{1}} --mode {shlex.quote(mode)}"


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

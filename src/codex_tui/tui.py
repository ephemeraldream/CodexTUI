from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass, field
from typing import Callable, Protocol, TextIO

from .codex_stream import codex_exec_command, run_codex_json_stream
from .file_nav import file_hits_for_thread, render_file_hits
from .models import ThreadRow
from .paths import real_codex_bin
from .store import CodexStore
from .theme import TuiTheme, build_curses_theme
from .transcript import format_ms, render_thread, short_id, truncate


SESSION_ROW_HEIGHT = 2
PREVIEW_MODE_TABS = (
    ("chat", "chat"),
    ("assistant", "asst"),
    ("final", "final"),
    ("user", "user"),
    ("files", "files"),
)


class StreamOutput(Protocol):
    def write(self, text: str) -> int: ...

    def flush(self) -> None: ...


StreamRunner = Callable[[ThreadRow, str, StreamOutput], int]
NewStreamRunner = Callable[[str, StreamOutput], int]
ThreadLoader = Callable[[], list[ThreadRow]]


@dataclass
class TuiApp:
    threads: list[ThreadRow]
    stream_runner: StreamRunner
    new_stream_runner: NewStreamRunner | None = None
    thread_loader: ThreadLoader | None = None
    mode: str = "chat"
    selected: int = 0
    top: int = 0
    focus: str = "sessions"
    preview_top: int = 0
    status: str = "Enter continues the selected session; n starts a new CodexTUI JSON stream."
    preview_cache: dict[tuple[str, str, int], list[str]] = field(default_factory=dict)
    stream_lines: list[str] = field(default_factory=list)
    stream_top: int | None = None
    stream_reviewing: bool = False
    stream_command_label: str = "codex exec resume --json"
    stream_context_label: str = "resume"
    theme: TuiTheme = field(default_factory=TuiTheme)

    def run(self, stdscr: object) -> int:
        import curses

        self.stdscr = stdscr
        self.curses = curses
        curses.cbreak()
        curses.noecho()
        safe_curs_set(curses, 0)
        self.theme = build_curses_theme(curses)
        stdscr.keypad(True)
        while True:
            self.draw()
            key = stdscr.getch()
            if key in (ord("q"), 27):
                return 0
            if key in (9,):
                self.toggle_focus()
            elif key in (curses.KEY_UP, ord("k")):
                self.move_focused(-1)
            elif key in (curses.KEY_DOWN, ord("j")):
                self.move_focused(1)
            elif key in (curses.KEY_NPAGE,):
                self.page_focused(1)
            elif key in (curses.KEY_PPAGE,):
                self.page_focused(-1)
            elif key in (ord("v"),):
                self.set_mode("chat")
            elif key in (ord("f"),):
                self.set_mode("final")
            elif key in (ord("u"),):
                self.set_mode("user")
            elif key in (ord("a"),):
                self.set_mode("assistant")
            elif key in (ord("o"),):
                self.set_mode("files")
            elif key in (ord("r"),):
                self.refresh_threads()
            elif key in (ord("n"),):
                self.ask_new()
            elif key in (10, 13, curses.KEY_ENTER):
                self.ask_selected()

    def selected_thread(self) -> ThreadRow:
        return self.threads[self.selected]

    def move_selection(self, delta: int) -> None:
        if not self.threads:
            return
        previous = self.selected
        self.selected = clamp(self.selected + delta, 0, len(self.threads) - 1)
        if self.selected != previous:
            self.preview_top = 0
        self.status = f"Selected {short_id(self.selected_thread().id)}."

    def move_focused(self, delta: int) -> None:
        if self.focus == "preview":
            self.scroll_preview(delta)
            return
        self.move_selection(delta)

    def page_focused(self, direction: int) -> None:
        height, _width = self.stdscr.getmaxyx()
        page_height = dashboard_body_height(height)
        if self.focus == "preview":
            self.scroll_preview(direction * page_height)
            return
        self.move_selection(direction * visible_session_count(page_height))

    def scroll_preview(self, delta: int) -> None:
        self.preview_top = max(0, self.preview_top + delta)
        self.status = f"Preview scroll: line {self.preview_top + 1}."

    def toggle_focus(self) -> None:
        self.focus = "preview" if self.focus == "sessions" else "sessions"
        self.status = f"Focus: {self.focus}."

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.preview_top = 0
        self.status = f"Preview mode: {mode}."

    def refresh_threads(self) -> None:
        self.preview_cache.clear()
        if self.thread_loader is None:
            self.status = "Preview cache refreshed."
            return
        selected_id = self.selected_thread().id if self.threads else ""
        refreshed = self.thread_loader()
        if not refreshed:
            if not self.threads:
                self.selected = 0
                self.top = 0
                self.preview_top = 0
                self.status = "Refresh found no sessions. Press n to start a new Codex prompt."
                return
            self.status = "Refresh found no sessions; keeping current list."
            return
        self.threads = refreshed
        self.selected = selection_index_for_thread(self.threads, selected_id, self.selected)
        self.top = min(self.top, self.selected)
        self.preview_top = 0
        self.status = f"Refreshed {len(self.threads)} sessions."

    def draw(self) -> None:
        stdscr = self.stdscr
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        if height < 10 or width < 50:
            add_text(stdscr, 0, 0, "Terminal too small for CodexTUI. Press q to quit.", width)
            stdscr.refresh()
            return

        theme = self.theme
        add_text(stdscr, 0, 0, self.dashboard_header(width), width, theme.app_header)
        list_width = max(26, min(44, width // 3))
        preview_x = list_width + 2
        preview_width = max(1, width - preview_x)
        body_height = dashboard_body_height(height)
        preview_height = body_height
        self.keep_selected_visible(visible_session_count(body_height))
        sessions_scroll = self.session_scroll_label(body_height)
        preview_scroll = self.preview_scroll_label(preview_width, preview_height)

        sessions_attr = theme.pane_active if self.focus == "sessions" else theme.pane_inactive
        preview_attr = theme.pane_active if self.focus == "preview" else theme.pane_inactive
        add_text(stdscr, 1, 0, f"Sessions | {sessions_scroll}", list_width, sessions_attr)
        add_text(stdscr, 1, preview_x, preview_header(self.mode, preview_scroll, preview_width), preview_width, preview_attr)
        for y in range(1, height - 2):
            add_text(stdscr, y, list_width, "|", 2, theme.divider)

        self.draw_sessions(list_width, body_height)
        self.draw_preview(preview_x, 2, preview_width, preview_height)

        add_text(stdscr, height - 2, 0, footer_help(self.focus, has_threads=bool(self.threads), width=width), width, theme.footer)
        add_text(stdscr, height - 1, 0, self.status, width, status_line_attr(self.status, theme))
        stdscr.refresh()

    def dashboard_header(self, width: int) -> str:
        if not self.threads:
            return fit_header("CodexTUI | no sessions", width)
        thread = self.selected_thread()
        title = thread.title or thread.first_user_message or thread.preview or "(untitled)"
        label = f"CodexTUI | {self.selected + 1}/{len(self.threads)} {short_id(thread.id)} {title}"
        return fit_header(label, width)

    def keep_selected_visible(self, visible_count: int) -> None:
        visible_count = max(1, visible_count)
        if self.selected < self.top:
            self.top = self.selected
        elif self.selected >= self.top + visible_count:
            self.top = self.selected - visible_count + 1

    def session_scroll_label(self, height: int) -> str:
        visible_count = visible_session_count(height)
        return scroll_position_label(len(self.threads), visible_count, self.top)

    def draw_sessions(self, width: int, height: int) -> None:
        theme = self.theme
        if not self.threads:
            add_text(self.stdscr, 2, 0, "No sessions found.", width, theme.pane_inactive)
            add_text(self.stdscr, 3, 0, "Press n for new prompt.", width)
            add_text(self.stdscr, 4, 0, "Press r to refresh.", width)
            return
        end = min(len(self.threads), self.top + visible_session_count(height))
        row = 2
        end_y = 2 + max(0, height)
        for idx in range(self.top, end):
            thread = self.threads[idx]
            marker = ">" if idx == self.selected else " "
            title_line, metadata_line = session_row_lines(thread, marker, width)
            selected = idx == self.selected
            title_attr = theme.selection if selected else 0
            metadata_attr = theme.selection if selected else theme.status_muted
            add_text(self.stdscr, row, 0, title_line, width, title_attr)
            if row + 1 < end_y:
                add_text(self.stdscr, row + 1, 0, metadata_line, width, metadata_attr)
            row += SESSION_ROW_HEIGHT

    def draw_preview(self, x: int, y: int, width: int, height: int) -> None:
        lines = self.empty_preview_lines() if not self.threads else self.preview_lines(self.selected_thread(), width)
        visual_rows = styled_wrapped_lines(lines, width, self.theme)
        self.preview_top = clamped_scroll_top(len(visual_rows), height, self.preview_top)
        rows = visual_rows[self.preview_top : self.preview_top + max(0, height)]
        row = y
        for line, attr in rows:
            if row >= y + height:
                break
            add_text(self.stdscr, row, x, line, width, attr)
            row += 1

    def preview_scroll_label(self, width: int, height: int) -> str:
        lines = self.empty_preview_lines() if not self.threads else self.preview_lines(self.selected_thread(), width)
        wrapped = wrap_lines(lines, width)
        self.preview_top = clamped_scroll_top(len(wrapped), height, self.preview_top)
        return scroll_position_label(len(wrapped), height, self.preview_top)

    def preview_lines(self, thread: ThreadRow, width: int | None = None) -> list[str]:
        cache_width = max(0, width or 0)
        key = (thread.id, self.mode, cache_width)
        if key not in self.preview_cache:
            if self.mode == "files":
                text = render_file_hits(file_hits_for_thread(thread))
            else:
                text = render_thread(
                    thread,
                    mode=self.mode,
                    color=False,
                    width=width,
                    include_metadata=False,
                    header_style="compact",
                )
            self.preview_cache[key] = text.splitlines()
        return self.preview_cache[key]

    def empty_preview_lines(self) -> list[str]:
        return [
            "No Codex sessions found for the current filters.",
            "",
            "Press n to start a new Codex prompt through CodexTUI.",
            "Use ctui doctor if Codex is not installed or not logged in.",
            "Use r to refresh after Codex creates a session.",
        ]

    def ask_selected(self) -> None:
        if not self.threads:
            self.status = "No selected session. Press n to start a new Codex prompt."
            return
        prompt = self.read_prompt("Ask CodexTUI")
        if not prompt:
            self.status = "Ask cancelled."
            return
        thread = self.selected_thread()
        code = self.stream_prompt(
            context_label=stream_session_label(thread),
            command_label="codex exec resume --json",
            runner=lambda stdout: self.stream_runner(thread, prompt, stdout),
        )
        self.refresh_after_resume_stream(code)
        self.stdscr.clear()

    def ask_new(self) -> None:
        if self.new_stream_runner is None:
            self.status = "New prompt streaming is unavailable."
            return
        prompt = self.read_prompt("New Codex prompt")
        if not prompt:
            self.status = "New prompt cancelled."
            return
        code = self.stream_prompt(
            context_label="new prompt",
            command_label="codex exec --json",
            runner=lambda stdout: self.new_stream_runner(prompt, stdout),
        )
        self.refresh_after_new_stream(code)
        self.stdscr.clear()

    def stream_prompt(self, *, context_label: str, command_label: str, runner: Callable[[StreamOutput], int]) -> int:
        self.stream_top = None
        self.stream_reviewing = False
        self.stream_command_label = command_label
        self.stream_context_label = context_label
        self.stream_lines = []
        self.status = "Streaming response inside CodexTUI."
        self.draw_stream()
        writer = CursesStreamWriter(self)
        code = runner(writer)
        writer.close_line()
        self.status = "Stream finished." if code == 0 else f"Stream exited with status {code}."
        self.stream_reviewing = True
        self.stream_lines.extend(["", stream_completion_line(self.status)])
        self.draw_stream()
        self.review_stream()
        self.stream_reviewing = False
        return code

    def refresh_after_new_stream(self, code: int) -> None:
        self.preview_cache.clear()
        completion = stream_completion_summary(code)
        if self.thread_loader is None:
            return
        refreshed = self.thread_loader()
        if not refreshed:
            self.status = f"{completion}; refresh found no sessions."
            return
        self.threads = refreshed
        self.selected = 0
        self.top = 0
        self.preview_top = 0
        self.status = f"{completion}; refreshed {len(self.threads)} sessions."

    def refresh_after_resume_stream(self, code: int) -> None:
        self.preview_cache.clear()
        if self.thread_loader is None:
            return
        selected_id = self.selected_thread().id if self.threads else ""
        refreshed = self.thread_loader()
        completion = stream_completion_summary(code)
        if not refreshed:
            self.status = f"{completion}; refresh found no sessions; keeping current list."
            return
        self.threads = refreshed
        self.selected = selection_index_for_thread(self.threads, selected_id, self.selected)
        self.top = min(self.top, self.selected)
        self.preview_top = 0
        self.status = f"{completion}; refreshed {len(self.threads)} sessions."

    def append_stream_line(self, line: str) -> None:
        self.stream_lines.append(line.rstrip("\n"))

    def draw_stream(self, current_line: str | None = None) -> None:
        stdscr = self.stdscr
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        if height < 6 or width < 30:
            add_text(stdscr, 0, 0, "Streaming in CodexTUI. Please wait.", width)
            stdscr.refresh()
            return

        body_height = height - 3
        theme = self.theme
        stream_scroll = self.stream_scroll_label(current_line, width, body_height)
        add_text(stdscr, 0, 0, stream_header(self.stream_context_label, stream_scroll, width), width, theme.app_header)
        for row, (line, attr) in enumerate(self.visible_stream_rows(current_line, width, body_height), start=1):
            add_text(stdscr, row, 0, line, width, attr)
        add_text(
            stdscr,
            height - 2,
            0,
            stream_footer_help(self.stream_command_label, reviewing=self.stream_reviewing, width=width),
            width,
            theme.footer,
        )
        add_text(stdscr, height - 1, 0, self.status, width, status_line_attr(self.status, theme))
        stdscr.refresh()

    def visible_stream_lines(self, current_line: str | None, width: int, height: int) -> list[str]:
        return [line for line, _attr in self.visible_stream_rows(current_line, width, height)]

    def visible_stream_rows(self, current_line: str | None, width: int, height: int) -> list[tuple[str, int]]:
        if height <= 0:
            return []
        lines = list(self.stream_lines)
        if current_line is not None:
            lines.append(current_line)
        rows = styled_wrapped_lines(lines, width, self.theme)
        start = self.stream_start(len(rows), height)
        return rows[start : start + height]

    def stream_scroll_label(self, current_line: str | None, width: int, height: int) -> str:
        lines = list(self.stream_lines)
        if current_line is not None:
            lines.append(current_line)
        wrapped = wrap_lines(lines, width)
        start = self.stream_start(len(wrapped), height)
        return scroll_position_label(len(wrapped), height, start)

    def stream_start(self, total_lines: int, height: int) -> int:
        requested = total_lines if self.stream_top is None else self.stream_top
        return clamped_scroll_top(total_lines, height, requested)

    def scroll_stream_view(self, delta: int, width: int, height: int) -> None:
        wrapped = wrap_lines(self.stream_lines, width)
        current = self.stream_start(len(wrapped), height)
        self.stream_top = clamped_scroll_top(len(wrapped), height, current + delta)
        self.status = f"Stream scroll: line {self.stream_top + 1}."

    def scroll_stream(self, delta: int) -> None:
        height, width = self.stdscr.getmaxyx()
        self.scroll_stream_view(delta, width, stream_body_height(height))
        self.draw_stream()

    def scroll_stream_page(self, direction: int) -> None:
        height, width = self.stdscr.getmaxyx()
        body_height = stream_body_height(height)
        self.scroll_stream_view(direction * body_height, width, body_height)
        self.draw_stream()

    def review_stream(self) -> None:
        curses = self.curses
        while True:
            key = self.stdscr.getch()
            if key in (ord("q"), 27, 10, 13, curses.KEY_ENTER):
                self.stream_top = None
                return
            if key in (curses.KEY_UP, ord("k")):
                self.scroll_stream(-1)
            elif key in (curses.KEY_DOWN, ord("j")):
                self.scroll_stream(1)
            elif key in (curses.KEY_PPAGE,):
                self.scroll_stream_page(-1)
            elif key in (curses.KEY_NPAGE,):
                self.scroll_stream_page(1)

    def read_prompt(self, label: str) -> str:
        curses = self.curses
        height, width = self.stdscr.getmaxyx()
        prompt = prompt_entry_prefix(label, width)
        y = height - 1
        add_text(self.stdscr, y, 0, " " * max(0, width - 1), width)
        add_text(self.stdscr, y, 0, prompt, width, self.theme.footer)
        self.stdscr.refresh()
        curses.echo()
        safe_curs_set(curses, 1)
        try:
            raw = self.stdscr.getstr(
                y,
                min(len(prompt), max(0, width - 1)),
                prompt_input_limit(prompt, width),
            )
        finally:
            curses.noecho()
            safe_curs_set(curses, 0)
        return raw.decode("utf-8", errors="replace").strip()


def run_tui(
    *,
    include_archived: bool = False,
    limit: int | None = 80,
    query: str | None = None,
    source: str | None = None,
    cwd: str | None = None,
    raw_json: bool = False,
) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("ctui tui needs an interactive terminal.", file=sys.stderr)
        return 2
    store = CodexStore()

    def load_threads() -> list[ThreadRow]:
        return store.load_threads(
            include_archived=include_archived,
            limit=limit,
            query=query,
            source=source,
            cwd=cwd,
        )

    threads = load_threads()

    def runner(thread: ThreadRow, prompt: str, stdout: StreamOutput) -> int:
        return stream_selected_thread(thread, prompt, raw_json=raw_json, stdout=stdout)

    def new_runner(prompt: str, stdout: StreamOutput) -> int:
        return stream_new_prompt(prompt, raw_json=raw_json, stdout=stdout)

    status = (
        "Enter continues the selected session; n starts a new CodexTUI JSON stream."
        if threads
        else "No sessions found. Press n to start a new Codex prompt, or q to quit."
    )
    return run_curses_app(
        TuiApp(threads, runner, new_stream_runner=new_runner, thread_loader=load_threads, status=status)
    )


def run_curses_app(app: TuiApp) -> int:
    import curses

    result = curses.wrapper(app.run)
    return int(result or 0)


def stream_selected_thread(
    thread: ThreadRow,
    prompt: str,
    *,
    raw_json: bool = False,
    stdout: TextIO | None = None,
) -> int:
    command = codex_exec_command(real_codex_bin(), prompt=prompt, resume_id=thread.id)
    kwargs: dict[str, object] = {"raw_json": raw_json}
    if stdout is not None:
        kwargs["stdout"] = stdout
        kwargs["stderr_to_stdout"] = True
    return run_codex_json_stream(command, **kwargs)


def stream_new_prompt(
    prompt: str,
    *,
    raw_json: bool = False,
    stdout: TextIO | None = None,
) -> int:
    command = codex_exec_command(real_codex_bin(), prompt=prompt, resume_id=None)
    kwargs: dict[str, object] = {"raw_json": raw_json}
    if stdout is not None:
        kwargs["stdout"] = stdout
        kwargs["stderr_to_stdout"] = True
    return run_codex_json_stream(command, **kwargs)


class CursesStreamWriter:
    def __init__(self, app: TuiApp) -> None:
        self.app = app
        self.pending = ""

    def write(self, text: str) -> int:
        if not text:
            return 0
        parts = text.split("\n")
        self.pending += parts[0]
        for part in parts[1:]:
            self.app.append_stream_line(self.pending)
            self.pending = part
        self.app.draw_stream(self.pending or None)
        return len(text)

    def flush(self) -> None:
        self.app.draw_stream(self.pending or None)

    def close_line(self) -> None:
        if self.pending:
            self.app.append_stream_line(self.pending)
            self.pending = ""
            self.app.draw_stream()


def safe_curs_set(curses: object, visibility: int) -> None:
    try:
        curses.curs_set(visibility)
    except Exception:
        return


def add_text(window: object, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
    if width <= 0:
        return
    clean = text.replace("\t", "    ")
    try:
        window.addnstr(y, x, clean, max(0, width - 1), attr)
    except Exception:
        return


def preview_header(mode: str, scroll_label: str, width: int) -> str:
    full = f"Preview {preview_mode_tabs(mode)} | {scroll_label}"
    if fits_terminal_width(full, width):
        return full
    compact = f"Preview: {mode} | {scroll_label}"
    if fits_terminal_width(compact, width):
        return compact
    tight = f"{mode} | {scroll_label}"
    return fit_header(tight, width)


def stream_header(context_label: str, scroll_label: str, width: int) -> str:
    clean_context = " ".join(context_label.split()).strip()
    if clean_context:
        prefix = " CodexTUI Stream | "
        suffix = f" | {scroll_label} "
        context_width = max(0, width - 1 - len(prefix) - len(suffix))
        if context_width > 0:
            header = f"{prefix}{truncate(clean_context, context_width)}{suffix}"
            if fits_terminal_width(header, width):
                return header
            return fit_header(header, width)
    compact = f" CodexTUI Stream | {scroll_label} "
    if fits_terminal_width(compact, width):
        return compact
    return fit_header(f"Stream | {scroll_label}", width)


def stream_footer_help(command_label: str, *, reviewing: bool, width: int | None = None) -> str:
    short_label = stream_command_short_label(command_label)
    if reviewing:
        return fit_footer(
            [
                f"{command_label} | review: arrows/PgUp/PgDn scroll | enter/q return",
                f"{command_label} | review: scroll | enter/q return",
                f"{short_label} | review: arrows/PgUp/PgDn | enter/q",
                f"{short_label} | review scroll | enter/q",
                f"{short_label} | enter/q",
            ],
            width,
        )
    return fit_footer(
        [
            f"{command_label} | live: capturing output",
            f"{command_label} | live capture",
            f"{short_label} | live capture",
            f"{short_label} | live",
        ],
        width,
    )


def stream_command_short_label(command_label: str) -> str:
    if "resume" in command_label.split():
        return "resume"
    if command_label.startswith("codex exec"):
        return "exec"
    return command_label


def stream_completion_line(status: str) -> str:
    return f"[task] {status}"


def stream_completion_summary(code: int) -> str:
    return "Stream finished" if code == 0 else f"Stream exited with status {code}"


def stream_session_label(thread: ThreadRow) -> str:
    title = thread.title or thread.first_user_message or thread.preview
    suffix = f" {title}" if title else ""
    return f"resume {short_id(thread.id)}{suffix}"


def prompt_entry_prefix(label: str, width: int) -> str:
    usable_width = max(0, width - 1)
    if usable_width <= 2:
        return ""
    words = label.split()
    short_label = words[0] if words else "Prompt"
    variants = unique_labels([f"{label}: ", f"{short_label}: ", "> "])
    required_input = min(20, max(1, usable_width // 2))
    for variant in variants:
        if len(variant) < usable_width and prompt_input_limit(variant, width) >= required_input:
            return variant
    for variant in reversed(variants):
        if len(variant) < usable_width:
            return variant
    return ""


def prompt_input_limit(prefix: str, width: int) -> int:
    return max(1, max(0, width - 1) - len(prefix))


def unique_labels(labels: list[str]) -> list[str]:
    result: list[str] = []
    for label in labels:
        if label not in result:
            result.append(label)
    return result


def preview_mode_tabs(active_mode: str) -> str:
    return " ".join(f"[{label}]" if mode == active_mode else label for mode, label in PREVIEW_MODE_TABS)


def footer_help(focus: str, *, has_threads: bool, width: int | None = None) -> str:
    if not has_threads:
        return fit_footer(
            [
                "n new prompt | r refresh | q quit | ctui doctor for setup",
                "n new | r refresh | q quit | doctor",
                "n new | r refresh | q quit",
                "n/r/q",
            ],
            width,
        )
    if focus == "preview":
        return fit_footer(
            [
                "preview: arrows/PgUp/PgDn scroll | v chat | a assistant | f final | u user | o files | tab sessions | q quit",
                "preview: scroll | modes v/a/f/u/o | enter resume | n new | r refresh | tab | q",
                "scroll | modes v/a/f/u/o | enter resume | n new | r refresh | tab | q",
                "scroll | modes v/a/f/u/o | tab | q",
                "preview | scroll | tab | q",
            ],
            width,
        )
    return fit_footer(
        [
            "sessions: arrows select | enter resume | n new | r refresh | tab preview | q quit",
            "sessions: up/down | enter resume | n new | r refresh | tab preview | q quit",
            "up/down | enter resume | n new | tab | q",
            "sessions | enter | n | tab | q",
        ],
        width,
    )


def fit_footer(variants: list[str], width: int | None) -> str:
    if not variants:
        return ""
    if width is None:
        return variants[0]
    for variant in variants:
        if fits_terminal_width(variant, width):
            return variant
    return fit_header(variants[-1], width)


def fit_header(text: str, width: int) -> str:
    return truncate(text, max(0, width - 1))


def fits_terminal_width(text: str, width: int) -> bool:
    return len(text) <= max(0, width - 1)


def line_attr(line: str, theme: TuiTheme) -> int:
    stripped = line.strip()
    if is_role_header(stripped, "YOU"):
        return theme.user_header
    if is_role_header(stripped, "CODEX final"):
        return theme.assistant_final_header
    if is_role_header(stripped, "CODEX"):
        return theme.assistant_header
    if is_error_activity(stripped):
        return theme.status_error
    if stripped.startswith(("[tool]", "[tool output]", "[search]", "[plan]")):
        return theme.tool_header
    if stripped.startswith(("[task]", "[tokens]", "[context]", "[reasoning]", "[thread]", "[item]")):
        return theme.status_muted
    if is_code_fence(stripped):
        return theme.code
    return 0


def styled_lines(lines: list[str], theme: TuiTheme) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    in_code_block = False
    in_tool_output = False
    in_error_activity = False
    current_role_body = 0
    current_role_header = 0
    current_activity_body = 0
    current_activity_header = 0
    for line in lines:
        stripped = line.strip()
        fence = is_code_fence(stripped)
        detect_blocks = not in_code_block and not fence
        starts_tool_output = detect_blocks and stripped.startswith("[tool output]")
        starts_error_activity = detect_blocks and is_error_activity(stripped)
        starts_activity_detail = detect_blocks and has_activity_detail_body(stripped)
        role_body_attr = role_body_attr_for_header(stripped, theme) if detect_blocks else None
        starts_role_block = role_body_attr is not None
        starts_new_block = (detect_blocks and is_activity_header(stripped)) or starts_role_block
        markdown_attr = markdown_structure_attr(stripped, current_role_header or current_activity_header, theme)
        if starts_new_block and not starts_tool_output:
            in_tool_output = False
        if starts_new_block and not starts_error_activity:
            in_error_activity = False
        if starts_new_block and not starts_activity_detail:
            current_activity_body = 0
            current_activity_header = 0
        if starts_new_block:
            current_role_body = role_body_attr or 0
            current_role_header = line_attr(line, theme) if starts_role_block else 0
            markdown_attr = 0
        if in_code_block or fence:
            attr = theme.code
        elif starts_tool_output:
            attr = line_attr(line, theme)
            in_tool_output = True
            in_error_activity = False
            current_role_body = 0
        elif starts_error_activity:
            attr = line_attr(line, theme)
            in_error_activity = True
            current_role_body = 0
            current_activity_body = 0
            current_activity_header = 0
        elif starts_activity_detail:
            attr = line_attr(line, theme)
            current_activity_body = theme.status_muted
            current_activity_header = attr
            current_role_body = 0
            current_role_header = 0
        elif starts_role_block:
            attr = line_attr(line, theme)
        elif in_tool_output:
            attr = theme.code
        elif in_error_activity:
            attr = theme.status_error
        elif markdown_attr:
            attr = markdown_attr
        elif current_activity_body:
            attr = current_activity_body
        elif current_role_body:
            attr = current_role_body
        else:
            attr = line_attr(line, theme)
        result.append((line, attr))
        if fence:
            in_code_block = not in_code_block
    return result


def is_code_fence(line: str) -> bool:
    return line.startswith(("```", "~~~"))


def markdown_structure_attr(line: str, current_role_header: int, theme: TuiTheme) -> int:
    if not current_role_header:
        return 0
    if is_markdown_heading(line):
        return current_role_header
    if is_markdown_quote(line):
        return theme.status_muted
    if is_markdown_table_separator(line):
        return theme.divider
    if is_markdown_table_row(line):
        return theme.code
    if is_markdown_rule(line):
        return theme.divider
    return 0


def is_markdown_heading(line: str) -> bool:
    marker = line.split(" ", 1)[0]
    return 1 <= len(marker) <= 6 and set(marker) == {"#"} and line.startswith(f"{marker} ")


def is_markdown_quote(line: str) -> bool:
    return line == ">" or line.startswith("> ")


def is_markdown_rule(line: str) -> bool:
    if len(line) < 3:
        return False
    return set(line) <= {"-"} or set(line) <= {"*"} or set(line) <= {"_"}


def is_markdown_table_row(line: str) -> bool:
    return line.startswith("|") and line.endswith("|") and line.count("|") >= 3


def is_markdown_table_separator(line: str) -> bool:
    if not is_markdown_table_row(line):
        return False
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(is_markdown_table_separator_cell(cell) for cell in cells)


def is_markdown_table_separator_cell(cell: str) -> bool:
    marker = cell.strip().strip(":")
    return len(marker) >= 3 and set(marker) == {"-"}


def is_activity_header(line: str) -> bool:
    return line.startswith(
        (
            "[tool]",
            "[tool output]",
            "[search]",
            "[plan]",
            "[task]",
            "[tokens]",
            "[context]",
            "[reasoning]",
            "[thread]",
            "[item]",
        )
    )


def has_activity_detail_body(line: str) -> bool:
    return line.startswith(("[plan]", "[item]"))


def role_body_attr_for_header(line: str, theme: TuiTheme) -> int | None:
    if is_role_header(line, "YOU"):
        return theme.user_body
    if is_role_header(line, "CODEX final"):
        return theme.assistant_final_body
    if is_role_header(line, "CODEX"):
        return theme.assistant_body
    return None


def status_line_attr(status: str, theme: TuiTheme) -> int:
    lowered = status.casefold()
    if any(marker in lowered for marker in ("failed", "exited with status", "unable", "unavailable")):
        return theme.status_error
    return theme.status_muted


def is_role_header(line: str, role: str) -> bool:
    if line.startswith("[") and line.endswith(f"  {role}"):
        return True
    if line == role:
        return True
    prefix = f"{role} "
    if not line.startswith(prefix):
        return False
    return looks_like_compact_time(line[len(prefix) :])


def looks_like_compact_time(value: str) -> bool:
    return len(value) == 5 and value[:2].isdigit() and value[2] == ":" and value[3:].isdigit()


def is_error_activity(line: str) -> bool:
    if not line.startswith(("[task]", "[tool]", "[tool output]", "[tokens]", "[item]")):
        return False
    lowered = line.casefold()
    return any(marker in lowered for marker in ("failed", "error", "exited with status", "aborted", "limit reached"))


def wrap_lines(lines: list[str], width: int) -> list[str]:
    result: list[str] = []
    for line in lines:
        result.extend(wrap_line(line, width))
    return result


def styled_wrapped_lines(lines: list[str], width: int, theme: TuiTheme) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for line, attr in styled_lines(lines, theme):
        result.extend((wrapped, attr) for wrapped in wrap_line(line, width))
    return result


def wrap_line(line: str, width: int) -> list[str]:
    wrap_width = max(1, width - 1)
    if not line:
        return [""]
    wrapped = textwrap.wrap(
        line,
        width=wrap_width,
        replace_whitespace=False,
        drop_whitespace=False,
        break_long_words=True,
        break_on_hyphens=False,
    )
    return wrapped or [""]


def visible_lines(lines: list[str], width: int, height: int, top: int) -> list[str]:
    if height <= 0:
        return []
    wrapped = wrap_lines(lines, width)
    start = clamped_scroll_top(len(wrapped), height, top)
    return wrapped[start : start + height]


def scroll_position_label(total_lines: int, height: int, top: int) -> str:
    if total_lines <= 0:
        return "empty"
    if height <= 0:
        return f"line {min(max(0, top) + 1, total_lines)}/{total_lines}"
    start = clamped_scroll_top(total_lines, height, top)
    end = min(total_lines, start + height)
    if total_lines <= height:
        return f"all {total_lines}"
    return f"{start + 1}-{end}/{total_lines}"


def visible_session_count(height: int) -> int:
    return max(1, height // SESSION_ROW_HEIGHT)


def dashboard_body_height(height: int) -> int:
    return max(1, height - 4)


def stream_body_height(height: int) -> int:
    return max(1, height - 3)


def session_row_lines(thread: ThreadRow, marker: str, width: int) -> tuple[str, str]:
    title = thread.title or thread.first_user_message or thread.preview or "(untitled)"
    title_line = prefixed_session_line(f"{marker} ", title, width)
    metadata_line = prefixed_session_line("  ", session_metadata(thread, width), width)
    return title_line, metadata_line


def prefixed_session_line(prefix: str, text: str, width: int) -> str:
    usable_width = max(1, width - 1)
    if usable_width <= len(prefix):
        return prefix[:usable_width]
    return f"{prefix}{truncate(text, usable_width - len(prefix))}"


def session_metadata(thread: ThreadRow, width: int) -> str:
    text_width = max(1, width - 1 - len("  "))
    core = [short_id(thread.id), thread.source or "?"]
    cwd = clean_session_cwd(thread.cwd)
    short_cwd = basename_label(cwd)
    updated = compact_session_time(thread.recency_at_ms)
    if thread.archived:
        candidates = [
            core + ["archived", cwd, updated],
            core + ["archived", short_cwd, updated],
            core + ["archived", cwd],
            core + ["archived", short_cwd],
            core + ["archived"],
            core,
        ]
    else:
        candidates = [
            core + [cwd, updated],
            core + [short_cwd, updated],
            core + [cwd],
            core + [short_cwd],
            core + [updated],
            core,
        ]
    for parts in candidates:
        label = " ".join(part for part in parts if part)
        if len(label) <= text_width:
            return label
    return " ".join(core)


def clean_session_cwd(cwd: str) -> str:
    clean = " ".join((cwd or "?").split()).strip()
    return clean or "?"


def basename_label(path: str) -> str:
    clean = path.rstrip("/\\")
    if clean in {"", "?"}:
        return clean or "?"
    return clean.replace("\\", "/").rsplit("/", 1)[-1] or clean


def compact_session_time(value: int) -> str:
    formatted = format_ms(value)
    if len(formatted) >= 16 and formatted[4] == "-" and formatted[7] == "-":
        return formatted[5:16]
    return formatted


def clamped_scroll_top(total_lines: int, height: int, requested: int) -> int:
    max_top = max(0, total_lines - max(1, height))
    return clamp(requested, 0, max_top)


def selection_index_for_thread(threads: list[ThreadRow], selected_id: str, fallback: int) -> int:
    for idx, thread in enumerate(threads):
        if thread.id == selected_id:
            return idx
    return clamp(fallback, 0, len(threads) - 1)


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))

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
from .transcript import render_thread, short_id, truncate


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
    status: str = "Enter continues the selected session; n starts a new CodexPlus JSON stream."
    preview_cache: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    stream_lines: list[str] = field(default_factory=list)
    stream_top: int | None = None
    stream_command_label: str = "codex exec resume --json"

    def run(self, stdscr: object) -> int:
        import curses

        self.stdscr = stdscr
        self.curses = curses
        curses.cbreak()
        curses.noecho()
        safe_curs_set(curses, 0)
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
                self.move_focused(10)
            elif key in (curses.KEY_PPAGE,):
                self.move_focused(-10)
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
            self.status = "Refresh found no sessions; keeping current list."
            return
        self.threads = refreshed
        self.selected = selection_index_for_thread(self.threads, selected_id, self.selected)
        self.top = min(self.top, self.selected)
        self.preview_top = 0
        self.status = f"Refreshed {len(self.threads)} sessions."

    def draw(self) -> None:
        curses = self.curses
        stdscr = self.stdscr
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        if height < 10 or width < 50:
            add_text(stdscr, 0, 0, "Terminal too small for CodexPlus TUI. Press q to quit.", width)
            stdscr.refresh()
            return

        add_text(stdscr, 0, 0, " CodexPlus TUI ", width, curses.A_REVERSE)
        list_width = max(26, min(44, width // 3))
        preview_x = list_width + 2
        preview_width = max(1, width - preview_x)
        body_height = height - 4
        self.keep_selected_visible(body_height - 2)

        sessions_attr = curses.A_REVERSE if self.focus == "sessions" else curses.A_BOLD
        preview_attr = curses.A_REVERSE if self.focus == "preview" else curses.A_BOLD
        add_text(stdscr, 1, 0, "Sessions", list_width, sessions_attr)
        add_text(stdscr, 1, preview_x, f"Preview: {self.mode}", preview_width, preview_attr)
        for y in range(1, height - 2):
            add_text(stdscr, y, list_width, "|", 1)

        self.draw_sessions(list_width, body_height)
        self.draw_preview(preview_x, 2, preview_width, body_height - 1)

        help_text = "tab focus | arrows move/scroll | enter resume | n new | r refresh | v/a/f/u/o modes | q quit"
        add_text(stdscr, height - 2, 0, help_text, width, curses.A_REVERSE)
        add_text(stdscr, height - 1, 0, self.status, width)
        stdscr.refresh()

    def keep_selected_visible(self, visible_count: int) -> None:
        visible_count = max(1, visible_count)
        if self.selected < self.top:
            self.top = self.selected
        elif self.selected >= self.top + visible_count:
            self.top = self.selected - visible_count + 1

    def draw_sessions(self, width: int, height: int) -> None:
        curses = self.curses
        end = min(len(self.threads), self.top + max(1, height - 1))
        for row, idx in enumerate(range(self.top, end), start=2):
            thread = self.threads[idx]
            marker = ">" if idx == self.selected else " "
            title = truncate(thread.title or thread.first_user_message or thread.preview or "(untitled)", width - 12)
            line = f"{marker} {short_id(thread.id)} {title}"
            attr = curses.A_REVERSE if idx == self.selected else 0
            add_text(self.stdscr, row, 0, line, width, attr)

    def draw_preview(self, x: int, y: int, width: int, height: int) -> None:
        wrapped = wrap_lines(self.preview_lines(self.selected_thread()), width)
        self.preview_top = clamped_scroll_top(len(wrapped), height, self.preview_top)
        lines = wrapped[self.preview_top : self.preview_top + max(0, height)]
        row = y
        for line in lines:
            if row >= y + height:
                break
            add_text(self.stdscr, row, x, line, width)
            row += 1

    def preview_lines(self, thread: ThreadRow) -> list[str]:
        key = (thread.id, self.mode)
        if key not in self.preview_cache:
            if self.mode == "files":
                text = render_file_hits(file_hits_for_thread(thread))
            else:
                text = render_thread(thread, mode=self.mode, color=False)
            self.preview_cache[key] = text.splitlines()
        return self.preview_cache[key]

    def ask_selected(self) -> None:
        prompt = self.read_prompt("Ask CodexPlus")
        if not prompt:
            self.status = "Ask cancelled."
            return
        thread = self.selected_thread()
        self.stream_prompt(
            title=f"CodexPlus streaming {short_id(thread.id)} via codex exec resume --json",
            command_label="codex exec resume --json",
            runner=lambda stdout: self.stream_runner(thread, prompt, stdout),
        )
        self.preview_cache.clear()
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
            title="CodexPlus streaming a new prompt via codex exec --json",
            command_label="codex exec --json",
            runner=lambda stdout: self.new_stream_runner(prompt, stdout),
        )
        self.refresh_after_new_stream(code)
        self.stdscr.clear()

    def stream_prompt(self, *, title: str, command_label: str, runner: Callable[[StreamOutput], int]) -> int:
        self.stream_top = None
        self.stream_command_label = command_label
        self.stream_lines = [title, ""]
        self.status = "Streaming response inside CodexPlus TUI."
        self.draw_stream()
        writer = CursesStreamWriter(self)
        code = runner(writer)
        writer.close_line()
        self.status = "Stream finished." if code == 0 else f"Stream exited with status {code}."
        self.stream_lines.extend(["", f"{self.status} Arrows/PageUp/PageDown scroll, Enter/q returns."])
        self.draw_stream()
        self.review_stream()
        return code

    def refresh_after_new_stream(self, code: int) -> None:
        self.preview_cache.clear()
        completion = "Stream finished" if code == 0 else f"Stream exited with status {code}"
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

    def append_stream_line(self, line: str) -> None:
        self.stream_lines.append(line.rstrip("\n"))

    def draw_stream(self, current_line: str | None = None) -> None:
        curses = self.curses
        stdscr = self.stdscr
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        if height < 6 or width < 30:
            add_text(stdscr, 0, 0, "Streaming in CodexPlus TUI. Please wait.", width)
            stdscr.refresh()
            return

        add_text(stdscr, 0, 0, " CodexPlus Stream ", width, curses.A_REVERSE)
        body_height = height - 3
        for row, line in enumerate(self.visible_stream_lines(current_line, width, body_height), start=1):
            add_text(stdscr, row, 0, line, width)
        add_text(
            stdscr,
            height - 2,
            0,
            f"{self.stream_command_label} | output captured by CodexPlus | scroll after finish",
            width,
            curses.A_REVERSE,
        )
        add_text(stdscr, height - 1, 0, self.status, width)
        stdscr.refresh()

    def visible_stream_lines(self, current_line: str | None, width: int, height: int) -> list[str]:
        if height <= 0:
            return []
        lines = list(self.stream_lines)
        if current_line is not None:
            lines.append(current_line)
        wrapped = wrap_lines(lines, width)
        start = self.stream_start(len(wrapped), height)
        return wrapped[start : start + height]

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
        self.scroll_stream_view(delta, width, max(1, height - 3))
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
                self.scroll_stream(-10)
            elif key in (curses.KEY_NPAGE,):
                self.scroll_stream(10)

    def read_prompt(self, label: str) -> str:
        curses = self.curses
        height, width = self.stdscr.getmaxyx()
        prompt = f"{label}: "
        y = height - 1
        add_text(self.stdscr, y, 0, " " * max(0, width - 1), width)
        add_text(self.stdscr, y, 0, prompt, width)
        self.stdscr.refresh()
        curses.echo()
        safe_curs_set(curses, 1)
        try:
            raw = self.stdscr.getstr(y, min(len(prompt), width - 1), max(1, width - len(prompt) - 1))
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
        print("cxp tui needs an interactive terminal.", file=sys.stderr)
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
    if not threads:
        print("No sessions found.")
        return 0

    def runner(thread: ThreadRow, prompt: str, stdout: StreamOutput) -> int:
        return stream_selected_thread(thread, prompt, raw_json=raw_json, stdout=stdout)

    def new_runner(prompt: str, stdout: StreamOutput) -> int:
        return stream_new_prompt(prompt, raw_json=raw_json, stdout=stdout)

    return run_curses_app(TuiApp(threads, runner, new_stream_runner=new_runner, thread_loader=load_threads))


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


def wrap_lines(lines: list[str], width: int) -> list[str]:
    result: list[str] = []
    wrap_width = max(1, width - 1)
    for line in lines:
        if not line:
            result.append("")
            continue
        wrapped = textwrap.wrap(
            line,
            width=wrap_width,
            replace_whitespace=False,
            drop_whitespace=False,
            break_long_words=True,
            break_on_hyphens=False,
        )
        result.extend(wrapped or [""])
    return result


def visible_lines(lines: list[str], width: int, height: int, top: int) -> list[str]:
    if height <= 0:
        return []
    wrapped = wrap_lines(lines, width)
    start = clamped_scroll_top(len(wrapped), height, top)
    return wrapped[start : start + height]


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

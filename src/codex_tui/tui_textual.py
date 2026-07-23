from __future__ import annotations

import os
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TextIO
from urllib.parse import unquote, urlparse

from .codex_stream import (
    codex_exec_command,
    file_change_detail_from_stream_record,
    run_codex_json_stream,
    tool_output_detail_from_stream_record,
)
from .models import ChatMessage, ThreadRow
from .paths import real_codex_bin
from .store import CodexStore
from .transcript import (
    compact_timestamp,
    format_ms,
    one_line,
    pretty_json_text,
    read_messages,
    role_label,
    short_id,
    truncate,
)
from .transcript_blocks import (
    FileChange,
    SessionInfo,
    TranscriptBlock,
    changed_paths_summary,
    context_text,
    default_session_info,
    patch_paths,
    session_footer_text,
    session_info_for_thread,
    session_info_from_record,
    transcript_blocks_for_thread,
)


try:
    from rich.console import Group
    from rich.markdown import Markdown as RichMarkdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.text import Text
    from textual.app import App, ComposeResult, ScreenStackError
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.events import Key, Paste
    from textual.timer import Timer
    from textual.widgets import Footer, Header, Input, ListItem, ListView, Static
except ImportError as exc:  # pragma: no cover - exercised through run_textual_tui fallback.
    TEXTUAL_IMPORT_ERROR: ImportError | None = exc
else:
    TEXTUAL_IMPORT_ERROR = None


HISTORY_MODES = ("conversations", "runs", "all")
COMPACT_LAYOUT_MAX_WIDTH = 70
MIN_HISTORY_ROW_WIDTH = 20
MAX_HISTORY_ROW_WIDTH = 72
TRANSCRIPT_SCROLL_KEYS = {"up", "down", "k", "j", "pageup", "pagedown", "home", "end", "G"}
TRANSCRIPT_INNER_SCROLL_KEYS = {"ctrl+j", "ctrl+k", "alt+j", "alt+k"}
TRANSCRIPT_SCROLL_STEP_LINES = 6
SEARCH_DEBOUNCE_SECONDS = 0.18
IMAGE_ATTACHMENT_PREFIXES = {"/image", "/img"}
IMAGE_ATTACHMENT_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".heic",
}
CLIPBOARD_IMAGE_SUFFIX = ".png"


@dataclass(frozen=True)
class HistoryEntry:
    kind: str
    title: str
    subtitle: str
    thread: ThreadRow
    threads: tuple[ThreadRow, ...]
    search_text: str

    @property
    def is_group(self) -> bool:
        return len(self.threads) > 1


@dataclass(frozen=True)
class ComposerPayload:
    prompt: str
    image_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComposerToken:
    value: str
    start: int
    end: int


ThreadLoader = Callable[[], list[ThreadRow]]


def run_textual_tui(
    *,
    include_archived: bool = False,
    limit: int | None = 80,
    query: str | None = None,
    source: str | None = None,
    cwd: str | None = None,
    raw_json: bool = False,
) -> int:
    if TEXTUAL_IMPORT_ERROR is not None:
        print(
            "ctui tui needs the Textual/Rich dependencies. "
            "Install the project with dependencies, or run `.venv/bin/python -m codex_tui tui` in this checkout.",
            file=sys.stderr,
        )
        return 2
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

    app = CodexTextualApp(load_threads, raw_json=raw_json)
    result = app.run()
    if isinstance(result, tuple) and len(result) == 2 and result[0] == "resume":
        exec_official_resume(str(result[1]))
    return int(result or 0) if isinstance(result, int) else 0


def build_history_entries(threads: Iterable[ThreadRow], *, mode: str, query: str = "") -> list[HistoryEntry]:
    thread_list = list(threads)
    conversations: list[HistoryEntry] = []
    runs_by_title: dict[str, list[ThreadRow]] = {}
    individual_runs: list[ThreadRow] = []
    include_transcripts = bool(query.strip())
    for thread in thread_list:
        if is_conversation_thread(thread):
            conversations.append(entry_for_thread(thread, kind="conversation", include_transcripts=include_transcripts))
        else:
            key = normalized_run_title(thread)
            if key:
                runs_by_title.setdefault(key, []).append(thread)
            else:
                individual_runs.append(thread)
    runs = [entry_for_run_group(title, group, include_transcripts=include_transcripts) for title, group in runs_by_title.items()]
    runs.extend(entry_for_thread(thread, kind="run", include_transcripts=include_transcripts) for thread in individual_runs)
    runs.sort(key=lambda entry: entry.thread.recency_at_ms, reverse=True)
    conversations.sort(key=lambda entry: entry.thread.recency_at_ms, reverse=True)
    if mode == "runs":
        entries = runs
    elif mode == "all":
        entries = sorted([*conversations, *runs], key=lambda entry: entry.thread.recency_at_ms, reverse=True)
    else:
        entries = conversations
    if query.strip():
        entries = [entry for entry in entries if entry_matches_query(entry, query)]
    return entries


def is_conversation_thread(thread: ThreadRow) -> bool:
    if thread.source in {"cli", "vscode"}:
        return True
    messages = safe_read_messages(thread)
    user_turns = sum(1 for message in messages if message.role == "user")
    assistant_turns = sum(1 for message in messages if message.role == "assistant")
    if user_turns >= 2:
        return True
    if assistant_turns >= 2 and user_turns >= 1:
        return True
    if user_turns >= 1 and assistant_turns >= 1:
        return any(is_human_readable_assistant_reply(message.text) for message in messages if message.role == "assistant")
    return False


def is_human_readable_assistant_reply(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped in {"{}", "[]"}:
        return False
    if stripped.startswith("{"):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            return True
        return not (
            isinstance(value, dict)
            and {"success", "summary", "key_changes_made", "key_learnings"}.issubset(value)
        )
    return True


def normalized_run_title(thread: ThreadRow) -> str:
    title = display_title(thread).strip()
    if not title:
        return ""
    return " ".join(title.casefold().split())


def entry_for_thread(thread: ThreadRow, *, kind: str, include_transcripts: bool = False) -> HistoryEntry:
    title = display_title(thread)
    subtitle = thread_subtitle(thread)
    search_text = entry_search_text([thread], title, subtitle, include_transcripts=include_transcripts)
    return HistoryEntry(
        kind=kind,
        title=title,
        subtitle=subtitle,
        thread=thread,
        threads=(thread,),
        search_text=search_text,
    )


def entry_for_run_group(title_key: str, threads: list[ThreadRow], include_transcripts: bool = False) -> HistoryEntry:
    ordered = sorted(threads, key=lambda thread: thread.recency_at_ms, reverse=True)
    representative = ordered[0]
    title = display_title(representative)
    suffix = "run" if len(ordered) == 1 else "runs"
    subtitle = f"{len(ordered)} {suffix} | latest {short_id(representative.id)} | {project_label(representative.cwd)}"
    search_text = entry_search_text(ordered, title_key, subtitle, include_transcripts=include_transcripts)
    return HistoryEntry(
        kind="run_group" if len(ordered) > 1 else "run",
        title=title,
        subtitle=subtitle,
        thread=representative,
        threads=tuple(ordered),
        search_text=search_text,
    )


def display_title(thread: ThreadRow) -> str:
    return one_line(thread.title or thread.first_user_message or thread.preview or "(untitled)")


def thread_subtitle(thread: ThreadRow) -> str:
    parts = [short_id(thread.id), thread.source or "?", format_ms(thread.recency_at_ms), project_label(thread.cwd)]
    return " | ".join(part for part in parts if part)


def project_label(cwd: str) -> str:
    if not cwd:
        return ""
    return Path(cwd).name or cwd


def entry_search_text(threads: Iterable[ThreadRow], *extra: str, include_transcripts: bool = False) -> str:
    pieces = list(extra)
    for thread in threads:
        pieces.extend([thread.id, thread.title, thread.preview, thread.first_user_message, thread.cwd, thread.source])
        if include_transcripts:
            for message in safe_read_messages(thread):
                pieces.append(message.text)
    return "\n".join(piece for piece in pieces if piece).casefold()


def entry_matches_query(entry: HistoryEntry, query: str) -> bool:
    words = [word.casefold() for word in query.split() if word.strip()]
    if not words:
        return True
    return all(word in entry.search_text for word in words)


def safe_read_messages(thread: ThreadRow) -> list[ChatMessage]:
    try:
        return read_messages(Path(thread.rollout_path))
    except OSError:
        return []


def parse_composer_payload(value: str, *, cwd: Path | None = None) -> ComposerPayload:
    text = value.strip()
    if not text:
        return ComposerPayload("")
    try:
        tokens = composer_tokens(text)
    except ValueError:
        return ComposerPayload(text)
    root = cwd or Path.cwd()
    image_paths: list[str] = []
    attachment_ranges: list[tuple[int, int]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.value in IMAGE_ATTACHMENT_PREFIXES and index + 1 < len(tokens):
            next_token = tokens[index + 1]
            if not looks_like_image_path(path_from_pasted_value(next_token.value)):
                index += 1
                continue
            image_paths.append(normalized_attachment_path(next_token.value, root))
            attachment_ranges.append((token.start, next_token.end))
            index += 2
            continue
        if token.value.startswith("@") and looks_like_image_path(token.value[1:]):
            image_paths.append(normalized_attachment_path(token.value[1:], root))
            attachment_ranges.append((token.start, token.end))
            index += 1
            continue
        index += 1
    prompt = text if not attachment_ranges else remove_composer_token_ranges(text, attachment_ranges)
    return ComposerPayload(prompt, tuple(image_paths))


def composer_tokens(text: str) -> list[ComposerToken]:
    tokens: list[ComposerToken] = []
    index = 0
    length = len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        start = index
        value: list[str] = []
        quote = ""
        while index < length:
            char = text[index]
            if quote:
                if char == quote:
                    quote = ""
                    index += 1
                    continue
                if quote == '"' and char == "\\" and index + 1 < length:
                    index += 1
                    value.append(text[index])
                    index += 1
                    continue
                value.append(char)
                index += 1
                continue
            if char.isspace():
                break
            if char in {"'", '"'}:
                quote = char
                index += 1
                continue
            if char == "\\" and index + 1 < length:
                index += 1
                value.append(text[index])
                index += 1
                continue
            value.append(char)
            index += 1
        if quote:
            raise ValueError("No closing quotation")
        tokens.append(ComposerToken("".join(value), start, index))
    return tokens


def remove_composer_token_ranges(text: str, ranges: Iterable[tuple[int, int]]) -> str:
    result = text
    for start, end in sorted(ranges, reverse=True):
        result = result[:start] + result[end:]
    return " ".join(result.strip().split())


def looks_like_image_path(value: str) -> bool:
    return Path(value).suffix.casefold() in IMAGE_ATTACHMENT_EXTENSIONS


def normalized_attachment_path(value: str, cwd: Path) -> str:
    path = Path(path_from_pasted_value(value)).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return str(path.resolve(strict=False))


def first_missing_attachment(image_paths: Iterable[str]) -> str | None:
    for image_path in image_paths:
        if not Path(image_path).is_file():
            return image_path
    return None


def capture_clipboard_image() -> tuple[str | None, str | None]:
    pngpaste = shutil.which("pngpaste")
    if pngpaste is None:
        return None, "Clipboard image paste needs pngpaste. Install it with `brew install pngpaste`."
    temp = tempfile.NamedTemporaryFile(prefix="codextui-clipboard-", suffix=CLIPBOARD_IMAGE_SUFFIX, delete=False)
    image_path = Path(temp.name)
    temp.close()
    result = subprocess.run(
        [pngpaste, str(image_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0 and image_path.is_file() and image_path.stat().st_size > 0:
        return str(image_path), None
    image_path.unlink(missing_ok=True)
    detail = (result.stderr or result.stdout).strip()
    suffix = f": {detail}" if detail else "."
    return None, f"No image found in clipboard{suffix}"


def image_paths_from_paste_text(text: str, *, cwd: Path | None = None) -> tuple[str, ...]:
    stripped = text.strip()
    if not stripped:
        return ()
    root = cwd or Path.cwd()
    line_candidates = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(line_candidates) > 1:
        line_paths = existing_image_paths(line_candidates, root)
        if line_paths:
            return line_paths
    whole_path = existing_image_path(stripped, root)
    if whole_path:
        return (whole_path,)
    try:
        token_candidates = shlex.split(stripped)
    except ValueError:
        return ()
    if len(token_candidates) <= 1:
        return ()
    return existing_image_paths(token_candidates, root)


def existing_image_paths(candidates: Iterable[str], cwd: Path) -> tuple[str, ...]:
    paths: list[str] = []
    for candidate in candidates:
        image_path = existing_image_path(candidate, cwd)
        if image_path is None:
            return ()
        paths.append(image_path)
    return tuple(paths)


def existing_image_path(value: str, cwd: Path) -> str | None:
    candidate = path_from_pasted_value(value)
    if not looks_like_image_path(candidate):
        return None
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = cwd / path
    resolved = path.resolve(strict=False)
    return str(resolved) if resolved.is_file() else None


def path_from_pasted_value(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme == "file":
        return unquote(parsed.path)
    return value


def composer_display_text(payload: ComposerPayload) -> str:
    if not payload.image_paths:
        return payload.prompt
    suffix = "\n".join(
        f"[Image {index}] {Path(image_path).name}"
        for index, image_path in enumerate(payload.image_paths, start=1)
    )
    return f"{payload.prompt}\n\n{suffix}"


def conversation_title(thread: ThreadRow, *, width: int | None = None) -> str:
    title = f"{truncate(display_title(thread), 96)}  [{short_id(thread.id)}]"
    return truncate(title, width) if width is not None else title


def status_line_text(status_text: str, info: SessionInfo, *, width: int | None = None) -> str:
    status = one_line(status_text) or "Ready"
    full_footer = session_footer_text(info)
    full = f"{status} | {full_footer}"
    if width is None or len(full) <= width:
        return full
    compact_footer = compact_session_footer_text(info)
    compact = f"{status} | {compact_footer}"
    if len(compact) <= width:
        return compact
    separator_width = 3
    if compact_footer and width > len(compact_footer) + separator_width:
        status_width = width - len(compact_footer) - separator_width
        return f"{truncate(status, status_width)} | {compact_footer}"
    return truncate(status, width)


def compact_session_footer_text(info: SessionInfo) -> str:
    model = truncate(info.model or "?", 18)
    context = context_text(info.context_tokens, info.context_window)
    return f"{model} | {context}"


def composer_help_text(
    *,
    history_visible: bool,
    pending_image_count: int = 0,
    width: int | None = None,
) -> str:
    history_hint = "b hide list" if history_visible else "b show list"
    compact_history_hint = "b hide" if history_visible else "b show"
    image_hint = image_attachment_help_text(pending_image_count)
    variants = [
        " | ".join(
            [
                "n new",
                "j/k blocks",
                "Enter/t expand",
                "i/c chat",
                image_hint,
                history_hint,
                "Ctrl+j/k scroll",
                "gg/G",
                "R resume",
            ]
        ),
    ]
    if pending_image_count:
        if history_visible:
            variants.extend(
                [
                    " | ".join(["n new", "j/k", "Enter/t", "i/c", image_hint, compact_history_hint, "R resume"]),
                    " | ".join(["n", "Enter/t", image_hint, compact_history_hint, "R"]),
                ]
            )
        else:
            variants.extend(
                [
                    " | ".join(["n new", "j/k", "Enter/t", "i/c", image_hint, history_hint, "R resume"]),
                    " | ".join(["n", "Enter/t", image_hint, history_hint, "R"]),
                ]
            )
    else:
        if history_visible:
            variants.extend(
                [
                    " | ".join(["n new", "j/k", "Enter/t", "i/c chat", history_hint, "R resume"]),
                    " | ".join(["n new", "j/k", "Enter/t", "i/c", compact_history_hint, "R resume"]),
                    " | ".join(["n", "j/k", "Enter/t", "i/c", compact_history_hint, "R"]),
                ]
            )
        else:
            variants.extend(
                [
                    " | ".join(["n new", "j/k", "Enter/t", "i/c chat", history_hint, "R resume"]),
                    " | ".join(["n new", "j/k", "Enter/t", "i/c", history_hint, "R"]),
                    " | ".join(["n", "j/k", "Enter/t", "i/c", compact_history_hint, "R"]),
                ]
            )
    if width is None:
        return variants[0]
    for variant in variants:
        if len(variant) <= width:
            return variant
    return truncate(variants[-1], width)


def mode_line_text(mode: str, count: int, *, width: int | None = None) -> str:
    mode_label = {"conversations": "conv", "runs": "runs", "all": "all"}.get(mode, mode)
    variants = [
        f"{mode} | {count} shown | / search | g mode",
        f"{mode_label} | {count} shown | / search | g",
        f"{mode_label} | {count} shown | / g",
        f"{mode_label} | {count} | / g",
        f"{mode_label} | {count}",
    ]
    if width is None:
        return variants[0]
    for variant in variants:
        if len(variant) <= width:
            return variant
    return truncate(variants[-1], width)


def empty_history_title(query: str, *, mode: str = "conversations") -> str:
    if query.strip():
        return "No matching dialogs"
    if mode == "runs":
        return "No Codex runs"
    if mode == "all":
        return "No Codex history"
    return "No Codex dialogs"


def empty_history_text(
    query: str,
    *,
    mode: str = "conversations",
    alternate_mode: str = "",
    alternate_count: int = 0,
) -> str:
    if query.strip():
        text = "No Codex dialogs match the current search."
    elif mode == "runs":
        text = "No Codex runs found."
    elif mode == "all":
        text = "No Codex history found."
    else:
        text = "No Codex dialogs found."
    if alternate_mode and alternate_count > 0:
        alternate_label = history_mode_entry_label(alternate_mode, alternate_count)
        text = f"{text} Press g to view {alternate_count} {alternate_label}."
    return text


def empty_history_status(
    query: str,
    *,
    mode: str = "conversations",
    alternate_mode: str = "",
    alternate_count: int = 0,
) -> str:
    if alternate_mode and alternate_count > 0:
        current_label = history_mode_status_label(mode)
        alternate_label = history_mode_entry_label(alternate_mode, alternate_count)
        return f"No {current_label} shown. Press g for {alternate_count} {alternate_label}."
    if query.strip() or mode == "conversations":
        return "No dialogs found."
    return f"No {history_mode_status_label(mode)} found."


def history_loaded_status(mode: str, count: int) -> str:
    if mode == "runs":
        label = "run group" if count == 1 else "run groups"
    elif mode == "all":
        label = "history row" if count == 1 else "history rows"
    else:
        label = "dialog" if count == 1 else "dialogs"
    return f"{count} {label} loaded."


def next_nonempty_history_mode(
    threads: Iterable[ThreadRow],
    *,
    current_mode: str,
    query: str = "",
) -> tuple[str, int]:
    thread_list = list(threads)
    try:
        current_index = HISTORY_MODES.index(current_mode)
    except ValueError:
        return "", 0
    for offset in range(1, len(HISTORY_MODES)):
        mode = HISTORY_MODES[(current_index + offset) % len(HISTORY_MODES)]
        count = len(build_history_entries(thread_list, mode=mode, query=query))
        if count:
            return mode, count
    return "", 0


def history_mode_containing_thread(
    threads: Iterable[ThreadRow],
    thread_id: str,
    *,
    current_mode: str,
    query: str = "",
) -> str:
    if not thread_id:
        return current_mode
    thread_list = list(threads)
    mode_order = [current_mode, *(mode for mode in HISTORY_MODES if mode != current_mode)]
    for mode in mode_order:
        if mode not in HISTORY_MODES:
            continue
        entries = build_history_entries(thread_list, mode=mode, query=query)
        if any(any(thread.id == thread_id for thread in entry.threads) for entry in entries):
            return mode
    return current_mode


def history_mode_entry_label(mode: str, count: int) -> str:
    if mode == "runs":
        return "run group" if count == 1 else "run groups"
    if mode == "all":
        return "history row" if count == 1 else "history rows"
    return "conversation" if count == 1 else "conversations"


def history_mode_status_label(mode: str) -> str:
    if mode == "runs":
        return "runs"
    if mode == "all":
        return "history"
    return "dialogs"


def image_attachment_help_text(count: int) -> str:
    if count <= 0:
        return "Cmd/Ctrl+V img"
    if count == 1:
        return "[Image 1]"
    return f"{count} images"


def selection_index_for_entry(entries: list[HistoryEntry], thread_id: str) -> int:
    if not entries:
        return 0
    if thread_id:
        for index, entry in enumerate(entries):
            if entry.thread.id == thread_id:
                return index
    return 0


def exec_official_resume(session_id: str) -> None:
    command = [str(real_codex_bin()), "resume"]
    if not os.environ.get("CODEX_ALT_SCREEN"):
        command.append("--no-alt-screen")
    command.append(session_id)
    os.execv(str(real_codex_bin()), command)


def thread_id_from_stream_record(record: dict[str, object]) -> str:
    if record.get("type") == "thread.started":
        value = record.get("thread_id")
        return value.strip() if isinstance(value, str) else ""
    payload = record.get("payload")
    if isinstance(payload, dict) and record.get("type") == "session_meta":
        value = payload.get("id")
        return value.strip() if isinstance(value, str) else ""
    return ""


if TEXTUAL_IMPORT_ERROR is None:

    class ComposerInput(Input):
        def action_paste(self) -> None:
            handler = getattr(self.app, "handle_composer_system_paste", None)
            if callable(handler) and handler():
                return
            super().action_paste()

        def _on_paste(self, event: Paste) -> None:
            handler = getattr(self.app, "handle_composer_paste_text", None)
            if callable(handler) and handler(event.text):
                event.prevent_default()
                event.stop()
                return
            super()._on_paste(event)


    class CodexTextualApp(App[object]):
        TITLE = "CodexTUI"
        ENABLE_COMMAND_PALETTE = False

        CSS = """
        Screen {
            background: #07101d;
            color: #d7deeb;
        }

        #root {
            height: 1fr;
        }

        #history-pane {
            width: 34%;
            min-width: 28;
            border-right: solid #3b465d;
            background: #091524;
        }

        #conversation-pane {
            width: 1fr;
            background: #07101d;
        }

        #history-title, #conversation-title, #status-line {
            height: 1;
            padding: 0 1;
            background: #111c2d;
            color: #f1e7d0;
            text-style: bold;
        }

        #history-search, #composer {
            height: 3;
            border: solid #3b465d;
            background: #0b1829;
        }

        #mode-line {
            height: 1;
            padding: 0 1;
            color: #9aa8bf;
        }

        #thread-list {
            height: 1fr;
            border: none;
        }

        .thread-row {
            height: 3;
            padding: 0 1;
        }

        .thread-text {
            height: 2;
        }

        #transcript {
            height: 1fr;
            padding: 0 1;
            background: #07101d;
            border: none;
        }

        .transcript-row {
            height: auto;
            padding: 0 0 1 0;
        }

        .transcript-text {
            height: auto;
        }

        #composer-help {
            height: 1;
            padding: 0 1;
            color: #8fa0ba;
        }
        """

        BINDINGS = [
            ("q", "quit_or_back", "Back/Quit"),
            ("escape", "back", "Back"),
            ("/", "focus_search", "Search"),
            ("b", "toggle_history_pane", "History"),
            ("f2", "toggle_history_pane", "History"),
            ("g", "cycle_history_mode", "Mode"),
            ("n", "new_dialog", "New"),
            ("r", "refresh", "Refresh"),
            ("i", "focus_composer", "Compose"),
            ("c", "focus_composer", "Compose"),
            ("R", "official_resume", "Official resume"),
            Binding("cmd+v,super+v", "paste_clipboard_image", "Paste image", show=False),
        ]

        def __init__(self, thread_loader: ThreadLoader, *, raw_json: bool = False) -> None:
            super().__init__()
            self.thread_loader = thread_loader
            self.raw_json = raw_json
            self.threads: list[ThreadRow] = []
            self.entries: list[HistoryEntry] = []
            self.history_mode = "conversations"
            self.query = ""
            self.current_thread: ThreadRow | None = None
            self.current_session_info = SessionInfo()
            self.status_text = "Ready"
            self.transcript_blocks: list[TranscriptBlock] = []
            self.expanded_block_ids: set[str] = set()
            self.live_block_counter = 0
            self.live_thread_id = ""
            self.thread_ids_before_new_stream: set[str] = set()
            self.new_dialog_active = False
            self.streaming = False
            self.history_visible = True
            self.pending_gg = False
            self.pending_image_paths: list[str] = []
            self.search_timer: Timer | None = None
            self.live_call_labels: dict[str, str] = {}
            self.live_tool_output_details: dict[str, str] = {}
            self.live_file_change_details: dict[str, tuple[FileChange, ...]] = {}

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal(id="root"):
                with Vertical(id="history-pane"):
                    yield Static("History", id="history-title")
                    yield Input(placeholder="Search history", id="history-search")
                    yield Static("", id="mode-line")
                    yield ListView(id="thread-list")
                with Vertical(id="conversation-pane"):
                    yield Static("Select a conversation", id="conversation-title")
                    yield ListView(id="transcript")
                    yield Static("", id="composer-help")
                    yield ComposerInput(placeholder="Type a message and press Enter", id="composer")
                    yield Static("Ready", id="status-line")
            yield Footer()

        def on_mount(self) -> None:
            self.load_threads()
            self.refresh_history()
            if self.size.width <= COMPACT_LAYOUT_MAX_WIDTH:
                self.set_history_pane_visible(False, focus=False)
                self.focus_transcript()
                return
            self.update_composer_help()
            self.focus_history_list()

        def _check_resize(self) -> None:
            super()._check_resize()
            if getattr(self, "_is_mounted", False):
                self.call_after_refresh(self.refresh_width_sensitive_layout)

        def load_threads(self) -> None:
            self.threads = self.thread_loader()

        def refresh_history(self) -> None:
            selected_id = self.current_thread.id if self.current_thread else ""
            self.entries = build_history_entries(self.threads, mode=self.history_mode, query=self.query)
            mode_line = self.query_one("#mode-line", Static)
            mode_line.update(
                mode_line_text(
                    self.history_mode,
                    len(self.entries),
                    width=self.history_mode_content_width(),
                )
            )
            list_view = self.query_one("#thread-list", ListView)
            list_view.clear()
            row_width = self.history_row_width()
            for entry in self.entries:
                item = ListItem(
                    Static(history_row_renderable(entry, row_width), classes="thread-text"),
                    classes="thread-row",
                )
                setattr(item, "history_entry", entry)
                list_view.append(item)
            if self.entries:
                selected = selection_index_for_entry(self.entries, selected_id)
                list_view.index = selected
                selected_thread = self.entries[selected].thread
                if not self.new_dialog_active and (
                    self.current_thread is None or self.current_thread.id != selected_thread.id
                ):
                    self.current_thread = selected_thread
                    self.render_conversation(selected_thread)
            elif not self.new_dialog_active:
                self.clear_conversation_for_empty_history()
            if not self.new_dialog_active:
                self.set_status(
                    self.empty_history_status()
                    if not self.entries
                    else history_loaded_status(self.history_mode, len(self.entries))
                )

        def refresh_width_sensitive_layout(self) -> None:
            self.render_history_mode_line()
            self.render_history_rows()
            self.render_current_conversation_title()
            self.update_composer_help()
            self.render_status_line()

        def render_history_mode_line(self) -> None:
            self.query_one("#mode-line", Static).update(
                mode_line_text(
                    self.history_mode,
                    len(self.entries),
                    width=self.history_mode_content_width(),
                )
            )

        def render_history_rows(self) -> None:
            row_width = self.history_row_width()
            list_view = self.query_one("#thread-list", ListView)
            for item in list_view.children:
                entry = getattr(item, "history_entry", None)
                if isinstance(entry, HistoryEntry):
                    item.query_one(Static).update(history_row_renderable(entry, row_width))

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id == "history-search":
                self.query = event.value
                if self.search_timer is not None:
                    self.search_timer.stop()
                self.search_timer = self.set_timer(SEARCH_DEBOUNCE_SECONDS, self.refresh_history)

        def clear_history_search(self) -> None:
            if self.search_timer is not None:
                self.search_timer.stop()
                self.search_timer = None
            self.query = ""
            search = self.query_one("#history-search", Input)
            if search.value:
                search.value = ""
            if self.search_timer is not None:
                self.search_timer.stop()
                self.search_timer = None

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input.id == "history-search":
                self.focus_history_list()
                return
            if event.input.id == "composer":
                if self.submit_composer(event.value):
                    event.input.value = ""

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            if event.list_view.id == "transcript":
                self.toggle_selected_transcript_block()
                return
            entry = getattr(event.item, "history_entry", None)
            if isinstance(entry, HistoryEntry):
                self.open_entry(entry, focus_transcript=True)

        def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
            if event.list_view.id == "transcript":
                return
            try:
                focused_id = getattr(self.focused, "id", "")
            except ScreenStackError:
                return
            if focused_id != "thread-list":
                return
            if event.item is None:
                return
            entry = getattr(event.item, "history_entry", None)
            if isinstance(entry, HistoryEntry):
                if not any(candidate.thread.id == entry.thread.id for candidate in self.entries):
                    return
                self.open_entry(entry, focus_transcript=False)

        def on_key(self, event: Key) -> None:
            focused_id = getattr(self.focused, "id", "")
            if focused_id == "thread-list" and event.key in {"j", "k"}:
                event.prevent_default()
                event.stop()
                list_view = self.query_one("#thread-list", ListView)
                if event.key == "j":
                    list_view.action_cursor_down()
                else:
                    list_view.action_cursor_up()
                return
            if focused_id == "transcript" and event.key in TRANSCRIPT_SCROLL_KEYS:
                event.prevent_default()
                event.stop()
                self.scroll_transcript(event.key)
                return
            if focused_id == "transcript" and event.key in TRANSCRIPT_INNER_SCROLL_KEYS:
                event.prevent_default()
                event.stop()
                self.scroll_selected_transcript_block(event.key)
                return
            if focused_id == "transcript" and event.key == "t":
                event.prevent_default()
                event.stop()
                self.toggle_selected_transcript_block()
                return
            if focused_id == "transcript" and event.key == "g":
                event.prevent_default()
                event.stop()
                if self.current_thread is None and not self.new_dialog_active:
                    self.pending_gg = False
                    self.action_cycle_history_mode()
                    return
                if self.pending_gg:
                    self.pending_gg = False
                    self.scroll_transcript("home")
                else:
                    self.pending_gg = True
                    self.set_timer(0.7, self.clear_pending_gg)
                return
            self.pending_gg = False
            if event.key != "escape":
                return
            if focused_id in {"composer", "history-search", "transcript"}:
                event.prevent_default()
                event.stop()
                self.focus_history_list()

        def action_focus_search(self) -> None:
            if not self.history_visible:
                self.set_history_pane_visible(True, focus=False)
            self.query_one("#history-search", Input).focus()

        def action_focus_composer(self) -> None:
            self.query_one("#composer", Input).focus()

        def action_paste_clipboard_image(self) -> None:
            self.handle_composer_system_paste()

        def action_cycle_history_mode(self) -> None:
            index = HISTORY_MODES.index(self.history_mode)
            self.history_mode = HISTORY_MODES[(index + 1) % len(HISTORY_MODES)]
            self.refresh_history()

        def action_refresh(self) -> None:
            selected_id = self.current_thread.id if self.current_thread else ""
            self.load_threads()
            self.refresh_history()
            if selected_id:
                entry = next((entry for entry in self.entries if entry.thread.id == selected_id), None)
                if entry:
                    self.current_thread = entry.thread
                    self.render_conversation(entry.thread)

        def action_toggle_history_pane(self) -> None:
            self.set_history_pane_visible(not self.history_visible)

        def action_new_dialog(self) -> None:
            if self.streaming:
                self.set_status("Codex is still responding.")
                return
            self.clear_history_search()
            self.current_thread = None
            self.current_session_info = default_session_info()
            self.live_thread_id = ""
            self.thread_ids_before_new_stream = set()
            self.new_dialog_active = True
            self.expanded_block_ids.clear()
            self.transcript_blocks = [
                TranscriptBlock(
                    id="new-dialog",
                    kind="status",
                    title="New dialog",
                    subtitle="",
                    text="New Codex dialog.",
                )
            ]
            self.query_one("#conversation-title", Static).update("New Codex dialog")
            self.refresh_history()
            self.render_transcript_blocks(preserve_index=False)
            self.set_status("New dialog. Type the first message below.")
            self.update_composer_help()
            self.query_one("#composer", Input).focus()

        def action_back(self) -> None:
            self.focus_history_list()

        def action_quit_or_back(self) -> None:
            focused = self.focused
            focused_id = getattr(focused, "id", "") if focused is not None else ""
            if focused_id == "transcript" and not self.history_visible:
                self.exit(0)
                return
            if focused_id in {"composer", "history-search", "transcript"}:
                self.focus_history_list()
                return
            self.exit(0)

        def action_official_resume(self) -> None:
            if self.current_thread is None:
                self.set_status("Open a conversation before official resume.")
                return
            self.exit(("resume", self.current_thread.id))

        def open_entry(self, entry: HistoryEntry, *, focus_transcript: bool) -> None:
            if self.current_thread is not None and self.current_thread.id == entry.thread.id:
                if focus_transcript:
                    self.focus_transcript()
                return
            self.current_thread = entry.thread
            self.render_conversation(entry.thread)
            if focus_transcript:
                self.focus_transcript()

        def render_conversation(self, thread: ThreadRow) -> None:
            self.render_conversation_title(thread)
            self.current_session_info = session_info_for_thread(thread)
            self.transcript_blocks = transcript_blocks_for_thread(thread)
            self.expanded_block_ids.clear()
            if not self.transcript_blocks:
                self.transcript_blocks = [
                    TranscriptBlock(
                        id="empty",
                        kind="status",
                        title="Empty",
                        subtitle="",
                        text="No chat messages found in this session.",
                    )
                ]
                self.render_transcript_blocks(preserve_index=False)
                self.set_status("No chat messages found.")
                return
            self.render_transcript_blocks(preserve_index=False)
            self.set_status(f"Opened {short_id(thread.id)}. Type below to continue.")

        def clear_conversation_for_empty_history(self) -> None:
            self.current_thread = None
            self.current_session_info = default_session_info()
            self.expanded_block_ids.clear()
            alternate_mode, alternate_count = self.empty_history_alternate()
            title = empty_history_title(self.query, mode=self.history_mode)
            text = empty_history_text(
                self.query,
                mode=self.history_mode,
                alternate_mode=alternate_mode,
                alternate_count=alternate_count,
            )
            self.query_one("#conversation-title", Static).update(title)
            self.transcript_blocks = [
                TranscriptBlock(
                    id="empty-history",
                    kind="status",
                    title="Empty",
                    subtitle="",
                    text=text,
                )
            ]
            self.render_transcript_blocks(preserve_index=False)

        def submit_composer(self, prompt: str) -> bool:
            parsed = parse_composer_payload(prompt, cwd=self.composer_cwd())
            payload = ComposerPayload(parsed.prompt, tuple([*self.pending_image_paths, *parsed.image_paths]))
            if not payload.prompt:
                if payload.image_paths:
                    self.set_status("Type a message for the attached image before sending.")
                    self.update_composer_help()
                return False
            missing = first_missing_attachment(payload.image_paths)
            if missing:
                self.set_status(f"Image not found: {missing}")
                return False
            if self.streaming:
                self.set_status("Codex is still responding.")
                return False
            thread = self.current_thread
            self.append_transcript_block(
                TranscriptBlock(
                    id=self.next_live_block_id("user"),
                    kind="message",
                    title="YOU",
                    subtitle="",
                    text=composer_display_text(payload),
                    role="user",
                )
            )
            self.append_transcript_block(
                TranscriptBlock(
                    id=self.next_live_block_id("status"),
                    kind="status",
                    title="Status",
                    subtitle="",
                    text="[task] Codex turn starting...",
                )
            )
            self.streaming = True
            self.live_call_labels.clear()
            self.live_tool_output_details.clear()
            self.live_file_change_details.clear()
            suffix = f" with {len(payload.image_paths)} image(s)" if payload.image_paths else ""
            if thread is None:
                self.new_dialog_active = True
                self.live_thread_id = ""
                self.thread_ids_before_new_stream = {existing.id for existing in self.threads}
                self.set_status(f"Starting new Codex dialog{suffix}...")
                self.run_worker(
                    lambda: self.new_worker(payload.prompt, payload.image_paths),
                    thread=True,
                    exclusive=True,
                )
            else:
                self.set_status(f"Sending to Codex{suffix}...")
                self.run_worker(
                    lambda: self.resume_worker(thread, payload.prompt, payload.image_paths),
                    thread=True,
                    exclusive=True,
                )
            self.pending_image_paths.clear()
            self.update_composer_help()
            return True

        def new_worker(self, prompt: str, image_paths: tuple[str, ...] = ()) -> None:
            writer = TextualStreamWriter(self)
            code = run_codex_json_stream(
                codex_exec_command(
                    real_codex_bin(),
                    prompt=prompt,
                    resume_id=None,
                    image_paths=image_paths,
                ),
                raw_json=self.raw_json,
                stdout=writer,
                stderr_to_stdout=True,
                event_callback=lambda record: self.call_from_thread(
                    self.update_session_info_from_stream_record,
                    record,
                ),
            )
            self.call_from_thread(self.finish_new_stream, code)

        def resume_worker(self, thread: ThreadRow, prompt: str, image_paths: tuple[str, ...] = ()) -> None:
            writer = TextualStreamWriter(self)
            code = run_codex_json_stream(
                codex_exec_command(
                    real_codex_bin(),
                    prompt=prompt,
                    resume_id=thread.id,
                    image_paths=image_paths,
                ),
                raw_json=self.raw_json,
                stdout=writer,
                stderr_to_stdout=True,
                event_callback=lambda record: self.call_from_thread(
                    self.update_session_info_from_stream_record,
                    record,
                ),
            )
            self.call_from_thread(self.finish_stream, thread.id, code)

        def append_stream_line(self, line: str) -> None:
            self.append_stream_block(line)

        def append_stream_block(self, block: str) -> None:
            if block.startswith("YOU\n"):
                return
            stripped = block.rstrip()
            detail_text = self.live_tool_output_details.pop(stripped, "")
            file_changes = self.live_file_change_details.pop(stripped, ())
            self.append_transcript_block(
                transcript_block_from_stream_block(
                    stripped,
                    self.next_live_block_id("stream"),
                    detail_text=detail_text,
                    file_changes=file_changes,
                )
            )

        def finish_stream(self, thread_id: str, code: int) -> None:
            self.streaming = False
            self.load_threads()
            refreshed = next((thread for thread in self.threads if thread.id == thread_id), self.current_thread)
            if refreshed is not None:
                self.current_thread = refreshed
                self.render_conversation(refreshed)
            self.refresh_history()
            status = "Codex finished." if code == 0 else f"Codex exited with status {code}."
            self.set_status(status)

        def finish_new_stream(self, code: int) -> None:
            self.streaming = False
            self.new_dialog_active = False
            self.load_threads()
            refreshed = self.created_thread_after_new_stream()
            if refreshed is not None:
                self.history_mode = history_mode_containing_thread(
                    self.threads,
                    refreshed.id,
                    current_mode=self.history_mode,
                    query=self.query,
                )
                self.current_thread = refreshed
                self.render_conversation(refreshed)
            self.refresh_history()
            status = "Codex finished." if code == 0 else f"Codex exited with status {code}."
            self.set_status(status)

        def created_thread_after_new_stream(self) -> ThreadRow | None:
            if self.live_thread_id:
                thread = next((thread for thread in self.threads if thread.id == self.live_thread_id), None)
                if thread is not None:
                    return thread
            new_threads = [
                thread
                for thread in self.threads
                if thread.id not in self.thread_ids_before_new_stream
            ]
            if new_threads:
                return max(new_threads, key=lambda thread: thread.recency_at_ms)
            return self.threads[0] if self.threads else None

        def set_status(self, text: str) -> None:
            self.status_text = text
            self.render_status_line()

        def render_status_line(self) -> None:
            self.query_one("#status-line", Static).update(
                status_line_text(
                    self.status_text,
                    self.current_session_info,
                    width=self.conversation_content_width(),
                )
            )

        def render_conversation_title(self, thread: ThreadRow) -> None:
            self.query_one("#conversation-title", Static).update(
                conversation_title(thread, width=self.conversation_content_width())
            )

        def render_current_conversation_title(self) -> None:
            if self.current_thread is not None:
                self.render_conversation_title(self.current_thread)
                return
            title = (
                "New Codex dialog"
                if self.new_dialog_active
                else empty_history_title(self.query, mode=self.history_mode)
            )
            self.query_one("#conversation-title", Static).update(
                truncate(title, self.conversation_content_width())
            )

        def empty_history_alternate(self) -> tuple[str, int]:
            return next_nonempty_history_mode(
                self.threads,
                current_mode=self.history_mode,
                query=self.query,
            )

        def empty_history_status(self) -> str:
            alternate_mode, alternate_count = self.empty_history_alternate()
            return empty_history_status(
                self.query,
                mode=self.history_mode,
                alternate_mode=alternate_mode,
                alternate_count=alternate_count,
            )

        def conversation_content_width(self) -> int:
            pane = self.query_one("#conversation-pane", Vertical)
            width = pane.size.width
            if width <= 0:
                width = self.estimated_conversation_pane_width()
            return max(1, width - 2)

        def estimated_conversation_pane_width(self) -> int:
            total_width = max(1, self.size.width)
            if self.history_visible and total_width > COMPACT_LAYOUT_MAX_WIDTH:
                history_width = max(28, int(total_width * 0.34))
                return max(1, total_width - history_width)
            return total_width

        def update_session_info_from_stream_record(self, record: dict[str, object]) -> None:
            thread_id = thread_id_from_stream_record(record)
            if thread_id:
                self.live_thread_id = thread_id
            if not self.raw_json:
                detail = tool_output_detail_from_stream_record(record, call_labels=self.live_call_labels)
                if detail is not None:
                    self.live_tool_output_details[detail.rendered] = detail.detail_text
                file_detail = file_change_detail_from_stream_record(record, call_labels=self.live_call_labels)
                if file_detail is not None:
                    changes = tuple(
                        FileChange(path=changed_path, diff=file_detail.patch_text)
                        for changed_path in patch_paths(file_detail.patch_text)
                    )
                    if changes:
                        self.live_file_change_details[file_detail.rendered] = changes
            self.current_session_info = session_info_from_record(record, current=self.current_session_info)
            self.render_status_line()

        def focus_history_list(self) -> None:
            if not self.history_visible:
                self.set_history_pane_visible(True, focus=False)
            self.query_one("#thread-list", ListView).focus()

        def focus_transcript(self) -> None:
            self.query_one("#transcript", ListView).focus()

        def set_history_pane_visible(self, visible: bool, *, focus: bool = True) -> None:
            self.history_visible = visible
            pane = self.query_one("#history-pane", Vertical)
            pane.display = visible
            pane.styles.width = "34%" if visible else 0
            pane.styles.min_width = 28 if visible else 0
            pane.styles.border_right = ("solid", "#3b465d") if visible else None
            self.update_composer_help()
            self.query_one("#root", Horizontal).refresh(layout=True)
            if visible:
                self.set_status("History pane shown.")
                if focus:
                    self.focus_history_list()
                return
            self.set_status("History pane hidden. Press b or F2 to show it.")
            if focus:
                self.focus_transcript()

        def update_composer_help(self) -> None:
            self.query_one("#composer-help", Static).update(
                composer_help_text(
                    history_visible=self.history_visible,
                    pending_image_count=len(self.pending_image_paths),
                    width=self.conversation_content_width(),
                )
            )

        def composer_cwd(self) -> Path:
            thread = self.current_thread
            if thread is not None and thread.cwd:
                return Path(thread.cwd).expanduser()
            return Path.cwd()

        def handle_composer_paste_text(self, text: str) -> bool:
            image_paths = image_paths_from_paste_text(text, cwd=self.composer_cwd())
            if image_paths:
                self.add_pending_image_paths(image_paths, source="Pasted")
                return True
            if not text.strip():
                return self.handle_composer_system_paste()
            return False

        def handle_composer_system_paste(self) -> bool:
            image_path, error = capture_clipboard_image()
            if image_path is None:
                self.set_status(error or "No image found in clipboard.")
                return False
            self.add_pending_image_paths((image_path,), source="Clipboard")
            return True

        def add_pending_image_paths(self, image_paths: Iterable[str], *, source: str) -> None:
            added = 0
            seen = set(self.pending_image_paths)
            for image_path in image_paths:
                if image_path in seen:
                    continue
                self.pending_image_paths.append(image_path)
                seen.add(image_path)
                added += 1
            self.query_one("#composer", Input).focus()
            self.update_composer_help()
            total = len(self.pending_image_paths)
            label = "image" if total == 1 else "images"
            if added:
                self.set_status(f"{source} {added} image(s). {total} pending {label}.")
            else:
                self.set_status(f"Image already attached. {total} pending {label}.")

        def scroll_transcript(self, key: str) -> None:
            transcript = self.query_one("#transcript", ListView)
            if key in {"up", "k"}:
                transcript.action_cursor_up()
            elif key in {"down", "j"}:
                transcript.action_cursor_down()
            elif key == "pageup":
                transcript.action_page_up()
            elif key == "pagedown":
                transcript.action_page_down()
            elif key == "home":
                transcript.index = 0 if self.transcript_blocks else None
                transcript.scroll_home(animate=False)
            elif key in {"end", "G"}:
                transcript.index = max(0, len(self.transcript_blocks) - 1) if self.transcript_blocks else None
                transcript.scroll_end(animate=False)

        def scroll_selected_transcript_block(self, key: str) -> None:
            transcript = self.query_one("#transcript", ListView)
            direction = -1 if key.endswith("k") else 1
            transcript.scroll_relative(y=direction * TRANSCRIPT_SCROLL_STEP_LINES, animate=False)

        def render_transcript_blocks(self, *, preserve_index: bool = True) -> None:
            transcript = self.query_one("#transcript", ListView)
            selected = transcript.index if preserve_index else 0
            transcript.clear()
            for block in self.transcript_blocks:
                transcript.append(self.transcript_item(block))
            if self.transcript_blocks:
                transcript.index = max(0, min(selected or 0, len(self.transcript_blocks) - 1))

        def append_transcript_block(self, block: TranscriptBlock) -> None:
            self.transcript_blocks.append(block)
            transcript = self.query_one("#transcript", ListView)
            transcript.append(self.transcript_item(block))
            transcript.index = len(self.transcript_blocks) - 1
            transcript.scroll_end(animate=False)
            transcript.refresh(layout=True)

        def transcript_item(self, block: TranscriptBlock) -> ListItem:
            item = ListItem(
                Static(
                    transcript_block_renderable(block, expanded=block.id in self.expanded_block_ids),
                    classes="transcript-text",
                ),
                classes="transcript-row",
            )
            setattr(item, "transcript_block", block)
            return item

        def toggle_selected_transcript_block(self) -> None:
            transcript = self.query_one("#transcript", ListView)
            index = transcript.index
            if index is None or index < 0 or index >= len(self.transcript_blocks):
                return
            block = self.transcript_blocks[index]
            if not block.expandable:
                self.set_status("Selected block has nothing to expand.")
                return
            if block.id in self.expanded_block_ids:
                self.expanded_block_ids.remove(block.id)
                self.set_status(f"Collapsed {block.title}.")
            else:
                self.expanded_block_ids.add(block.id)
                self.set_status(f"Expanded {block.title}.")
            self.render_transcript_blocks(preserve_index=True)
            self.focus_transcript()

        def next_live_block_id(self, prefix: str) -> str:
            self.live_block_counter += 1
            return f"live-{prefix}-{self.live_block_counter}"

        def clear_pending_gg(self) -> None:
            self.pending_gg = False

        def history_row_width(self) -> int:
            estimated = int(self.size.width * 0.34) - 6
            return max(MIN_HISTORY_ROW_WIDTH, min(MAX_HISTORY_ROW_WIDTH, estimated))

        def history_mode_content_width(self) -> int:
            pane = self.query_one("#history-pane", Vertical)
            width = pane.size.width
            if width <= 0:
                width = max(28, int(max(1, self.size.width) * 0.34))
            return max(1, width - 2)


    class TextualStreamWriter:
        def __init__(self, app: CodexTextualApp) -> None:
            self.app = app
            self.pending = ""

        def write(self, text: str) -> int:
            if not text:
                return 0
            self.pending += text
            return len(text)

        def flush(self) -> None:
            block = self.pending.rstrip("\n")
            self.pending = ""
            if block:
                self.app.call_from_thread(self.app.append_stream_block, block)


    def message_renderable(message: ChatMessage) -> Panel:
        label = role_label(message)
        timestamp = compact_timestamp(message.timestamp)
        title = f"{label} {timestamp}".strip()
        style = "cyan" if message.role == "user" else "green" if message.phase == "final_answer" else "blue"
        text = message.text.rstrip()
        pretty = pretty_json_text(text)
        if pretty != text:
            body = Syntax(pretty, "json", word_wrap=True, theme="monokai")
        else:
            body = RichMarkdown(text or " ")
        return Panel(Group(body), title=title, title_align="left", border_style=style, padding=(0, 1))


    def stream_renderable(block: str) -> object:
        message = chat_message_from_stream_block(block)
        if message is not None:
            return message_renderable(message)
        return Text(block.rstrip(), style=style_for_stream_line(block))


    def transcript_block_renderable(block: TranscriptBlock, *, expanded: bool) -> object:
        title = Text()
        title.append(block.title.upper(), style=f"bold {style_for_transcript_block(block)}")
        if block.subtitle:
            title.append(f" {block.subtitle}", style="dim")

        if block.kind == "file_change":
            body = transcript_file_change_body(block, expanded=expanded)
        elif block.detail_text:
            body = transcript_detail_body(block, expanded=expanded)
        else:
            body = transcript_text_body(block)
        return Panel(Group(body), title=title, title_align="left", border_style=style_for_transcript_block(block), padding=(0, 1))


    def transcript_text_body(block: TranscriptBlock) -> object:
        text = block.text.rstrip()
        pretty = pretty_json_text(text)
        if pretty != text:
            return Syntax(pretty, "json", word_wrap=True, theme="monokai")
        if block.kind == "status":
            return Text(text or " ", style="dim")
        if block.kind == "tool":
            return Text(text or " ", style="yellow")
        return RichMarkdown(text or " ")


    def transcript_file_change_body(block: TranscriptBlock, *, expanded: bool) -> object:
        if not expanded:
            summary = Text()
            summary.append(block.text or "Changed files", style="#f4f0e4")
            summary.append("\nEnter/t expands", style="dim")
            return summary
        renderables: list[object] = []
        for change in block.file_changes:
            header = Text(change.path, style="bold yellow")
            diff = Syntax(change.diff.rstrip() or " ", "diff", word_wrap=True, theme="monokai")
            renderables.append(Group(header, diff))
        return Group(*renderables) if renderables else Text(block.text or "No diff available.", style="dim")


    def transcript_detail_body(block: TranscriptBlock, *, expanded: bool) -> object:
        if expanded:
            return Text(block.detail_text.rstrip() or " ", style="yellow")
        summary = Text()
        summary.append(block.text or "Folded output", style="yellow")
        summary.append("\nEnter/t expands", style="dim")
        return summary


    def transcript_block_from_stream_block(
        block: str,
        block_id: str,
        *,
        detail_text: str = "",
        file_changes: tuple[FileChange, ...] = (),
    ) -> TranscriptBlock:
        message = chat_message_from_stream_block(block)
        if message is not None:
            return TranscriptBlock(
                id=block_id,
                kind="message",
                title=role_label(message),
                subtitle="live",
                text=message.text,
                role=message.role,
                phase=message.phase,
            )
        stripped = block.rstrip()
        if file_changes:
            return TranscriptBlock(
                id=block_id,
                kind="file_change",
                title="File change",
                subtitle="live",
                text=changed_paths_summary(change.path for change in file_changes),
                file_changes=file_changes,
            )
        kind = kind_for_stream_block(stripped)
        if detail_text:
            kind = "tool_output"
        return TranscriptBlock(
            id=block_id,
            kind=kind,
            title=title_for_stream_block(stripped),
            subtitle="live",
            text=stripped,
            detail_text=detail_text,
        )


    def kind_for_stream_block(block: str) -> str:
        stripped = block.strip()
        if stripped.startswith("[tool output]"):
            return "tool_output"
        if stripped.startswith(("[tool]", "[tool output]", "[search]")):
            return "tool"
        return "status"


    def title_for_stream_block(block: str) -> str:
        stripped = block.strip()
        if stripped.startswith("[tool output]"):
            return "Tool output"
        if stripped.startswith("[tool]"):
            return "Tool"
        if stripped.startswith("[search]"):
            return "Search"
        if stripped.startswith("[tokens]"):
            return "Tokens"
        if stripped.startswith("[context]"):
            return "Context"
        if stripped.startswith("[reasoning]"):
            return "Reasoning"
        if stripped.startswith("[task]"):
            return "Status"
        return "Event"


    def style_for_transcript_block(block: TranscriptBlock) -> str:
        if block.kind == "file_change":
            return "yellow"
        if block.kind in {"tool", "tool_output"}:
            return "yellow"
        if block.kind == "status":
            return "dim"
        if block.role == "user":
            return "cyan"
        if block.phase == "final_answer":
            return "green"
        return "blue"


    def chat_message_from_stream_block(block: str) -> ChatMessage | None:
        if block.startswith("YOU\n"):
            return ChatMessage("", "user", "", unindent_stream_text(block.removeprefix("YOU\n")))
        if block.startswith("CODEX final\n"):
            return ChatMessage("", "assistant", "final_answer", unindent_stream_text(block.removeprefix("CODEX final\n")))
        if block.startswith("CODEX\n"):
            return ChatMessage("", "assistant", "", unindent_stream_text(block.removeprefix("CODEX\n")))
        return None


    def unindent_stream_text(text: str) -> str:
        return "\n".join(line[2:] if line.startswith("  ") else line for line in text.splitlines()).rstrip()


    def history_row_renderable(entry: HistoryEntry, width: int) -> Text:
        label = "D" if entry.kind == "conversation" else "G" if entry.is_group else "R"
        text = Text()
        text.append(f"{label} ", style="bold cyan" if label == "D" else "bold yellow")
        text.append(truncate(entry.title, width), style="bold #f4f0e4")
        text.append("\n  ")
        text.append(truncate(entry.subtitle, width), style="#8fa0ba")
        return text


    def style_for_stream_line(line: str) -> str:
        stripped = line.strip()
        if stripped.startswith("[task]") and ("failed" in stripped or "exited" in stripped):
            return "bold red"
        if stripped.startswith(("[tool]", "[tool output]", "[search]")):
            return "yellow"
        if stripped.startswith(("[task]", "[tokens]", "[context]", "[reasoning]")):
            return "dim"
        if stripped in {"YOU", "CODEX", "CODEX final"}:
            return "bold"
        return ""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from .file_nav import FileHit, file_hits_for_thread, render_file_hits
from .fzf import PickerSelection, choose_file, choose_search_match, choose_thread, is_available
from .models import SearchMatch
from .paths import real_codex_bin
from .store import CodexStore
from .transcript import filter_messages, format_ms, one_line, read_messages, render_thread, short_id, truncate


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(list(sys.argv[1:] if argv is None else argv)))
    try:
        return int(args.func(args) or 0)
    except LookupError as exc:
        print(f"cxp: {exc}", file=sys.stderr)
        return 2


def normalize_argv(argv: list[str]) -> list[str]:
    if argv and argv[0] == "h":
        argv = ["browse", *argv[1:]]
    elif argv and argv[0] == "history":
        argv = argv[1:]
    if argv and argv[0] in {"-h", "--help", "--version"}:
        return argv
    if not argv or argv[0].startswith("-"):
        argv = ["browse", *argv]
    return argv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cxp",
        description="CodexPlus, an unofficial local workbench for OpenAI Codex CLI sessions.",
    )
    parser.add_argument("--version", action="version", version=f"CodexPlus {__version__}")
    sub = parser.add_subparsers(
        dest="command",
        metavar="{browse,list,view,files,assistant,final,user,search,resume,path,stats,install-shim,compress}",
    )

    def add_hidden_parser(name: str) -> argparse.ArgumentParser:
        hidden = sub.add_parser(name, help=argparse.SUPPRESS)
        sub._choices_actions = [action for action in sub._choices_actions if action.dest != name]
        return hidden

    def add_common_filters(p: argparse.ArgumentParser) -> None:
        p.add_argument("-a", "--all", action="store_true", help="include archived sessions")
        p.add_argument("-n", "--limit", type=int, default=80, help="maximum sessions to load")
        p.add_argument("-q", "--query", help="filter sessions by title, prompt, cwd, or id")
        p.add_argument("--source", choices=["cli", "vscode", "exec"], help="filter by session source")
        cwd_group = p.add_mutually_exclusive_group()
        cwd_group.add_argument("--cwd", help="filter by working directory substring")
        cwd_group.add_argument("--here", action="store_true", help="filter to the current git workspace or cwd")

    browse_p = sub.add_parser("browse", aliases=["b"], help="pick a session and resume it")
    add_common_filters(browse_p)
    browse_p.add_argument("--mode", choices=["chat", "assistant", "final", "user"], default="chat")
    browse_p.set_defaults(func=browse_threads)

    list_p = sub.add_parser("list", aliases=["ls", "sessions"], help="list sessions")
    add_common_filters(list_p)
    list_p.add_argument("--json", action="store_true", help="emit JSON lines")
    list_p.set_defaults(func=list_threads)

    view_p = sub.add_parser("view", aliases=["show"], help="render one clean transcript")
    view_p.add_argument("selector", nargs="?", default="last", help="session id, prefix, title text, path, or last")
    view_p.add_argument("--mode", choices=["chat", "assistant", "final", "user"], default="chat")
    view_p.add_argument("--phase", action="append", help="limit assistant phases, such as commentary or final_answer")
    view_p.add_argument("--no-pager", action="store_true", help="print directly instead of opening less")
    view_p.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    view_p.set_defaults(func=view_thread)

    files_p = sub.add_parser("files", aliases=["f"], help="list files mentioned in a session")
    files_p.add_argument("selector", nargs="?", default="last", help="session id, prefix, title text, path, or last")
    files_p.add_argument("--mode", choices=["chat", "assistant", "final", "user"], default="chat")
    files_p.add_argument("--json", action="store_true", help="emit JSON lines")
    files_p.add_argument("--open", action="store_true", help="pick a file with fzf when available and open it in EDITOR")
    files_p.add_argument("--editor", help="editor command for --open, defaults to EDITOR or nvim/vim/vi")
    files_p.set_defaults(func=files_thread)

    assistant_p = sub.add_parser("assistant", aliases=["answers"], help="show only Codex messages")
    assistant_p.add_argument("selector", nargs="?", default="last")
    assistant_p.add_argument("--no-pager", action="store_true")
    assistant_p.add_argument("--no-color", action="store_true")
    assistant_p.set_defaults(func=lambda args: view_thread(setattr_return(args, "mode", "assistant")))

    final_p = sub.add_parser("final", help="show only the final Codex answer")
    final_p.add_argument("selector", nargs="?", default="last")
    final_p.add_argument("--no-pager", action="store_true")
    final_p.add_argument("--no-color", action="store_true")
    final_p.set_defaults(func=lambda args: view_thread(setattr_return(args, "mode", "final")))

    user_p = sub.add_parser("user", aliases=["questions"], help="show only user turns")
    user_p.add_argument("selector", nargs="?", default="last")
    user_p.add_argument("--no-pager", action="store_true")
    user_p.add_argument("--no-color", action="store_true")
    user_p.set_defaults(func=lambda args: view_thread(setattr_return(args, "mode", "user")))

    search_p = sub.add_parser("search", aliases=["grep"], help="search clean session text")
    search_p.add_argument("text")
    search_p.add_argument("--mode", choices=["chat", "assistant", "final", "user"], default="chat")
    search_p.add_argument("--metadata-only", action="store_true", help="search only titles, previews, and cwd")
    search_p.add_argument("--open", action="store_true", help="pick a matching session with fzf and resume it")
    search_p.add_argument("--json", action="store_true", help="emit JSON lines")
    search_p.add_argument("-a", "--all", action="store_true", help="include archived sessions")
    search_p.add_argument("-n", "--limit", type=int, default=40, help="maximum matches to print")
    search_p.add_argument("--source", choices=["cli", "vscode", "exec"], help="filter by source")
    search_cwd_group = search_p.add_mutually_exclusive_group()
    search_cwd_group.add_argument("--cwd", help="filter by working directory substring")
    search_cwd_group.add_argument("--here", action="store_true", help="filter to the current git workspace or cwd")
    search_p.set_defaults(func=search_threads)

    resume_p = sub.add_parser("resume", help="resume a selected Codex session")
    resume_p.add_argument("selector", nargs="?")
    resume_p.add_argument("-n", "--limit", type=int, default=80)
    resume_p.add_argument("-q", "--query")
    resume_p.add_argument("--source", choices=["cli", "vscode", "exec"])
    resume_cwd_group = resume_p.add_mutually_exclusive_group()
    resume_cwd_group.add_argument("--cwd")
    resume_cwd_group.add_argument("--here", action="store_true", help="filter to the current git workspace or cwd")
    resume_p.set_defaults(func=resume_thread)

    path_p = sub.add_parser("path", help="print the rollout JSONL path for a session")
    path_p.add_argument("selector", nargs="?", default="last")
    path_p.set_defaults(func=show_path)

    stats_p = sub.add_parser("stats", help="show session counts")
    stats_p.set_defaults(func=stats)

    preview_p = add_hidden_parser("preview")
    preview_p.add_argument("selector")
    preview_p.add_argument("--mode", choices=["chat", "assistant", "final", "user"], default="chat")
    preview_p.set_defaults(func=preview_thread)

    file_preview_p = add_hidden_parser("file-preview")
    file_preview_p.add_argument("path")
    file_preview_p.add_argument("line", nargs="?")
    file_preview_p.set_defaults(func=preview_file)

    install_p = sub.add_parser("install-shim", help="install an optional codex wrapper shim")
    install_p.add_argument("--target", default=str(Path.home() / ".local" / "bin" / "codex"), help="shim path")
    install_p.add_argument("--real-codex", default=str(real_codex_bin()), help="official Codex binary path")
    install_p.add_argument("--force", action="store_true", help="replace an existing target")
    install_p.set_defaults(func=install_shim)

    compress_p = sub.add_parser("compress", help="design placeholder for future local summaries")
    compress_p.set_defaults(func=compress_placeholder)

    return parser


def cwd_filter(args: argparse.Namespace) -> str | None:
    if getattr(args, "here", False):
        return str(current_project_root())
    return getattr(args, "cwd", None)


def current_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).expanduser().resolve(strict=False)
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def list_threads(args: argparse.Namespace) -> int:
    threads = CodexStore().load_threads(
        include_archived=args.all,
        limit=args.limit,
        query=args.query,
        source=args.source,
        cwd=cwd_filter(args),
    )
    if args.json:
        for thread in threads:
            print(
                json.dumps(
                    {
                        "id": thread.id,
                        "title": thread.title,
                        "cwd": thread.cwd,
                        "source": thread.source,
                        "archived": thread.archived,
                        "updated_at": format_ms(thread.recency_at_ms),
                        "rollout_path": thread.rollout_path,
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    if not threads:
        print("No sessions found.")
        return 0
    width = shutil.get_terminal_size((120, 30)).columns
    title_width = max(24, min(76, width - 54))
    print(f"{'updated':19}  {'src':6}  {'id':8}  {'title':{title_width}}  cwd")
    print(f"{'-' * 19}  {'-' * 6}  {'-' * 8}  {'-' * title_width}  {'-' * 20}")
    for thread in threads:
        title = truncate(thread.title or thread.first_user_message or thread.preview, title_width)
        cwd = truncate(thread.cwd, max(20, width - title_width - 42))
        print(
            f"{format_ms(thread.recency_at_ms):19}  "
            f"{thread.source[:6]:6}  "
            f"{short_id(thread.id):8}  "
            f"{title:{title_width}}  "
            f"{cwd}"
        )
    return 0


def browse_threads(args: argparse.Namespace) -> int:
    store = CodexStore()
    threads = store.load_threads(
        include_archived=args.all,
        limit=args.limit,
        query=args.query,
        source=args.source,
        cwd=cwd_filter(args),
    )
    if not threads:
        print("No sessions found.")
        return 0
    if not is_available():
        return list_threads(args)
    selection = choose_thread(threads, mode=args.mode)
    if not selection:
        return 0
    return handle_session_selection(selection, mode=args.mode)


def view_thread(args: argparse.Namespace) -> int:
    thread = CodexStore().resolve_thread(args.selector, include_archived=True)
    phases = set(args.phase) if getattr(args, "phase", None) else None
    color = sys.stdout.isatty() and not args.no_color
    output = render_thread(thread, mode=args.mode, phases=phases, color=color)
    if args.no_pager or not sys.stdout.isatty():
        print(output, end="")
    else:
        page(output)
    return 0


def preview_thread(args: argparse.Namespace) -> int:
    thread = CodexStore().resolve_thread(args.selector, include_archived=True)
    print(render_thread(thread, mode=args.mode, color=False))
    return 0


def files_thread(args: argparse.Namespace) -> int:
    thread = CodexStore().resolve_thread(args.selector, include_archived=True)
    hits = file_hits_for_thread(thread, mode=args.mode)
    if args.json:
        for hit in hits:
            print(
                json.dumps(
                    {
                        "path": hit.display_path,
                        "resolved_path": hit.resolved_path,
                        "line": hit.line,
                        "role": hit.role,
                        "count": hit.count,
                        "exists": hit.exists,
                        "context": hit.context,
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    if args.open:
        return open_file_from_hits(hits, editor=args.editor)
    print(render_file_hits(hits), end="")
    return 0


def search_threads(args: argparse.Namespace) -> int:
    needle = args.text
    threads = CodexStore().load_threads(
        include_archived=args.all,
        limit=None,
        source=args.source,
        cwd=cwd_filter(args),
    )
    matches: list[SearchMatch] = []
    needle_fold = needle.casefold()
    for thread in threads:
        metadata = " ".join([thread.title, thread.preview, thread.first_user_message, thread.cwd])
        thread_matches: list[SearchMatch] = []
        if not args.metadata_only:
            messages = filter_messages(read_messages(Path(thread.rollout_path)), args.mode)
            for message in messages:
                if needle_fold in message.text.casefold():
                    thread_matches.append(SearchMatch(thread, message.role, snippet(message.text, needle)))
        if args.metadata_only or not thread_matches:
            if needle_fold in metadata.casefold():
                thread_matches.insert(0, SearchMatch(thread, "meta", snippet(metadata, needle)))
        matches.extend(thread_matches)
        if args.limit and len(matches) >= args.limit:
            break
    if not matches:
        if not args.json:
            print("No matches.")
        return 1
    if args.json:
        print_search_matches_json(matches, limit=args.limit, mode=args.mode)
        return 0
    if args.open and is_available():
        selection = choose_search_match(matches[: args.limit or None], mode=args.mode)
        if not selection:
            return 0
        return handle_session_selection(selection, mode=args.mode)
    print_search_matches(matches, limit=args.limit, mode=args.mode)
    return 0


def print_search_matches(matches: list[SearchMatch], *, limit: int | None, mode: str) -> None:
    for match in matches[: limit or None]:
        thread = match.thread
        title = thread.title or thread.first_user_message or thread.preview
        print(f"{short_id(thread.id)}  {match.role:9}  {truncate(title, 64)}")
        print(f"  {match.snippet}")
        print(f"  cxp view {thread.id} --mode {mode}")


def print_search_matches_json(matches: list[SearchMatch], *, limit: int | None, mode: str) -> None:
    for match in matches[: limit or None]:
        thread = match.thread
        print(
            json.dumps(
                {
                    "id": thread.id,
                    "title": thread.title or thread.first_user_message or thread.preview,
                    "cwd": thread.cwd,
                    "source": thread.source,
                    "archived": thread.archived,
                    "updated_at": format_ms(thread.recency_at_ms),
                    "rollout_path": thread.rollout_path,
                    "role": match.role,
                    "snippet": match.snippet,
                    "mode": mode,
                },
                ensure_ascii=False,
            )
        )


def resume_thread(args: argparse.Namespace) -> int:
    store = CodexStore()
    if args.selector:
        thread = store.resolve_thread(args.selector)
        return exec_resume(thread.id)
    threads = store.load_threads(
        include_archived=False,
        limit=args.limit,
        query=args.query,
        source=args.source,
        cwd=cwd_filter(args),
    )
    if not is_available():
        print("Use a session id, or run this in a TTY with fzf installed.", file=sys.stderr)
        return 2
    selection = choose_thread(threads, mode="chat", allow_actions=False)
    if not selection:
        return 0
    return exec_resume(selection.value)


def handle_session_selection(selection: PickerSelection, *, mode: str) -> int:
    if selection.action == "resume":
        return exec_resume(selection.value)
    if selection.action == "view":
        return render_selected_thread(selection.value, mode=mode)
    if selection.action == "final":
        return render_selected_thread(selection.value, mode="final")
    if selection.action == "user":
        return render_selected_thread(selection.value, mode="user")
    if selection.action == "files":
        return files_thread(
            argparse.Namespace(selector=selection.value, mode=mode, json=False, open=False, editor=None)
        )
    if selection.action == "edit_file":
        return files_thread(
            argparse.Namespace(selector=selection.value, mode=mode, json=False, open=True, editor=None)
        )
    print(f"Unknown picker action: {selection.action}", file=sys.stderr)
    return 2


def render_selected_thread(selector: str, *, mode: str) -> int:
    return view_thread(
        argparse.Namespace(selector=selector, mode=mode, phase=None, no_pager=False, no_color=False)
    )


def exec_resume(session_id: str) -> int:
    command = [str(real_codex_bin()), "resume"]
    if not os.environ.get("CODEX_ALT_SCREEN"):
        command.append("--no-alt-screen")
    command.append(session_id)
    os.execv(str(real_codex_bin()), command)
    return 0


def show_path(args: argparse.Namespace) -> int:
    thread = CodexStore().resolve_thread(args.selector)
    print(thread.rollout_path)
    return 0


def preview_file(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    line = parse_line(args.line)
    if not path.is_file():
        print(f"File not found: {path}")
        return 1
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"Unable to read {path}: {exc}")
        return 1
    if not lines:
        print("(empty file)")
        return 0
    if line is None or line < 1:
        start = 1
        end = min(len(lines), 120)
    else:
        start = max(1, line - 8)
        end = min(len(lines), line + 12)
    for number in range(start, end + 1):
        marker = ">" if line == number else " "
        print(f"{number:>5} {marker} {lines[number - 1]}")
    return 0


def stats(_: argparse.Namespace) -> int:
    store = CodexStore()
    threads = store.load_threads(include_archived=True, limit=None)
    archived = sum(1 for thread in threads if thread.archived)
    by_source: dict[str, int] = {}
    for thread in threads:
        by_source[thread.source or "?"] = by_source.get(thread.source or "?", 0) + 1
    print(f"threads: {len(threads)}")
    print(f"archived: {archived}")
    for source, count in sorted(by_source.items(), key=lambda item: item[1], reverse=True):
        print(f"{source}: {count}")
    return 0


def install_shim(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    if target.exists() and not args.force:
        print(f"Refusing to replace existing shim target: {target}", file=sys.stderr)
        print("Pass --force after checking that this is the target you want to replace.", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    content = shim_script(real_codex=args.real_codex)
    target.write_text(content, encoding="utf-8")
    target.chmod(0o755)
    print(f"Installed CodexPlus shim at {target}")
    return 0


def shim_script(*, real_codex: str) -> str:
    return f"""#!/bin/sh
set -eu
case "${{1:-}}" in
  h|history)
    shift
    exec cxp browse "$@"
    ;;
  sessions|conversations)
    shift
    exec cxp list "$@"
    ;;
  view|show|assistant|answers|final|user|questions|search|grep|files|stats|path)
    exec cxp "$@"
    ;;
esac
exec {real_codex} "$@"
"""


def compress_placeholder(_: argparse.Namespace) -> int:
    print("Compression is intentionally not implemented in v0.1.")
    print("CodexPlus will add local summaries without rewriting Codex internal history.")
    return 2


def page(output: str) -> None:
    pager = os.environ.get("PAGER") or "less"
    if shutil.which(pager.split()[0]) is None:
        print(output, end="")
        return
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, prefix="cxp-history-", suffix=".txt") as handle:
        handle.write(output)
        temp_path = handle.name
    try:
        if pager == "less":
            subprocess.run(["less", "-R", temp_path], check=False)
        else:
            subprocess.run([pager, temp_path], check=False)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def open_file_from_hits(hits: list[FileHit], *, editor: str | None) -> int:
    if not hits:
        print("No file references found.", file=sys.stderr)
        return 1
    hit = choose_file(hits) if is_available() else hits[0]
    if hit is None:
        return 0
    return open_file(hit, editor=editor)


def open_file(hit: FileHit, *, editor: str | None) -> int:
    command = editor_command(editor or os.environ.get("EDITOR"), hit)
    if command is None:
        print(hit.resolved_path)
        return 0
    return subprocess.run(command, check=False).returncode


def editor_command(editor: str | None, hit: FileHit) -> list[str] | None:
    editor_parts = shlex.split(editor) if editor else default_editor_parts()
    if not editor_parts:
        return None
    path = hit.resolved_path
    line = hit.line
    editor_name = Path(editor_parts[0]).name
    if line is not None and editor_name in {"nvim", "vim", "vi"}:
        return [*editor_parts, f"+{line}", path]
    if line is not None and editor_name in {"code", "code-insiders", "codium"}:
        return [*editor_parts, "-g", f"{path}:{line}"]
    if line is not None and editor_name in {"emacs", "emacsclient"}:
        return [*editor_parts, f"+{line}", path]
    return [*editor_parts, path]


def default_editor_parts() -> list[str] | None:
    for candidate in ("nvim", "vim", "vi", "nano"):
        if shutil.which(candidate):
            return [candidate]
    return None


def parse_line(value: str | None) -> int | None:
    if not value or value == "-":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def snippet(text: str, needle: str, *, radius: int = 90) -> str:
    flat = one_line(text)
    pos = flat.casefold().find(needle.casefold())
    if pos < 0:
        return truncate(flat, radius * 2)
    start = max(0, pos - radius)
    end = min(len(flat), pos + len(needle) + radius)
    prefix = "..." if start else ""
    suffix = "..." if end < len(flat) else ""
    return prefix + flat[start:end] + suffix


def setattr_return(namespace: argparse.Namespace, key: str, value: object) -> argparse.Namespace:
    setattr(namespace, key, value)
    if not hasattr(namespace, "phase"):
        setattr(namespace, "phase", None)
    return namespace


if __name__ == "__main__":
    raise SystemExit(main())

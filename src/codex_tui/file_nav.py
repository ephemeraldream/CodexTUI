from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import ChatMessage, ThreadRow
from .transcript import filter_messages, one_line, read_messages, truncate


SOURCE_SUFFIXES = {
    ".bash",
    ".c",
    ".cc",
    ".cfg",
    ".cpp",
    ".cs",
    ".css",
    ".env",
    ".fish",
    ".go",
    ".gradle",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".kt",
    ".lock",
    ".md",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".sass",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}

KNOWN_FILENAMES = {
    ".dockerignore",
    ".env",
    ".gitignore",
    "AGENTS.md",
    "Cargo.lock",
    "Cargo.toml",
    "CHANGELOG.md",
    "Dockerfile",
    "Makefile",
    "README",
    "README.md",
    "go.mod",
    "go.sum",
    "package.json",
    "pnpm-lock.yaml",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "tailwind.config.js",
    "tsconfig.json",
    "vite.config.ts",
    "yarn.lock",
}

KNOWN_NAME_PATTERN = "|".join(re.escape(name) for name in sorted(KNOWN_FILENAMES, key=len, reverse=True))
PATH_REFERENCE_RE = re.compile(
    r"(?<![\w@+./~-])"
    r"(?P<path>"
    r"(?:~|/|\./|\../)?(?:[\w@+.-]+/)+[\w@+.-]+"
    r"|"
    rf"(?:{KNOWN_NAME_PATTERN})"
    r")"
    r"(?::(?P<line>\d+)|#L(?P<hash_line>\d+))?"
)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]\n]+\]\((?P<target>[^)\s]+)\)")
LINE_SUFFIX_RE = re.compile(r"^(?P<path>.*?)(?::(?P<line>\d+)|#L(?P<hash_line>\d+))?$")


@dataclass(frozen=True)
class FileHit:
    display_path: str
    resolved_path: str
    line: int | None
    role: str
    count: int
    context: str
    exists: bool


@dataclass
class _MutableHit:
    display_path: str
    resolved_path: str
    line: int | None
    role: str
    count: int
    context: str
    exists: bool


def file_hits_for_thread(thread: ThreadRow, *, mode: str = "chat") -> list[FileHit]:
    messages = filter_messages(read_messages(Path(thread.rollout_path)), mode)
    return collect_file_hits(messages, cwd=thread.cwd)


def collect_file_hits(messages: Iterable[ChatMessage], *, cwd: str = "") -> list[FileHit]:
    cwd_path = Path(cwd).expanduser() if cwd else None
    hits: dict[str, _MutableHit] = {}
    order: list[str] = []
    for message in messages:
        message_keys: set[str] = set()
        for raw_reference in iter_file_references(message.text):
            parsed = normalize_file_reference(raw_reference, cwd_path)
            if parsed is None:
                continue
            resolved, display, line = parsed
            key = str(resolved)
            if key in message_keys:
                continue
            message_keys.add(key)
            if key not in hits:
                hits[key] = _MutableHit(
                    display_path=display,
                    resolved_path=key,
                    line=line,
                    role=message.role,
                    count=1,
                    context=truncate(one_line(message.text), 180),
                    exists=resolved.is_file(),
                )
                order.append(key)
                continue
            hit = hits[key]
            hit.count += 1
            if hit.line is None and line is not None:
                hit.line = line
    return [
        FileHit(
            display_path=hits[key].display_path,
            resolved_path=hits[key].resolved_path,
            line=hits[key].line,
            role=hits[key].role,
            count=hits[key].count,
            context=hits[key].context,
            exists=hits[key].exists,
        )
        for key in sorted(order, key=lambda item: hits[item].display_path.casefold())
    ]


def iter_file_references(text: str) -> Iterable[str]:
    for match in MARKDOWN_LINK_RE.finditer(text):
        yield match.group("target")
    for match in PATH_REFERENCE_RE.finditer(text):
        path = match.group("path")
        line = match.group("line") or match.group("hash_line")
        yield f"{path}:{line}" if line else path


def normalize_file_reference(raw_value: str, cwd: Path | None) -> tuple[Path, str, int | None] | None:
    value = clean_reference(raw_value)
    if not value or "://" in value or any(char.isspace() for char in value):
        return None
    match = LINE_SUFFIX_RE.match(value)
    if match is None:
        return None
    path_text = match.group("path")
    if not looks_like_file_reference(path_text):
        return None
    line_value = match.group("line") or match.group("hash_line")
    line = int(line_value) if line_value else None
    original_path = Path(path_text).expanduser()
    if original_path.is_absolute():
        resolved = original_path
    elif cwd is not None:
        resolved = cwd / original_path
    else:
        resolved = original_path
    resolved = resolved.resolve(strict=False)
    display = display_path(resolved, cwd, fallback=path_text)
    return resolved, display, line


def clean_reference(value: str) -> str:
    clean = value.strip().strip("`'\"<>[](){}")
    while clean.endswith((",", ";")):
        clean = clean[:-1]
    if clean.endswith(".") and not any(clean.endswith(suffix) for suffix in SOURCE_SUFFIXES):
        clean = clean[:-1]
    return clean


def looks_like_file_reference(path_text: str) -> bool:
    if not path_text or path_text in {".", ".."}:
        return False
    name = Path(path_text).name
    if name in KNOWN_FILENAMES:
        return True
    return Path(name).suffix in SOURCE_SUFFIXES


def display_path(path: Path, cwd: Path | None, *, fallback: str) -> str:
    if cwd is not None:
        try:
            return str(path.relative_to(cwd.resolve(strict=False)))
        except ValueError:
            pass
    return fallback if not path.is_absolute() else str(path)


def render_file_hits(hits: list[FileHit]) -> str:
    if not hits:
        return "No file references found.\n"
    path_width = max(12, min(72, max(len(hit.display_path) for hit in hits)))
    lines = [f"{'path':{path_width}}  {'line':>5}  {'refs':>4}  {'status':8}  context"]
    lines.append(f"{'-' * path_width}  {'-' * 5}  {'-' * 4}  {'-' * 8}  {'-' * 24}")
    for hit in hits:
        line = str(hit.line) if hit.line is not None else "-"
        status = "ok" if hit.exists else "missing"
        lines.append(
            f"{truncate(hit.display_path, path_width):{path_width}}  "
            f"{line:>5}  "
            f"{hit.count:>4}  "
            f"{status:8}  "
            f"{hit.context}"
        )
    return "\n".join(lines) + "\n"

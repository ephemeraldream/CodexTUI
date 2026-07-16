from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .models import ThreadRow
from .paths import codex_home
from .transcript import clean_metadata_text, one_line, read_messages


class CodexStore:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or codex_home()

    def load_threads(
        self,
        *,
        include_archived: bool = False,
        limit: int | None = 80,
        query: str | None = None,
        source: str | None = None,
        cwd: str | None = None,
    ) -> list[ThreadRow]:
        db_path = self.state_db_path()
        if db_path is None:
            return self.scan_threads_from_files(
                include_archived=include_archived,
                limit=limit,
                query=query,
                source=source,
                cwd=cwd,
            )
        con = self.open_state_db(db_path)
        if con is None:
            return self.scan_threads_from_files(
                include_archived=include_archived,
                limit=limit,
                query=query,
                source=source,
                cwd=cwd,
            )
        clauses: list[str] = []
        params: list[object] = []
        if not include_archived:
            clauses.append("archived = 0")
        if source:
            clauses.append("source = ?")
            params.append(source)
        if query:
            like = f"%{query}%"
            clauses.append("(title LIKE ? OR preview LIKE ? OR first_user_message LIKE ? OR cwd LIKE ? OR id LIKE ?)")
            params.extend([like, like, like, like, like])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"""
            SELECT id, title, cwd, source, archived, rollout_path, created_at_ms,
                   updated_at_ms, recency_at_ms, preview, first_user_message
            FROM threads
            {where}
            ORDER BY recency_at_ms DESC, id DESC
        """
        needs_python_filter = bool(query or cwd)
        if limit is not None and not needs_python_filter:
            sql += " LIMIT ?"
            params.append(limit)
        try:
            rows = con.execute(sql, params).fetchall()
        finally:
            con.close()
        threads = [
            ThreadRow(
                id=str(row["id"] or ""),
                title=clean_metadata_text(str(row["title"] or "")),
                cwd=str(row["cwd"] or ""),
                source=str(row["source"] or ""),
                archived=bool(row["archived"]),
                rollout_path=str(row["rollout_path"] or ""),
                created_at_ms=safe_ms(row["created_at_ms"]),
                updated_at_ms=safe_ms(row["updated_at_ms"]),
                recency_at_ms=safe_ms(row["recency_at_ms"]),
                preview=clean_metadata_text(str(row["preview"] or "")),
                first_user_message=clean_metadata_text(str(row["first_user_message"] or "")),
            )
            for row in rows
        ]
        if needs_python_filter:
            threads = [
                thread
                for thread in threads
                if thread_matches_filters(thread, query=query, source=None, cwd=cwd)
            ]
        if limit is not None and needs_python_filter:
            threads = threads[:limit]
        return threads

    def resolve_thread(
        self,
        selector: str | None,
        *,
        include_archived: bool = True,
        cwd: str | None = None,
    ) -> ThreadRow:
        threads = self.load_threads(include_archived=include_archived, limit=None, cwd=cwd)
        if not threads:
            raise LookupError("No Codex sessions found.")
        if selector in (None, "", "last", "@last"):
            return threads[0]
        path = Path(str(selector)).expanduser()
        if path.exists():
            return thread_from_path(path)
        exact = [thread for thread in threads if thread.id == selector]
        if exact:
            return exact[0]
        prefix = [thread for thread in threads if thread.id.startswith(str(selector))]
        if len(prefix) == 1:
            return prefix[0]
        query = str(selector).casefold()
        title_matches = [
            thread
            for thread in threads
            if query in thread.title.casefold()
            or query in thread.first_user_message.casefold()
            or query in thread.preview.casefold()
        ]
        if len(title_matches) == 1:
            return title_matches[0]
        if prefix:
            raise LookupError(f"Session selector is ambiguous: {selector} matched {len(prefix)} ids.")
        if title_matches:
            raise LookupError(f"Session selector is ambiguous: {selector} matched {len(title_matches)} titles.")
        raise LookupError(f"Session not found: {selector}")

    def state_db_path(self) -> Path | None:
        preferred = self.home / "state_5.sqlite"
        if preferred.exists():
            return preferred
        candidates = sorted(self.home.glob("state_*.sqlite"), key=lambda path: path.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    @staticmethod
    def open_state_db(path: Path) -> sqlite3.Connection | None:
        uri = f"file:{path}?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2.0)
        except sqlite3.Error:
            return None
        con.row_factory = sqlite3.Row
        return con

    def scan_threads_from_files(
        self,
        *,
        include_archived: bool = False,
        limit: int | None = 80,
        query: str | None = None,
        source: str | None = None,
        cwd: str | None = None,
    ) -> list[ThreadRow]:
        roots = [self.home / "sessions"]
        if include_archived:
            roots.append(self.home / "archived_sessions")
        files: list[Path] = []
        for root in roots:
            if root.exists():
                files.extend(root.rglob("*.jsonl"))
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        threads: list[ThreadRow] = []
        for path in files:
            meta = read_session_meta(path)
            session_id = meta.get("id") or id_from_path(path)
            first = first_user_message(path)
            title = clean_metadata_text(meta.get("thread_name") or first or path.name)
            mtime_ms = int(path.stat().st_mtime * 1000)
            threads.append(
                ThreadRow(
                    id=session_id,
                    title=title,
                    cwd=meta.get("cwd", ""),
                    source=meta.get("source", ""),
                    archived="archived_sessions" in path.parts,
                    rollout_path=str(path),
                    created_at_ms=mtime_ms,
                    updated_at_ms=mtime_ms,
                    recency_at_ms=mtime_ms,
                    preview="",
                    first_user_message=first,
                )
            )
            if not thread_matches_filters(threads[-1], query=query, source=source, cwd=cwd):
                threads.pop()
                continue
            if limit is not None and len(threads) >= limit:
                break
        return threads


def safe_ms(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def read_session_meta(path: Path) -> dict[str, str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "session_meta":
                    continue
                payload = record.get("payload") or {}
                return {str(k): str(v) for k, v in payload.items() if isinstance(v, (str, int))}
    except OSError:
        return {}
    return {}


def id_from_path(path: Path) -> str:
    match = re.search(r"(019[0-9a-f-]{32,})", path.name)
    return match.group(1) if match else path.stem


def first_user_message(path: Path) -> str:
    for message in read_messages(path):
        if message.role == "user":
            return one_line(message.text)
    return ""


def thread_from_path(path: Path) -> ThreadRow:
    meta = read_session_meta(path)
    session_id = meta.get("id") or id_from_path(path)
    mtime_ms = int(path.stat().st_mtime * 1000)
    first = first_user_message(path)
    return ThreadRow(
        id=session_id,
        title=first or path.name,
        cwd=meta.get("cwd", ""),
        source=meta.get("source", ""),
        archived="archived_sessions" in path.parts,
        rollout_path=str(path),
        created_at_ms=mtime_ms,
        updated_at_ms=mtime_ms,
        recency_at_ms=mtime_ms,
        preview="",
        first_user_message=first,
    )


def thread_matches_filters(
    thread: ThreadRow,
    *,
    query: str | None = None,
    source: str | None = None,
    cwd: str | None = None,
) -> bool:
    if source and thread.source != source:
        return False
    if cwd and not cwd_matches_filter(thread.cwd, cwd):
        return False
    if query:
        needle = query.casefold()
        haystack = " ".join([thread.title, thread.preview, thread.first_user_message, thread.cwd, thread.id])
        if needle not in haystack.casefold():
            return False
    return True


def cwd_matches_filter(thread_cwd: str, cwd_filter: str) -> bool:
    if not thread_cwd:
        return False
    try:
        thread_path = Path(thread_cwd).expanduser().resolve(strict=False)
        filter_path = Path(cwd_filter).expanduser().resolve(strict=False)
    except OSError:
        thread_path = None
        filter_path = None
    if thread_path is not None and filter_path is not None:
        if thread_path == filter_path or filter_path in thread_path.parents:
            return True
    if looks_like_path_filter(cwd_filter):
        return False
    return cwd_filter.casefold() in thread_cwd.casefold()


def looks_like_path_filter(value: str) -> bool:
    return value in {".", ".."} or value.startswith(("~", "/", "./", "../")) or "/" in value or "\\" in value

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import ThreadRow
from .paths import codex_home
from .transcript import clean_metadata_text, one_line, read_messages


@dataclass(frozen=True)
class StateDbLoadResult:
    threads: list[ThreadRow]
    has_readable_state_threads: bool


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
        if limit == 0:
            return []
        fallback_kwargs = {
            "include_archived": include_archived,
            "limit": limit,
            "query": query,
            "source": source,
            "cwd": cwd,
        }
        db_paths = self.state_db_paths()
        deferred_readable_threads: list[ThreadRow] = []
        for index, db_path in enumerate(db_paths):
            result = self._load_threads_from_state_db(
                db_path,
                include_archived=include_archived,
                limit=limit,
                query=query,
                source=source,
                cwd=cwd,
            )
            if result is not None:
                if index < len(db_paths) - 1 and not result.has_readable_state_threads:
                    deferred_readable_threads = merge_thread_lists(
                        deferred_readable_threads,
                        [thread for thread in result.threads if thread_rollout_readable(thread)],
                    )
                    continue
                threads = result.threads
                if deferred_readable_threads:
                    threads = merge_thread_lists(threads, deferred_readable_threads)
                    if limit is not None and limit >= 0:
                        threads = threads[:limit]
                return threads
        if deferred_readable_threads:
            if limit is not None and limit >= 0:
                return deferred_readable_threads[:limit]
            return deferred_readable_threads
        return self.scan_threads_from_files(**fallback_kwargs)

    def _load_threads_from_state_db(
        self,
        db_path: Path,
        *,
        include_archived: bool,
        limit: int | None,
        query: str | None,
        source: str | None,
        cwd: str | None,
    ) -> StateDbLoadResult | None:
        con = self.open_state_db(db_path)
        if con is None:
            return None
        clauses: list[str] = []
        params: list[object] = []
        if not include_archived:
            clauses.append("archived = 0")
        if source:
            clauses.append("source = ?")
            params.append(source)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        base_sql = f"""
            SELECT id, title, cwd, source, archived, rollout_path, created_at_ms,
                   updated_at_ms, recency_at_ms, preview, first_user_message
            FROM threads
            {where}
            ORDER BY recency_at_ms DESC, id DESC
        """
        needs_python_filter = bool(query or cwd)
        use_sql_limit = limit is not None and limit >= 0 and not needs_python_filter
        sql = base_sql
        sql_params = list(params)
        if use_sql_limit:
            sql += " LIMIT ?"
            sql_params.append(limit)
        reloaded_without_sql_limit = False
        try:
            rows = con.execute(sql, sql_params).fetchall()
            db_threads = [thread_from_state_row(row) for row in rows]
            if use_sql_limit and any(not thread_rollout_readable(thread) for thread in db_threads):
                rows = con.execute(base_sql, params).fetchall()
                db_threads = [thread_from_state_row(row) for row in rows]
                reloaded_without_sql_limit = True
        except sqlite3.Error:
            return None
        finally:
            con.close()
        if not db_threads:
            return None
        db_has_unreadable_rollout = any(not thread_rollout_readable(thread) for thread in db_threads)
        threads = db_threads
        if needs_python_filter:
            threads = [
                thread
                for thread in threads
                if thread_matches_filters(thread, query=query, source=None, cwd=cwd)
            ]
        db_filtered_empty = needs_python_filter and not threads
        readable_threads = [thread for thread in threads if thread_rollout_readable(thread)]
        merged_with_fallback = False
        if db_has_unreadable_rollout or db_filtered_empty:
            fallback_threads = self.scan_threads_from_files(
                include_archived=include_archived,
                limit=None,
                query=query,
                source=source,
                cwd=cwd,
            )
            if fallback_threads:
                threads = merge_thread_lists(readable_threads, fallback_threads)
                merged_with_fallback = True
            elif readable_threads:
                threads = readable_threads
                merged_with_fallback = True
            elif db_filtered_empty:
                return None
        if limit is not None and limit >= 0 and (
            needs_python_filter or merged_with_fallback or reloaded_without_sql_limit
        ):
            threads = threads[:limit]
        return StateDbLoadResult(threads=threads, has_readable_state_threads=bool(readable_threads))

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
        paths = self.state_db_paths()
        return paths[0] if paths else None

    def state_db_paths(self) -> list[Path]:
        candidates = list(self.home.glob("state_*.sqlite"))
        return sorted(candidates, key=state_db_sort_key, reverse=True)

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
        if limit == 0:
            return []
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


def state_db_sort_key(path: Path) -> tuple[int, float, str]:
    match = re.fullmatch(r"state_(\d+)\.sqlite", path.name)
    version = int(match.group(1)) if match else -1
    return (version, path.stat().st_mtime, path.name)


def thread_from_state_row(row: sqlite3.Row) -> ThreadRow:
    rollout_path = str(row["rollout_path"] or "")
    title = clean_metadata_text(str(row["title"] or ""))
    preview = clean_metadata_text(str(row["preview"] or ""))
    first = clean_metadata_text(str(row["first_user_message"] or ""))
    if rollout_path and (not title or not first):
        rollout_first = first_user_message(Path(rollout_path))
        if rollout_first:
            first = first or rollout_first
            title = title or rollout_first
    if not title:
        title = preview or (Path(rollout_path).name if rollout_path else "")
    return ThreadRow(
        id=str(row["id"] or ""),
        title=title,
        cwd=str(row["cwd"] or ""),
        source=str(row["source"] or ""),
        archived=bool(row["archived"]),
        rollout_path=rollout_path,
        created_at_ms=safe_ms(row["created_at_ms"]),
        updated_at_ms=safe_ms(row["updated_at_ms"]),
        recency_at_ms=safe_ms(row["recency_at_ms"]),
        preview=preview,
        first_user_message=first,
    )


def thread_rollout_readable(thread: ThreadRow) -> bool:
    if not thread.rollout_path:
        return False
    try:
        return Path(thread.rollout_path).is_file()
    except OSError:
        return False


def merge_thread_lists(primary: list[ThreadRow], fallback: list[ThreadRow]) -> list[ThreadRow]:
    by_key: dict[str, ThreadRow] = {}
    for thread in [*primary, *fallback]:
        key = thread.id or thread.rollout_path
        if key and key not in by_key:
            by_key[key] = thread
    return sorted(by_key.values(), key=lambda thread: (thread.recency_at_ms, thread.id), reverse=True)


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

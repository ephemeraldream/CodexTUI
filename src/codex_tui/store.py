from __future__ import annotations

import datetime as dt
import json
import math
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
        reloaded_without_sql_limit = False
        try:
            columns = state_db_thread_columns(con)
            select_columns = state_db_thread_select_columns(columns)
            if select_columns is None:
                return None
            clauses: list[str] = []
            params: list[object] = []
            if not include_archived and "archived" in columns:
                clauses.append(state_db_unarchived_clause())
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            base_sql = f"""
                SELECT {", ".join(select_columns)}
                FROM threads
                {where}
                ORDER BY recency_at_ms DESC, id DESC
            """
            needs_python_filter = bool(query or source or cwd)
            use_sql_limit = limit is not None and limit >= 0 and not needs_python_filter
            sql = base_sql
            sql_params = list(params)
            if use_sql_limit:
                sql += " LIMIT ?"
                sql_params.append(limit)
            rows = con.execute(sql, sql_params).fetchall()
            db_threads = [thread_from_state_row(row, base_dir=db_path.parent) for row in rows]
            if use_sql_limit and any(not thread_rollout_readable(thread) for thread in db_threads):
                rows = con.execute(base_sql, params).fetchall()
                db_threads = [thread_from_state_row(row, base_dir=db_path.parent) for row in rows]
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
                if thread_matches_filters(thread, query=query, source=source, cwd=cwd)
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
        elif readable_threads:
            if needs_python_filter:
                file_threads = self.scan_threads_from_files(
                    include_archived=include_archived,
                    limit=None,
                    query=query,
                    source=source,
                    cwd=cwd,
                )
            else:
                newest_state_recency_ms = max(thread.recency_at_ms for thread in readable_threads)
                file_threads = self.scan_threads_from_files(
                    include_archived=include_archived,
                    limit=None,
                    query=query,
                    source=source,
                    cwd=cwd,
                    newer_than_ms=newest_state_recency_ms,
                )
            unindexed_file_threads: list[ThreadRow] = []
            if file_threads:
                indexed_keys = self.indexed_state_thread_keys()
                unindexed_file_threads = [
                    thread
                    for thread in file_threads
                    if thread_key_values(thread).isdisjoint(indexed_keys)
                ]
            if unindexed_file_threads:
                threads = merge_thread_lists(threads, unindexed_file_threads)
                merged_with_fallback = True
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
        selectable_threads = [(thread, thread_selector_ids(thread)) for thread in threads]
        exact = [thread for thread, ids in selectable_threads if selector in ids]
        if exact:
            return exact[0]
        prefix = [
            thread
            for thread, ids in selectable_threads
            if any(thread_id.startswith(str(selector)) for thread_id in ids)
        ]
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

    def indexed_state_thread_keys(self) -> set[str]:
        keys: set[str] = set()
        for db_path in self.state_db_paths():
            con = self.open_state_db(db_path)
            if con is None:
                continue
            try:
                columns = state_db_thread_columns(con)
                if "id" not in columns:
                    continue
                rollout_column = "rollout_path" if "rollout_path" in columns else "'' AS rollout_path"
                rows = con.execute(f"SELECT id, {rollout_column} FROM threads").fetchall()
            except sqlite3.Error:
                continue
            finally:
                con.close()
            for row in rows:
                thread_id = state_db_text_value(row["id"])
                rollout_path = state_row_rollout_path(row["rollout_path"], base_dir=db_path.parent)
                keys.update(state_thread_key_values(thread_id, rollout_path))
        return keys

    def scan_threads_from_files(
        self,
        *,
        include_archived: bool = False,
        limit: int | None = 80,
        query: str | None = None,
        source: str | None = None,
        cwd: str | None = None,
        newer_than_ms: int | None = None,
    ) -> list[ThreadRow]:
        if limit == 0:
            return []
        roots = [self.home / "sessions"]
        if include_archived:
            roots.append(self.home / "archived_sessions")
        files: list[tuple[int, Path]] = []
        for root in roots:
            if root.exists():
                for path in root.rglob("*.jsonl"):
                    try:
                        if not path.is_file():
                            continue
                        mtime_ms = int(path.stat().st_mtime * 1000)
                    except OSError:
                        continue
                    if newer_than_ms is not None and mtime_ms <= newer_than_ms:
                        continue
                    files.append((mtime_ms, path))
        files.sort(key=lambda item: item[0], reverse=True)
        threads: list[ThreadRow] = []
        for mtime_ms, path in files:
            meta = read_session_meta(path)
            session_id = meta.get("id") or id_from_path(path)
            first = first_user_message(path)
            title = clean_metadata_text(meta.get("thread_name") or first or path.name)
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
    if isinstance(value, str):
        clean = value.strip()
        if not clean:
            return 0
        try:
            return int(clean)
        except ValueError:
            pass
        try:
            parsed_float = float(clean)
        except ValueError:
            pass
        else:
            if math.isfinite(parsed_float):
                return int(parsed_float)
        try:
            parsed = dt.datetime.fromisoformat(clean.replace("Z", "+00:00"))
        except ValueError:
            return 0
        return int(parsed.timestamp() * 1000)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def state_db_text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw_value = bytes(value)
        if not raw_value:
            return ""
        try:
            return raw_value.decode("utf-8").rstrip("\x00")
        except UnicodeDecodeError:
            return ""
    return str(value).rstrip("\x00")


def state_db_sort_key(path: Path) -> tuple[int, float, str]:
    match = re.fullmatch(r"state_(\d+)\.sqlite", path.name)
    version = int(match.group(1)) if match else -1
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    return (version, mtime, path.name)


def state_db_thread_columns(con: sqlite3.Connection) -> set[str]:
    return {str(row["name"]) for row in con.execute("PRAGMA table_info(threads)").fetchall()}


def state_db_thread_select_columns(columns: set[str]) -> list[str] | None:
    if "id" not in columns:
        return None
    return [
        "id",
        select_text_column(columns, "title"),
        select_text_column(columns, "cwd"),
        select_source_column(columns),
        select_int_column(columns, "archived"),
        select_text_column(columns, "rollout_path"),
        select_timestamp_ms_column(columns, "created_at_ms", "created_at"),
        select_timestamp_ms_column(columns, "updated_at_ms", "updated_at"),
        select_recency_at_ms_column(columns),
        select_text_column(columns, "preview"),
        select_text_column(columns, "first_user_message"),
    ]


def state_db_source_column(columns: set[str]) -> str | None:
    if "source" in columns:
        return "source"
    if "thread_source" in columns:
        return "thread_source"
    return None


def select_source_column(columns: set[str]) -> str:
    column = state_db_source_column(columns)
    if column is None:
        return "'' AS source"
    if column == "source":
        return "source"
    return f"{column} AS source"


def select_text_column(columns: set[str], column: str) -> str:
    if column in columns:
        return column
    return f"'' AS {column}"


def select_int_column(columns: set[str], column: str) -> str:
    if column in columns:
        return column
    return f"0 AS {column}"


def select_timestamp_ms_column(columns: set[str], ms_column: str, seconds_column: str) -> str:
    if ms_column in columns:
        return normalize_state_timestamp_sql(ms_column, ms_column, "ms")
    if seconds_column in columns:
        return normalize_state_timestamp_sql(seconds_column, ms_column, "seconds")
    return f"0 AS {ms_column}"


def select_recency_at_ms_column(columns: set[str]) -> str:
    if "recency_at_ms" in columns:
        return normalize_state_timestamp_sql("recency_at_ms", "recency_at_ms", "ms")
    if "recency_at" in columns:
        return normalize_state_timestamp_sql("recency_at", "recency_at_ms", "seconds")
    if "updated_at_ms" in columns:
        return normalize_state_timestamp_sql("updated_at_ms", "recency_at_ms", "ms")
    if "updated_at" in columns:
        return normalize_state_timestamp_sql("updated_at", "recency_at_ms", "seconds")
    if "created_at_ms" in columns:
        return normalize_state_timestamp_sql("created_at_ms", "recency_at_ms", "ms")
    if "created_at" in columns:
        return normalize_state_timestamp_sql("created_at", "recency_at_ms", "seconds")
    return "0 AS recency_at_ms"


def normalize_state_timestamp_sql(column: str, alias: str, numeric_unit: str) -> str:
    numeric_expr = f"CAST(CAST({column} AS REAL) AS INTEGER)"
    if numeric_unit == "seconds":
        numeric_expr = f"CAST(CAST({column} AS REAL) * 1000 AS INTEGER)"
    text_value = f"trim(CAST({column} AS TEXT))"
    digits = f"({text_value} GLOB '[0-9]*' AND {text_value} NOT GLOB '*[^0-9]*')"
    decimal = (
        f"({text_value} GLOB '[0-9]*.[0-9]*' "
        f"AND {text_value} NOT GLOB '*[^0-9.]*' "
        f"AND length({text_value}) - length(replace({text_value}, '.', '')) = 1)"
    )
    numeric_text = f"(({digits}) OR ({decimal}))"
    parsed_iso = f"CAST(strftime('%s', {column}) AS INTEGER) * 1000"
    return f"""
        CASE
            WHEN {column} IS NULL THEN 0
            WHEN typeof({column}) IN ('integer', 'real') THEN {numeric_expr}
            WHEN {numeric_text} THEN {numeric_expr}
            WHEN strftime('%s', {column}) IS NOT NULL THEN {parsed_iso}
            ELSE 0
        END AS {alias}
    """


def state_db_unarchived_clause() -> str:
    archived_text = "lower(trim(CAST(archived AS TEXT)))"
    false_values = "'', '0', 'false', 'no', 'off'"
    binary_zero = "(typeof(archived) = 'blob' AND replace(hex(archived), '00', '') = '')"
    numeric_zero_text = (
        f"({archived_text} GLOB '*0*' "
        f"AND {archived_text} NOT GLOB '*[^0-9.+-e]*' "
        f"AND ({archived_text} GLOB '[0-9]*' "
        f"OR {archived_text} GLOB '+[0-9]*' "
        f"OR {archived_text} GLOB '-[0-9]*' "
        f"OR {archived_text} GLOB '.[0-9]*' "
        f"OR {archived_text} GLOB '+.[0-9]*' "
        f"OR {archived_text} GLOB '-.[0-9]*') "
        f"AND CAST({archived_text} AS REAL) = 0)"
    )
    return (
        "(archived IS NULL "
        "OR (typeof(archived) IN ('integer', 'real') AND CAST(archived AS REAL) = 0) "
        f"OR {archived_text} IN ({false_values}) "
        f"OR {binary_zero} "
        f"OR {numeric_zero_text})"
    )


def state_db_archived_bool(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw_value = bytes(value)
        if not raw_value or all(byte == 0 for byte in raw_value):
            return False
        try:
            value = raw_value.decode("utf-8")
        except UnicodeDecodeError:
            return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"", "0", "false", "no", "off"}:
            return False
        try:
            if any(character.isdigit() for character in normalized) and float(normalized) == 0:
                return False
        except ValueError:
            pass
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def thread_from_state_row(row: sqlite3.Row, *, base_dir: Path | None = None) -> ThreadRow:
    rollout_path = state_row_rollout_path(row["rollout_path"], base_dir=base_dir)
    session_id = state_db_text_value(row["id"])
    title = clean_metadata_text(state_db_text_value(row["title"]))
    cwd = state_db_text_value(row["cwd"])
    source = state_db_text_value(row["source"])
    preview = clean_metadata_text(state_db_text_value(row["preview"]))
    first = clean_metadata_text(state_db_text_value(row["first_user_message"]))
    if rollout_path and (not session_id or not cwd or not source):
        rollout_meta = read_session_meta(Path(rollout_path))
        session_id = session_id or rollout_meta.get("id", "") or id_from_path(Path(rollout_path))
        cwd = cwd or rollout_meta.get("cwd", "")
        source = source or rollout_meta.get("source", "")
    if rollout_path and (not title or not first):
        rollout_first = first_user_message(Path(rollout_path))
        if rollout_first:
            first = first or rollout_first
            title = title or rollout_first
    if not title:
        title = preview or (Path(rollout_path).name if rollout_path else "")
    return ThreadRow(
        id=session_id,
        title=title,
        cwd=cwd,
        source=source,
        archived=state_db_archived_bool(row["archived"]),
        rollout_path=rollout_path,
        created_at_ms=safe_ms(row["created_at_ms"]),
        updated_at_ms=safe_ms(row["updated_at_ms"]),
        recency_at_ms=safe_ms(row["recency_at_ms"]),
        preview=preview,
        first_user_message=first,
    )


def state_row_rollout_path(value: object, *, base_dir: Path | None = None) -> str:
    raw_path = state_db_text_value(value)
    if not raw_path:
        return ""
    path = Path(raw_path).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    return str(path)


def thread_rollout_readable(thread: ThreadRow) -> bool:
    if not thread.rollout_path:
        return False
    try:
        return Path(thread.rollout_path).is_file()
    except (OSError, ValueError):
        return False


def merge_thread_lists(primary: list[ThreadRow], fallback: list[ThreadRow]) -> list[ThreadRow]:
    merged: list[ThreadRow] = []
    seen_keys: set[str] = set()
    for thread in [*primary, *fallback]:
        keys = thread_key_values(thread)
        if not keys:
            continue
        if not keys.isdisjoint(seen_keys):
            continue
        seen_keys.update(keys)
        merged.append(thread)
    return sorted(merged, key=lambda thread: (thread.recency_at_ms, thread.id), reverse=True)


def thread_key_values(thread: ThreadRow) -> set[str]:
    return state_thread_key_values(thread.id, thread.rollout_path)


def thread_selector_ids(thread: ThreadRow) -> set[str]:
    ids = {thread.id} if thread.id else set()
    if not thread.rollout_path:
        return ids
    rollout_path = Path(thread.rollout_path)
    meta_id = read_session_meta(rollout_path).get("id", "")
    if meta_id:
        ids.add(meta_id)
    path_id = id_from_path(rollout_path)
    if path_id:
        ids.add(path_id)
    return ids


def state_thread_key_values(thread_id: str, rollout_path: str) -> set[str]:
    keys: set[str] = set()
    if thread_id:
        keys.add(f"id:{thread_id}")
    if rollout_path:
        keys.add(f"path:{rollout_path}")
    return keys


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
    except (OSError, UnicodeDecodeError, ValueError):
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

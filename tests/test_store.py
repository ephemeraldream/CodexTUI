from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import path_bootstrap  # noqa: F401

from codex_plus.store import CodexStore


FIXTURES = Path(__file__).parent / "fixtures"


class StoreTests(unittest.TestCase):
    def test_scan_threads_from_files_without_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            session_dir = home / "sessions" / "2026" / "07" / "10"
            session_dir.mkdir(parents=True)
            target = session_dir / "rollout-2026-07-10T10-00-00-019f-test-basic.jsonl"
            target.write_text((FIXTURES / "rollout-basic.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
            threads = CodexStore(home).load_threads()
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0].id, "019f-test-basic")
        self.assertEqual(threads[0].cwd, "/tmp/project")
        self.assertEqual(threads[0].source, "cli")

    def test_scan_threads_from_files_applies_filters_before_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            other = write_session(
                home,
                "sessions",
                "019f-test-other",
                cwd="/tmp/other",
                source="vscode",
                user_message="Unrelated session",
            )
            project = write_session(
                home,
                "sessions",
                "019f-test-project",
                cwd="/tmp/project",
                source="cli",
                user_message="Find the project bug",
            )
            archived = write_session(
                home,
                "archived_sessions",
                "019f-test-archived",
                cwd="/tmp/project",
                source="cli",
                user_message="Archived project bug",
            )
            os.utime(other, (300, 300))
            os.utime(project, (200, 200))
            os.utime(archived, (100, 100))

            threads = CodexStore(home).load_threads(
                include_archived=False,
                limit=1,
                query="project bug",
                source="cli",
                cwd="/tmp/project",
            )
            archived_threads = CodexStore(home).load_threads(
                include_archived=True,
                limit=None,
                query="archived",
            )

        self.assertEqual([thread.id for thread in threads], ["019f-test-project"])
        self.assertEqual([thread.id for thread in archived_threads], ["019f-test-archived"])

    def test_sqlite_metadata_cleans_autonomous_wrapper_before_query_filtering(self) -> None:
        prompt = autonomous_prompt("Ship a keyboard-only CLI wrapper.")
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            db_path = home / "state_5.sqlite"
            con = sqlite3.connect(db_path)
            try:
                con.execute(
                    """
                    CREATE TABLE threads (
                        id TEXT,
                        title TEXT,
                        cwd TEXT,
                        source TEXT,
                        archived INTEGER,
                        rollout_path TEXT,
                        created_at_ms INTEGER,
                        updated_at_ms INTEGER,
                        recency_at_ms INTEGER,
                        preview TEXT,
                        first_user_message TEXT
                    )
                    """
                )
                con.execute(
                    """
                    INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "019f-test-autonomous",
                        prompt,
                        "/tmp/project",
                        "cli",
                        0,
                        "/tmp/project/session.jsonl",
                        1783677600000,
                        1783677605000,
                        1783677605000,
                        prompt,
                        prompt,
                    ),
                )
                con.commit()
            finally:
                con.close()

            objective_matches = CodexStore(home).load_threads(query="keyboard-only")
            boilerplate_matches = CodexStore(home).load_threads(query="This is iteration")

        self.assertEqual([thread.id for thread in objective_matches], ["019f-test-autonomous"])
        self.assertEqual(objective_matches[0].title, "Ship a keyboard-only CLI wrapper.")
        self.assertEqual(boilerplate_matches, [])


def write_session(
    home: Path,
    root_name: str,
    session_id: str,
    *,
    cwd: str,
    source: str,
    user_message: str,
) -> Path:
    session_dir = home / root_name / "2026" / "07" / "10"
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"rollout-2026-07-10T10-00-00-{session_id}.jsonl"
    records = [
        {
            "timestamp": "2026-07-10T10:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": cwd, "source": source},
        },
        {
            "timestamp": "2026-07-10T10:00:01.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": user_message, "images": []},
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def autonomous_prompt(objective: str) -> str:
    return (
        "You are working autonomously towards an objective given below.\n"
        "This is iteration 7. Each iteration aims to make an incremental step forward.\n\n"
        "## Instructions\n\n"
        "1. Read notes first.\n\n"
        "## Output\n\n"
        "- success\n\n"
        "## Objective\n\n"
        f"{objective}"
    )


if __name__ == "__main__":
    unittest.main()

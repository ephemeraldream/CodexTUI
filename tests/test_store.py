from __future__ import annotations

import json
import os
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


if __name__ == "__main__":
    unittest.main()

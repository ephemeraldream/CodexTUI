from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()

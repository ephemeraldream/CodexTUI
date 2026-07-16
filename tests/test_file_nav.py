from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import path_bootstrap  # noqa: F401

from codex_tui.file_nav import collect_file_hits
from codex_tui.models import ChatMessage


FIXTURES = Path(__file__).parent / "fixtures"


class FileNavTests(unittest.TestCase):
    def test_collect_file_hits_dedupes_relative_and_absolute_paths(self) -> None:
        messages = [
            ChatMessage("2026-07-10T11:00:01.000Z", "user", "", "Open `src/app.py:7` and pyproject.toml."),
            ChatMessage(
                "2026-07-10T11:00:02.000Z",
                "assistant",
                "commentary",
                "Changed [src/app.py](/tmp/project/src/app.py:42) and tests/test_app.py.",
            ),
        ]

        hits = collect_file_hits(messages, cwd="/tmp/project")

        self.assertEqual([hit.display_path for hit in hits], ["pyproject.toml", "src/app.py", "tests/test_app.py"])
        app_hit = next(hit for hit in hits if hit.display_path == "src/app.py")
        self.assertEqual(app_hit.count, 2)
        self.assertEqual(app_hit.line, 7)

    def test_files_command_lists_clean_transcript_file_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            session_dir = home / "sessions" / "2026" / "07" / "10"
            session_dir.mkdir(parents=True)
            target = session_dir / "rollout-2026-07-10T11-00-00-019f-test-files.jsonl"
            target.write_text((FIXTURES / "rollout-files.jsonl").read_text(encoding="utf-8"), encoding="utf-8")

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "files", "last"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )

        self.assertIn("src/app.py", result.stdout)
        self.assertIn("tests/test_app.py", result.stdout)
        self.assertIn("pyproject.toml", result.stdout)
        self.assertNotIn("src/hidden_tool_call.py", result.stdout)


if __name__ == "__main__":
    unittest.main()

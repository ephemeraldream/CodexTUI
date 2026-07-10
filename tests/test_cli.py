from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import path_bootstrap  # noqa: F401

from codex_plus.cli import handle_session_selection
from codex_plus.fzf import PickerSelection


FIXTURES = Path(__file__).parent / "fixtures"


class CliTests(unittest.TestCase):
    def test_version_command(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [sys.executable, "-m", "codex_plus", "--version"],
            cwd=os.getcwd(),
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("CodexPlus 0.1.0", result.stdout)

    def test_help_hides_internal_preview_commands(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [sys.executable, "-m", "codex_plus", "--help"],
            cwd=os.getcwd(),
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("files", result.stdout)
        self.assertNotIn("==SUPPRESS==", result.stdout)
        self.assertNotIn("file-preview", result.stdout)

    def test_compress_placeholder_is_explicitly_not_implemented(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [sys.executable, "-m", "codex_plus", "compress"],
            cwd=os.getcwd(),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("not implemented", result.stdout)

    def test_search_open_falls_back_to_clean_printed_matches_without_tty(self) -> None:
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
                [sys.executable, "-m", "codex_plus", "search", "src/app.py", "--open"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        self.assertIn("019f-tes", result.stdout)
        self.assertIn("src/app.py", result.stdout)
        self.assertIn("cxp view 019f-test-files --mode chat", result.stdout)

    def test_search_ignores_tool_call_payloads(self) -> None:
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
                [sys.executable, "-m", "codex_plus", "search", "hidden_tool_call.py"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("No matches.", result.stdout)

    @patch("codex_plus.cli.view_thread")
    def test_picker_view_action_renders_clean_transcript_instead_of_resuming(self, view_mock) -> None:
        view_mock.return_value = 0

        result = handle_session_selection(PickerSelection("view", "019f-test-basic"), mode="assistant")

        self.assertEqual(result, 0)
        args = view_mock.call_args.args[0]
        self.assertEqual(args.selector, "019f-test-basic")
        self.assertEqual(args.mode, "assistant")

    @patch("codex_plus.cli.files_thread")
    def test_picker_files_action_lists_files_without_opening_editor(self, files_mock) -> None:
        files_mock.return_value = 0

        result = handle_session_selection(PickerSelection("files", "019f-test-basic"), mode="chat")

        self.assertEqual(result, 0)
        args = files_mock.call_args.args[0]
        self.assertEqual(args.selector, "019f-test-basic")
        self.assertEqual(args.mode, "chat")
        self.assertFalse(args.open)


if __name__ == "__main__":
    unittest.main()

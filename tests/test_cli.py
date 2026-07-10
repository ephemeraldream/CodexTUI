from __future__ import annotations

import json
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

    def test_search_json_outputs_clean_structured_matches(self) -> None:
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
                [sys.executable, "-m", "codex_plus", "search", "src/app.py", "--json"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["role"] for row in rows}, {"user", "assistant"})
        self.assertEqual({row["id"] for row in rows}, {"019f-test-files"})
        self.assertTrue(all(row["mode"] == "chat" for row in rows))
        self.assertTrue(any(row["snippet"] == "Please inspect `src/app.py:7` and pyproject.toml." for row in rows))
        self.assertFalse(any("hidden_tool_call.py" in row["snippet"] for row in rows))

    def test_search_json_no_matches_has_no_plain_text_output(self) -> None:
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
                [sys.executable, "-m", "codex_plus", "search", "not-present", "--json"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_search_ignores_autonomous_wrapper_boilerplate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            write_cli_session(
                home,
                "019f-test-autonomous",
                cwd="/tmp/project",
                user_message=autonomous_prompt("Ship a keyboard-only CLI wrapper."),
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            boilerplate_result = subprocess.run(
                [sys.executable, "-m", "codex_plus", "search", "This is iteration"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            objective_result = subprocess.run(
                [sys.executable, "-m", "codex_plus", "search", "keyboard-only"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(boilerplate_result.returncode, 1)
        self.assertIn("No matches.", boilerplate_result.stdout)
        self.assertEqual(objective_result.returncode, 0)
        self.assertIn("Ship a keyboard-only CLI wrapper.", objective_result.stdout)
        self.assertNotIn("This is iteration", objective_result.stdout)

    def test_search_ignores_autonomous_status_updates_but_finds_final_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            write_autonomous_status_session(home)

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            status_result = subprocess.run(
                [sys.executable, "-m", "codex_plus", "search", "checking notes"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            final_result = subprocess.run(
                [sys.executable, "-m", "codex_plus", "search", "completed iteration"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(status_result.returncode, 1)
        self.assertIn("No matches.", status_result.stdout)
        self.assertEqual(final_result.returncode, 0)
        self.assertIn("completed iteration", final_result.stdout)
        self.assertNotIn("checking notes", final_result.stdout)

    def test_clean_cli_commands_hide_event_bootstrap_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            session_dir = home / "sessions" / "2026" / "07" / "10"
            session_dir.mkdir(parents=True)
            target = session_dir / "rollout-2026-07-10T12-00-00-019f-test-event-bootstrap.jsonl"
            target.write_text(
                (FIXTURES / "rollout-event-bootstrap.jsonl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            search_result = subprocess.run(
                [sys.executable, "-m", "codex_plus", "search", "hidden bootstrap"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            user_result = subprocess.run(
                [sys.executable, "-m", "codex_plus", "user", "last", "--no-pager"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(search_result.returncode, 1)
        self.assertIn("No matches.", search_result.stdout)
        self.assertEqual(user_result.returncode, 0)
        self.assertIn("Show me the final answer", user_result.stdout)
        self.assertNotIn("hidden bootstrap", user_result.stdout)

    def test_list_here_filters_to_current_git_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "codex-home"
            project = root / "project"
            nested = project / "src"
            other_project = root / "other"
            nested.mkdir(parents=True)
            other_project.mkdir()
            (project / ".git").mkdir()
            write_cli_session(home, "019f-test-here", cwd=str(project), user_message="Project session")
            write_cli_session(home, "019f-test-elsewhere", cwd=str(other_project), user_message="Other session")

            repo_root = Path(__file__).resolve().parents[1]
            env = dict(os.environ)
            env["PYTHONPATH"] = str(repo_root / "src")
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_plus", "list", "--here", "--limit", "20"],
                cwd=nested,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Project session", result.stdout)
        self.assertNotIn("Other session", result.stdout)

    def test_single_session_commands_here_resolve_last_in_current_git_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "codex-home"
            project = root / "project"
            nested = project / "src"
            other_project = root / "other"
            nested.mkdir(parents=True)
            other_project.mkdir()
            (project / ".git").mkdir()
            project_session = write_cli_session(
                home,
                "019f-test-project",
                cwd=str(project),
                user_message="Project session mentions src/app.py:3",
            )
            other_session = write_cli_session(
                home,
                "019f-test-other",
                cwd=str(other_project),
                user_message="Other session mentions src/other.py:4",
            )
            os.utime(other_session, (300, 300))
            os.utime(project_session, (200, 200))

            repo_root = Path(__file__).resolve().parents[1]
            env = dict(os.environ)
            env["PYTHONPATH"] = str(repo_root / "src")
            env["CODEX_HOME"] = str(home)
            view_result = subprocess.run(
                [sys.executable, "-m", "codex_plus", "view", "--here", "--no-pager"],
                cwd=nested,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            files_result = subprocess.run(
                [sys.executable, "-m", "codex_plus", "files", "--here"],
                cwd=nested,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(view_result.returncode, 0)
        self.assertIn("Project session mentions src/app.py:3", view_result.stdout)
        self.assertNotIn("Other session mentions src/other.py:4", view_result.stdout)
        self.assertEqual(files_result.returncode, 0)
        self.assertIn("src/app.py", files_result.stdout)
        self.assertNotIn("src/other.py", files_result.stdout)

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

    @patch("codex_plus.cli.files_thread")
    def test_picker_edit_file_action_opens_file_picker(self, files_mock) -> None:
        files_mock.return_value = 0

        result = handle_session_selection(PickerSelection("edit_file", "019f-test-basic"), mode="chat")

        self.assertEqual(result, 0)
        args = files_mock.call_args.args[0]
        self.assertEqual(args.selector, "019f-test-basic")
        self.assertEqual(args.mode, "chat")
        self.assertTrue(args.open)


def write_cli_session(home: Path, session_id: str, *, cwd: str, user_message: str) -> Path:
    session_dir = home / "sessions" / "2026" / "07" / "10"
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"rollout-2026-07-10T10-00-00-{session_id}.jsonl"
    records = [
        {
            "timestamp": "2026-07-10T10:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": cwd, "source": "cli"},
        },
        {
            "timestamp": "2026-07-10T10:00:01.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": user_message, "images": []},
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def write_autonomous_status_session(home: Path) -> Path:
    session_dir = home / "sessions" / "2026" / "07" / "10"
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "rollout-2026-07-10T10-00-00-019f-test-status.jsonl"
    records = [
        {
            "timestamp": "2026-07-10T10:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": "019f-test-status", "cwd": "/tmp/project", "source": "exec"},
        },
        {
            "timestamp": "2026-07-10T10:00:01.000Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "Run the autonomous iteration", "images": []},
        },
        {
            "timestamp": "2026-07-10T10:00:02.000Z",
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "phase": "commentary",
                "message": json.dumps(
                    {
                        "success": True,
                        "summary": "checking notes",
                        "key_changes_made": [],
                        "key_learnings": [],
                    }
                ),
            },
        },
        {
            "timestamp": "2026-07-10T10:00:03.000Z",
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "phase": "final_answer",
                "message": json.dumps(
                    {
                        "success": True,
                        "summary": "completed iteration",
                        "key_changes_made": ["hidden status updates"],
                        "key_learnings": [],
                    }
                ),
            },
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

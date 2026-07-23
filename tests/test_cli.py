from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import path_bootstrap  # noqa: F401

from codex_tui.cli import handle_session_selection, main
from codex_tui.fzf import PickerSelection
from codex_tui.transcript import format_ms


FIXTURES = Path(__file__).parent / "fixtures"


class CliTests(unittest.TestCase):
    def test_version_command(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [sys.executable, "-m", "codex_tui", "--version"],
            cwd=os.getcwd(),
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("CodexTUI 0.1.0", result.stdout)

    def test_legacy_codex_plus_module_still_dispatches(self) -> None:
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
        self.assertIn("CodexTUI 0.1.0", result.stdout)

    def test_help_hides_internal_preview_commands(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [sys.executable, "-m", "codex_tui", "--help"],
            cwd=os.getcwd(),
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn("tui", result.stdout)
        self.assertIn("files", result.stdout)
        self.assertNotIn("==SUPPRESS==", result.stdout)
        self.assertNotIn("file-preview", result.stdout)

    def test_compress_placeholder_is_explicitly_not_implemented(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        result = subprocess.run(
            [sys.executable, "-m", "codex_tui", "compress"],
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
                [sys.executable, "-m", "codex_tui", "search", "src/app.py", "--open"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        self.assertIn("019f-tes", result.stdout)
        self.assertIn("src/app.py", result.stdout)
        self.assertIn("ctui view 019f-test-files --mode chat", result.stdout)

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
                [sys.executable, "-m", "codex_tui", "search", "hidden_tool_call.py"],
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
                [sys.executable, "-m", "codex_tui", "search", "src/app.py", "--json"],
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
                [sys.executable, "-m", "codex_tui", "search", "not-present", "--json"],
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
                [sys.executable, "-m", "codex_tui", "search", "This is iteration"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            objective_result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "search", "keyboard-only"],
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
                [sys.executable, "-m", "codex_tui", "search", "checking notes"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            final_result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "search", "completed iteration"],
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
                [sys.executable, "-m", "codex_tui", "search", "hidden bootstrap"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            user_result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "user", "last", "--no-pager"],
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
                [sys.executable, "-m", "codex_tui", "list", "--here", "--limit", "20"],
                cwd=nested,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Project session", result.stdout)
        self.assertNotIn("Other session", result.stdout)

    def test_list_enriches_blank_sqlite_metadata_from_rollout_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            rollout = write_cli_session(
                home,
                "019f-test-blank-title",
                cwd="/tmp/project",
                user_message="Recovered first prompt",
            )
            write_threads_db_row(
                home,
                session_id="019f-test-blank-title",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(rollout),
                title="",
                preview="",
                first_user_message="",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            list_result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            query_result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json", "-q", "Recovered"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(list_result.returncode, 0)
        rows = [json.loads(line) for line in list_result.stdout.splitlines()]
        self.assertEqual(rows[0]["title"], "Recovered first prompt")
        self.assertEqual(query_result.returncode, 0)
        query_rows = [json.loads(line) for line in query_result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in query_rows], ["019f-test-blank-title"])

    def test_list_falls_back_to_session_files_when_sqlite_threads_table_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            write_cli_session(
                home,
                "019f-test-empty-db",
                cwd="/tmp/project",
                user_message="Session hidden by empty db",
            )
            write_empty_threads_db(home)

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-empty-db"])
        self.assertEqual(rows[0]["title"], "Session hidden by empty db")

    def test_list_uses_session_files_when_state_db_symlink_is_broken(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            write_cli_session(
                home,
                "019f-test-broken-state-link",
                cwd="/tmp/project",
                user_message="Session hidden by broken state db symlink",
            )
            try:
                os.symlink(home / "missing.sqlite", home / "state_999.sqlite")
            except (AttributeError, NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codex_tui",
                    "list",
                    "--json",
                    "-q",
                    "Session hidden by broken state db symlink",
                ],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-broken-state-link"])

    def test_list_skips_corrupt_utf8_session_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            corrupt_dir = home / "sessions" / "2026" / "07" / "10"
            corrupt_dir.mkdir(parents=True)
            (corrupt_dir / "rollout-2026-07-10T09-00-00-019f-test-corrupt.jsonl").write_bytes(
                b"\xff\xfe\x00bad\n"
            )
            write_cli_session(
                home,
                "019f-test-valid-after-corrupt",
                cwd="/tmp/project",
                user_message="Valid session after corrupt file",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codex_tui",
                    "list",
                    "--json",
                    "-q",
                    "Valid session after corrupt file",
                ],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-valid-after-corrupt"])

    def test_list_merges_newer_unindexed_session_file_with_readable_sqlite_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            older_rollout = write_cli_session(
                home,
                "019f-test-indexed-old",
                cwd="/tmp/project",
                user_message="Indexed old session",
            )
            newer_rollout = write_cli_session(
                home,
                "019f-test-unindexed-new",
                cwd="/tmp/project",
                user_message="Unindexed new session",
            )
            os.utime(newer_rollout, (1783677607, 1783677607))
            write_threads_db_row(
                home,
                session_id="019f-test-indexed-old",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(older_rollout),
                title="Indexed old session",
                preview="",
                first_user_message="Indexed old session",
                recency_at_ms=1783677605000,
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json", "--limit", "2"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(
            [row["id"] for row in rows],
            ["019f-test-unindexed-new", "019f-test-indexed-old"],
        )

    def test_list_uses_session_files_when_sqlite_rows_reference_missing_rollouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            write_cli_session(
                home,
                "019f-test-readable-file",
                cwd="/tmp/project",
                user_message="Readable session file",
            )
            write_threads_db_row(
                home,
                session_id="019f-test-stale-row",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(home / "missing-rollout.jsonl"),
                title="Stale SQLite row",
                preview="",
                first_user_message="Stale SQLite row",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-readable-file"])
        self.assertEqual(rows[0]["title"], "Readable session file")

    def test_list_uses_session_files_when_stale_sqlite_rows_are_filtered_out(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            write_cli_session(
                home,
                "019f-test-filtered-file",
                cwd="/tmp/project",
                user_message="Readable filtered session",
            )
            write_threads_db_row(
                home,
                session_id="019f-test-stale-row",
                cwd="/tmp/other",
                source="cli",
                rollout_path=str(home / "missing-rollout.jsonl"),
                title="Unrelated stale SQLite row",
                preview="",
                first_user_message="Unrelated stale SQLite row",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json", "-q", "Readable filtered"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-filtered-file"])
        self.assertEqual(rows[0]["title"], "Readable filtered session")

    def test_list_uses_session_files_when_readable_sqlite_rows_do_not_match_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            indexed_rollout = write_cli_session(
                home,
                "019f-test-indexed-other",
                cwd="/tmp/other",
                user_message="Indexed other session",
            )
            write_cli_session(
                home,
                "019f-test-unindexed-query",
                cwd="/tmp/project",
                user_message="Readable unindexed history session",
            )
            write_threads_db_row(
                home,
                session_id="019f-test-indexed-other",
                cwd="/tmp/other",
                source="cli",
                rollout_path=str(indexed_rollout),
                title="Indexed other session",
                preview="",
                first_user_message="Indexed other session",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json", "-q", "unindexed history"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-unindexed-query"])
        self.assertEqual(rows[0]["title"], "Readable unindexed history session")

    def test_list_merges_older_matching_unindexed_session_file_with_sqlite_query_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            indexed_rollout = write_cli_session(
                home,
                "019f-test-indexed-query",
                cwd="/tmp/project",
                user_message="Shared filtered indexed session",
            )
            unindexed_rollout = write_cli_session(
                home,
                "019f-test-unindexed-older-query",
                cwd="/tmp/project",
                user_message="Shared filtered unindexed older session",
            )
            os.utime(unindexed_rollout, (1783677604, 1783677604))
            write_threads_db_row(
                home,
                session_id="019f-test-indexed-query",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(indexed_rollout),
                title="Shared filtered indexed session",
                preview="",
                first_user_message="Shared filtered indexed session",
                recency_at_ms=1783677605000,
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json", "-q", "Shared filtered", "--limit", "5"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(
            [row["id"] for row in rows],
            ["019f-test-indexed-query", "019f-test-unindexed-older-query"],
        )

    def test_list_uses_newer_state_database_when_state_five_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            old_rollout = write_cli_session(
                home,
                "019f-test-state-five",
                cwd="/tmp/project",
                user_message="Older state five session",
            )
            new_rollout = write_cli_session(
                home,
                "019f-test-state-six",
                cwd="/tmp/project",
                user_message="Newer state six session",
            )
            write_threads_db_row(
                home,
                session_id="019f-test-state-five",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(old_rollout),
                title="Older state five session",
                preview="",
                first_user_message="Older state five session",
                db_name="state_5.sqlite",
            )
            write_threads_db_row(
                home,
                session_id="019f-test-state-six",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(new_rollout),
                title="Newer state six session",
                preview="",
                first_user_message="Newer state six session",
                db_name="state_6.sqlite",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json", "--limit", "1"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-state-six"])
        self.assertEqual(rows[0]["title"], "Newer state six session")

    def test_list_skips_stale_newer_state_database_when_older_state_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            legacy_rollout = home / "legacy-rollouts" / "state-five.jsonl"
            write_session_file(
                legacy_rollout,
                "019f-test-readable-state-five",
                cwd="/tmp/project",
                user_message="Readable older state session",
            )
            write_threads_db_row(
                home,
                session_id="019f-test-stale-state-six",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(home / "missing-rollout.jsonl"),
                title="Stale newer state session",
                preview="",
                first_user_message="Stale newer state session",
                db_name="state_6.sqlite",
            )
            write_threads_db_row(
                home,
                session_id="019f-test-readable-state-five",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(legacy_rollout),
                title="Readable older state session",
                preview="",
                first_user_message="Readable older state session",
                db_name="state_5.sqlite",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json", "--limit", "1"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-readable-state-five"])
        self.assertEqual(rows[0]["title"], "Readable older state session")

    def test_list_merges_session_fallback_with_readable_older_state_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            scanned_rollout = write_cli_session(
                home,
                "019f-test-scanned-session",
                cwd="/tmp/project",
                user_message="Readable scanned session",
            )
            legacy_rollout = home / "legacy-rollouts" / "state-five.jsonl"
            write_session_file(
                legacy_rollout,
                "019f-test-readable-state-five",
                cwd="/tmp/project",
                user_message="Readable older state session",
            )
            os.utime(scanned_rollout, (300, 300))
            os.utime(legacy_rollout, (200, 200))
            write_threads_db_row(
                home,
                session_id="019f-test-stale-state-six",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(home / "missing-rollout.jsonl"),
                title="Stale newer state session",
                preview="",
                first_user_message="Stale newer state session",
                db_name="state_6.sqlite",
                recency_at_ms=1783677607000,
            )
            write_threads_db_row(
                home,
                session_id="019f-test-readable-state-five",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(legacy_rollout),
                title="Readable older state session",
                preview="",
                first_user_message="Readable older state session",
                db_name="state_5.sqlite",
                recency_at_ms=1783677606000,
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json", "--limit", "2"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(
            [row["id"] for row in rows],
            ["019f-test-readable-state-five", "019f-test-scanned-session"],
        )
        self.assertEqual(rows[0]["title"], "Readable older state session")
        self.assertEqual(rows[1]["title"], "Readable scanned session")

    def test_list_skips_limited_stale_row_when_same_state_database_has_readable_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            readable_rollout = home / "legacy-rollouts" / "readable-state-five.jsonl"
            write_session_file(
                readable_rollout,
                "019f-test-readable-same-db",
                cwd="/tmp/project",
                user_message="Readable same database session",
            )
            write_threads_db_row(
                home,
                session_id="019f-test-stale-same-db",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(home / "missing-rollout.jsonl"),
                title="Stale same database session",
                preview="",
                first_user_message="Stale same database session",
                recency_at_ms=1783677607000,
            )
            write_threads_db_row(
                home,
                session_id="019f-test-readable-same-db",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(readable_rollout),
                title="Readable same database session",
                preview="",
                first_user_message="Readable same database session",
                recency_at_ms=1783677605000,
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json", "--limit", "1"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-readable-same-db"])
        self.assertEqual(rows[0]["title"], "Readable same database session")

    def test_list_reads_legacy_state_database_without_metadata_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            legacy_rollout = home / "legacy-rollouts" / "legacy-state.jsonl"
            legacy_updated_at = 1783677605
            write_session_file(
                legacy_rollout,
                "019f-test-legacy-state",
                cwd="/tmp/project",
                user_message="Readable legacy state session",
            )
            write_legacy_threads_db_row(
                home,
                session_id="019f-test-legacy-state",
                cwd="/tmp/project",
                source="cli",
                rollout_path=str(legacy_rollout),
                title="",
                updated_at=legacy_updated_at,
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-legacy-state"])
        self.assertEqual(rows[0]["title"], "Readable legacy state session")
        self.assertEqual(rows[0]["updated_at"], format_ms(legacy_updated_at * 1000))

    def test_list_reads_legacy_state_database_with_iso_text_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            newer_rollout = home / "legacy-rollouts" / "iso-newer.jsonl"
            older_rollout = home / "legacy-rollouts" / "iso-older.jsonl"
            write_session_file(
                newer_rollout,
                "019f-test-aaa-iso-newer",
                cwd="/tmp/project",
                user_message="Readable ISO newer state session",
            )
            write_session_file(
                older_rollout,
                "019f-test-zzz-iso-older",
                cwd="/tmp/project",
                user_message="Readable ISO older state session",
            )
            write_iso_timestamp_threads_db_row(
                home,
                session_id="019f-test-aaa-iso-newer",
                rollout_path=str(newer_rollout),
                title="Readable ISO newer state session",
                updated_at="2026-07-10T10:00:05Z",
            )
            write_iso_timestamp_threads_db_row(
                home,
                session_id="019f-test-zzz-iso-older",
                rollout_path=str(older_rollout),
                title="Readable ISO older state session",
                updated_at="2026-07-10T10:00:03Z",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json", "--limit", "1"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-aaa-iso-newer"])
        self.assertEqual(rows[0]["updated_at"], format_ms(1783677605000))

    def test_list_reads_legacy_state_database_with_decimal_text_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            newer_rollout = home / "legacy-rollouts" / "decimal-newer.jsonl"
            older_rollout = home / "legacy-rollouts" / "decimal-older.jsonl"
            write_session_file(
                newer_rollout,
                "019f-test-aaa-decimal-newer",
                cwd="/tmp/project",
                user_message="Readable decimal newer state session",
            )
            write_session_file(
                older_rollout,
                "019f-test-zzz-decimal-older",
                cwd="/tmp/project",
                user_message="Readable decimal older state session",
            )
            write_text_timestamp_threads_db_row(
                home,
                session_id="019f-test-aaa-decimal-newer",
                rollout_path=str(newer_rollout),
                title="Readable decimal newer state session",
                recency_at_ms="1783677605000.0",
            )
            write_text_timestamp_threads_db_row(
                home,
                session_id="019f-test-zzz-decimal-older",
                rollout_path=str(older_rollout),
                title="Readable decimal older state session",
                recency_at_ms="1783677603000.0",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json", "--limit", "1"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-aaa-decimal-newer"])
        self.assertEqual(rows[0]["updated_at"], format_ms(1783677605000))

    def test_list_recovers_source_and_cwd_when_legacy_state_lacks_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            legacy_rollout = home / "legacy-rollouts" / "minimal-state.jsonl"
            write_session_file(
                legacy_rollout,
                "019f-test-minimal-state",
                cwd="/tmp/project",
                user_message="Readable minimal state session",
            )
            write_minimal_threads_db_row(
                home,
                session_id="019f-test-minimal-state",
                rollout_path=str(legacy_rollout),
                title="",
                updated_at=1783677605,
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codex_tui",
                    "list",
                    "--json",
                    "--source",
                    "cli",
                    "--cwd",
                    "/tmp/project",
                ],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-minimal-state"])
        self.assertEqual(rows[0]["title"], "Readable minimal state session")
        self.assertEqual(rows[0]["source"], "cli")
        self.assertEqual(rows[0]["cwd"], "/tmp/project")

    def test_list_recovers_blank_sqlite_id_from_rollout_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            rollout = home / "legacy-rollouts" / "blank-id-state.jsonl"
            write_session_file(
                rollout,
                "019f-test-blank-id-state",
                cwd="/tmp/project",
                user_message="Readable blank id state session",
            )
            write_threads_db_row(
                home,
                session_id="",
                cwd="",
                source="",
                rollout_path=str(rollout),
                title="",
                preview="",
                first_user_message="",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-blank-id-state"])
        self.assertEqual(rows[0]["title"], "Readable blank id state session")
        self.assertEqual(rows[0]["source"], "cli")
        self.assertEqual(rows[0]["cwd"], "/tmp/project")

    def test_list_decodes_blob_sqlite_rollout_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            rollout = home / "legacy-rollouts" / "blob-path-state.jsonl"
            write_session_file(
                rollout,
                "019f-test-blob-path-state",
                cwd="/tmp/project",
                user_message="Readable blob path state session",
            )
            write_blob_rollout_path_threads_db_row(
                home,
                session_id="",
                rollout_path=str(rollout),
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codex_tui",
                    "list",
                    "--json",
                    "-q",
                    "Readable blob path state session",
                ],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-blob-path-state"])
        self.assertEqual(rows[0]["title"], "Readable blob path state session")
        self.assertEqual(rows[0]["rollout_path"], str(rollout))

    def test_list_decodes_nul_padded_blob_sqlite_rollout_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            rollout = home / "legacy-rollouts" / "nul-padded-blob-path-state.jsonl"
            write_session_file(
                rollout,
                "019f-test-nul-padded-blob-path-state",
                cwd="/tmp/project",
                user_message="NUL padded blob path state session",
            )
            write_blob_rollout_path_threads_db_row(
                home,
                session_id="",
                rollout_path=str(rollout),
                path_suffix=b"\x00",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codex_tui",
                    "list",
                    "--json",
                    "-q",
                    "NUL padded blob path state session",
                ],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-nul-padded-blob-path-state"])
        self.assertEqual(rows[0]["title"], "NUL padded blob path state session")
        self.assertEqual(rows[0]["rollout_path"], str(rollout))

    def test_list_resolves_relative_sqlite_rollout_paths_from_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            relative_rollout = Path("legacy-rollouts") / "relative.jsonl"
            rollout = home / relative_rollout
            write_session_file(
                rollout,
                "019f-test-relative-rollout",
                cwd="/tmp/project",
                user_message="Relative rollout path session",
            )
            write_threads_db_row(
                home,
                session_id="019f-test-relative-rollout",
                cwd="",
                source="",
                rollout_path=str(relative_rollout),
                title="",
                preview="",
                first_user_message="",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "codex_tui",
                    "list",
                    "--json",
                    "-q",
                    "Relative rollout path session",
                ],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-relative-rollout"])
        self.assertEqual(rows[0]["title"], "Relative rollout path session")
        self.assertEqual(rows[0]["source"], "cli")
        self.assertEqual(rows[0]["cwd"], "/tmp/project")
        self.assertEqual(rows[0]["rollout_path"], str(rollout))

    def test_list_normalizes_text_archived_flags_from_state_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            text_zero_rollout = home / "legacy-rollouts" / "text-zero.jsonl"
            text_padded_zero_rollout = home / "legacy-rollouts" / "text-padded-zero.jsonl"
            text_decimal_zero_rollout = home / "legacy-rollouts" / "text-decimal-zero.jsonl"
            text_false_rollout = home / "legacy-rollouts" / "text-false.jsonl"
            write_session_file(
                text_zero_rollout,
                "019f-test-text-zero-archived",
                cwd="/tmp/project",
                user_message="Text zero archived flag session",
            )
            write_session_file(
                text_padded_zero_rollout,
                "019f-test-text-padded-zero-archived",
                cwd="/tmp/project",
                user_message="Text padded zero archived flag session",
            )
            write_session_file(
                text_decimal_zero_rollout,
                "019f-test-text-decimal-zero-archived",
                cwd="/tmp/project",
                user_message="Text decimal zero archived flag session",
            )
            write_session_file(
                text_false_rollout,
                "019f-test-text-false-archived",
                cwd="/tmp/project",
                user_message="Text false archived flag session",
            )
            write_text_archived_threads_db_row(
                home,
                session_id="019f-test-text-zero-archived",
                rollout_path=str(text_zero_rollout),
                title="Text zero archived flag session",
                archived_value="0",
                recency_at_ms=1783677606000,
            )
            write_text_archived_threads_db_row(
                home,
                session_id="019f-test-text-padded-zero-archived",
                rollout_path=str(text_padded_zero_rollout),
                title="Text padded zero archived flag session",
                archived_value=" 0 ",
                recency_at_ms=1783677605500,
            )
            write_text_archived_threads_db_row(
                home,
                session_id="019f-test-text-decimal-zero-archived",
                rollout_path=str(text_decimal_zero_rollout),
                title="Text decimal zero archived flag session",
                archived_value="0.0",
                recency_at_ms=1783677605250,
            )
            write_text_archived_threads_db_row(
                home,
                session_id="019f-test-text-false-archived",
                rollout_path=str(text_false_rollout),
                title="Text false archived flag session",
                archived_value="false",
                recency_at_ms=1783677605000,
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(
            [row["id"] for row in rows],
            [
                "019f-test-text-zero-archived",
                "019f-test-text-padded-zero-archived",
                "019f-test-text-decimal-zero-archived",
                "019f-test-text-false-archived",
            ],
        )
        self.assertEqual([row["archived"] for row in rows], [False, False, False, False])

    def test_list_normalizes_real_zero_archived_flag_from_state_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            rollout = home / "legacy-rollouts" / "real-zero.jsonl"
            write_session_file(
                rollout,
                "019f-test-real-zero-archived",
                cwd="/tmp/project",
                user_message="Real zero archived flag session",
            )
            write_real_archived_threads_db_row(
                home,
                session_id="019f-test-real-zero-archived",
                rollout_path=str(rollout),
                title="Real zero archived flag session",
                archived_value=0.0,
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-real-zero-archived"])
        self.assertEqual(rows[0]["archived"], False)

    def test_list_normalizes_blob_zero_archived_flag_from_state_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            rollout = home / "legacy-rollouts" / "blob-zero.jsonl"
            write_session_file(
                rollout,
                "019f-test-blob-zero-archived",
                cwd="/tmp/project",
                user_message="Blob zero archived flag session",
            )
            write_blob_archived_threads_db_row(
                home,
                session_id="019f-test-blob-zero-archived",
                rollout_path=str(rollout),
                title="Blob zero archived flag session",
                archived_value=b"0",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-blob-zero-archived"])
        self.assertEqual(rows[0]["archived"], False)

    def test_list_normalizes_binary_zero_blob_archived_flag_from_state_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            rollout = home / "legacy-rollouts" / "binary-zero.jsonl"
            write_session_file(
                rollout,
                "019f-test-binary-zero-archived",
                cwd="/tmp/project",
                user_message="Binary zero archived flag session",
            )
            write_blob_archived_threads_db_row(
                home,
                session_id="019f-test-binary-zero-archived",
                rollout_path=str(rollout),
                title="Binary zero archived flag session",
                archived_value=b"\x00",
            )

            env = dict(os.environ)
            env["PYTHONPATH"] = "src"
            env["CODEX_HOME"] = str(home)
            result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "list", "--json"],
                cwd=os.getcwd(),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in rows], ["019f-test-binary-zero-archived"])
        self.assertEqual(rows[0]["archived"], False)

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
                [sys.executable, "-m", "codex_tui", "view", "--here", "--no-pager"],
                cwd=nested,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            files_result = subprocess.run(
                [sys.executable, "-m", "codex_tui", "files", "--here"],
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

    @patch("codex_tui.cli.run_codex_json_stream")
    def test_stream_command_runs_codex_exec_json_through_codextui(self, stream_mock) -> None:
        stream_mock.return_value = 0
        with patch.dict(os.environ, {"CODEX_REAL_BIN": "/tmp/codex"}):
            result = main(["stream", "--raw-json", "Fix", "the", "bug"])

        self.assertEqual(result, 0)
        stream_mock.assert_called_once_with(["/tmp/codex", "exec", "--json", "Fix the bug"], raw_json=True)

    @patch("codex_tui.cli.run_codex_json_stream")
    def test_stream_command_attaches_images_to_new_prompt(self, stream_mock) -> None:
        stream_mock.return_value = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "screen.png"
            image_path.write_bytes(b"png")
            with patch.dict(os.environ, {"CODEX_REAL_BIN": "/tmp/codex"}):
                result = main(["stream", "--image", str(image_path), "Describe", "this"])

        self.assertEqual(result, 0)
        stream_mock.assert_called_once_with(
            ["/tmp/codex", "exec", "--json", "--image", str(image_path.resolve()), "Describe this"],
            raw_json=False,
        )

    @patch("codex_tui.cli.run_codex_json_stream")
    def test_stream_command_rejects_missing_images_before_starting_codex(self, stream_mock) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.png"
            with patch.dict(os.environ, {"CODEX_REAL_BIN": "/tmp/codex"}):
                with patch("codex_tui.cli.sys.stderr", new_callable=StringIO) as stderr:
                    result = main(["stream", "--image", str(missing), "Describe", "this"])

        self.assertEqual(result, 2)
        self.assertIn("image not found", stderr.getvalue())
        stream_mock.assert_not_called()

    @patch("codex_tui.cli.run_codex_json_stream")
    def test_stream_resume_resolves_selector_without_opening_interactive_codex(self, stream_mock) -> None:
        stream_mock.return_value = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            write_cli_session(home, "019f-test-stream", cwd="/tmp/project", user_message="Stream this session")
            with patch.dict(os.environ, {"CODEX_HOME": str(home), "CODEX_REAL_BIN": "/tmp/codex"}):
                result = main(["stream", "--resume", "last", "Continue"])

        self.assertEqual(result, 0)
        stream_mock.assert_called_once_with(
            ["/tmp/codex", "exec", "resume", "--json", "019f-test-stream", "Continue"],
            raw_json=False,
        )

    @patch("codex_tui.cli.run_codex_json_stream")
    def test_stream_resume_uses_stdin_marker_when_prompt_is_piped(self, stream_mock) -> None:
        stream_mock.return_value = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            write_cli_session(home, "019f-test-piped", cwd="/tmp/project", user_message="Stream this session")
            with patch.dict(os.environ, {"CODEX_HOME": str(home), "CODEX_REAL_BIN": "/tmp/codex"}):
                with patch("codex_tui.cli.sys.stdin.isatty", return_value=False):
                    result = main(["stream", "--resume", "last"])

        self.assertEqual(result, 0)
        stream_mock.assert_called_once_with(
            ["/tmp/codex", "exec", "resume", "--json", "019f-test-piped", "-"],
            raw_json=False,
        )

    @patch("codex_tui.cli.run_tui")
    def test_tui_command_passes_filters_to_terminal_ui(self, tui_mock) -> None:
        tui_mock.return_value = 0
        with patch("codex_tui.cli.current_project_root", return_value=Path("/tmp/project")):
            result = main(
                [
                    "tui",
                    "--here",
                    "--all",
                    "--limit",
                    "12",
                    "--query",
                    "streaming",
                    "--source",
                    "exec",
                    "--raw-json",
                ]
            )

        self.assertEqual(result, 0)
        tui_mock.assert_called_once_with(
            include_archived=True,
            limit=12,
            query="streaming",
            source="exec",
            cwd=str(Path("/tmp/project")),
            raw_json=True,
        )

    @patch("codex_tui.cli.run_tui")
    def test_bare_ctui_opens_terminal_ui(self, tui_mock) -> None:
        tui_mock.return_value = 0

        result = main([])

        self.assertEqual(result, 0)
        tui_mock.assert_called_once_with(
            include_archived=False,
            limit=80,
            query=None,
            source=None,
            cwd=None,
            raw_json=False,
        )

    @patch("codex_tui.cli.run_tui")
    def test_bare_ctui_with_here_opens_scoped_terminal_ui(self, tui_mock) -> None:
        tui_mock.return_value = 0
        with patch("codex_tui.cli.current_project_root", return_value=Path("/tmp/project")):
            result = main(["--here"])

        self.assertEqual(result, 0)
        tui_mock.assert_called_once_with(
            include_archived=False,
            limit=80,
            query=None,
            source=None,
            cwd=str(Path("/tmp/project")),
            raw_json=False,
        )

    @patch("codex_tui.cli.view_thread")
    def test_picker_view_action_renders_clean_transcript_instead_of_resuming(self, view_mock) -> None:
        view_mock.return_value = 0

        result = handle_session_selection(PickerSelection("view", "019f-test-basic"), mode="assistant")

        self.assertEqual(result, 0)
        args = view_mock.call_args.args[0]
        self.assertEqual(args.selector, "019f-test-basic")
        self.assertEqual(args.mode, "assistant")

    @patch("codex_tui.cli.files_thread")
    def test_picker_files_action_lists_files_without_opening_editor(self, files_mock) -> None:
        files_mock.return_value = 0

        result = handle_session_selection(PickerSelection("files", "019f-test-basic"), mode="chat")

        self.assertEqual(result, 0)
        args = files_mock.call_args.args[0]
        self.assertEqual(args.selector, "019f-test-basic")
        self.assertEqual(args.mode, "chat")
        self.assertFalse(args.open)

    @patch("codex_tui.cli.files_thread")
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
    write_session_file(path, session_id, cwd=cwd, user_message=user_message)
    return path


def write_session_file(path: Path, session_id: str, *, cwd: str, user_message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def write_threads_db_row(
    home: Path,
    *,
    session_id: str,
    cwd: str,
    source: str,
    rollout_path: str,
    title: str,
    preview: str,
    first_user_message: str,
    db_name: str = "state_5.sqlite",
    recency_at_ms: int = 1783677605000,
) -> None:
    db_path = home / db_name
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS threads (
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
                session_id,
                title,
                cwd,
                source,
                0,
                rollout_path,
                1783677600000,
                recency_at_ms,
                recency_at_ms,
                preview,
                first_user_message,
            ),
        )
        con.commit()
    finally:
        con.close()


def write_empty_threads_db(home: Path) -> None:
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
        con.commit()
    finally:
        con.close()


def write_legacy_threads_db_row(
    home: Path,
    *,
    session_id: str,
    cwd: str,
    source: str,
    rollout_path: str,
    title: str,
    updated_at: int,
) -> None:
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
                created_at INTEGER,
                updated_at INTEGER
            )
            """
        )
        con.execute(
            """
            INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                title,
                cwd,
                source,
                0,
                rollout_path,
                updated_at - 5,
                updated_at,
            ),
        )
        con.commit()
    finally:
        con.close()


def write_minimal_threads_db_row(
    home: Path,
    *,
    session_id: str,
    rollout_path: str,
    title: str,
    updated_at: int,
) -> None:
    db_path = home / "state_5.sqlite"
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            CREATE TABLE threads (
                id TEXT,
                title TEXT,
                archived INTEGER,
                rollout_path TEXT,
                updated_at INTEGER
            )
            """
        )
        con.execute(
            """
            INSERT INTO threads VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                title,
                0,
                rollout_path,
                updated_at,
            ),
        )
        con.commit()
    finally:
        con.close()


def write_iso_timestamp_threads_db_row(
    home: Path,
    *,
    session_id: str,
    rollout_path: str,
    title: str,
    updated_at: str,
) -> None:
    db_path = home / "state_5.sqlite"
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT,
                title TEXT,
                cwd TEXT,
                source TEXT,
                archived INTEGER,
                rollout_path TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        con.execute(
            """
            INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                title,
                "/tmp/project",
                "cli",
                0,
                rollout_path,
                "2026-07-10T10:00:00Z",
                updated_at,
            ),
        )
        con.commit()
    finally:
        con.close()


def write_text_timestamp_threads_db_row(
    home: Path,
    *,
    session_id: str,
    rollout_path: str,
    title: str,
    recency_at_ms: str,
) -> None:
    db_path = home / "state_5.sqlite"
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT,
                title TEXT,
                cwd TEXT,
                source TEXT,
                archived INTEGER,
                rollout_path TEXT,
                created_at_ms TEXT,
                updated_at_ms TEXT,
                recency_at_ms TEXT,
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
                session_id,
                title,
                "/tmp/project",
                "cli",
                0,
                rollout_path,
                "1783677600000.0",
                recency_at_ms,
                recency_at_ms,
                "",
                title,
            ),
        )
        con.commit()
    finally:
        con.close()


def write_text_archived_threads_db_row(
    home: Path,
    *,
    session_id: str,
    rollout_path: str,
    title: str,
    archived_value: str,
    recency_at_ms: int,
) -> None:
    db_path = home / "state_5.sqlite"
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT,
                title TEXT,
                cwd TEXT,
                source TEXT,
                archived TEXT,
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
                session_id,
                title,
                "/tmp/project",
                "cli",
                archived_value,
                rollout_path,
                1783677600000,
                recency_at_ms,
                recency_at_ms,
                "",
                title,
            ),
        )
        con.commit()
    finally:
        con.close()


def write_real_archived_threads_db_row(
    home: Path,
    *,
    session_id: str,
    rollout_path: str,
    title: str,
    archived_value: float,
) -> None:
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
                archived REAL,
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
                session_id,
                title,
                "/tmp/project",
                "cli",
                archived_value,
                rollout_path,
                1783677600000,
                1783677605000,
                1783677605000,
                "",
                title,
            ),
        )
        con.commit()
    finally:
        con.close()


def write_blob_archived_threads_db_row(
    home: Path,
    *,
    session_id: str,
    rollout_path: str,
    title: str,
    archived_value: bytes,
) -> None:
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
                archived BLOB,
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
                session_id,
                title,
                "/tmp/project",
                "cli",
                sqlite3.Binary(archived_value),
                rollout_path,
                1783677600000,
                1783677605000,
                1783677605000,
                "",
                title,
            ),
        )
        con.commit()
    finally:
        con.close()


def write_blob_rollout_path_threads_db_row(
    home: Path,
    *,
    session_id: str,
    rollout_path: str,
    path_suffix: bytes = b"",
) -> None:
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
                rollout_path BLOB,
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
                session_id,
                "",
                "",
                "",
                0,
                sqlite3.Binary(rollout_path.encode("utf-8") + path_suffix),
                1783677600000,
                1783677605000,
                1783677605000,
                "",
                "",
            ),
        )
        con.commit()
    finally:
        con.close()


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

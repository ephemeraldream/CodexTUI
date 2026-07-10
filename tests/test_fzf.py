from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import path_bootstrap  # noqa: F401

from codex_plus.fzf import choose_search_match, choose_thread, parse_selection
from codex_plus.models import SearchMatch, ThreadRow


class FzfTests(unittest.TestCase):
    def test_parse_selection_defaults_enter_to_resume(self) -> None:
        selection = parse_selection(0, "019f-test-basic\t2026-07-10\n", {"ctrl-v": "view"})

        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.action, "resume")
        self.assertEqual(selection.value, "019f-test-basic")

    def test_parse_selection_maps_expected_action_key(self) -> None:
        selection = parse_selection(0, "ctrl-v\n019f-test-basic\t2026-07-10\n", {"ctrl-v": "view"})

        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.action, "view")
        self.assertEqual(selection.value, "019f-test-basic")

    @patch("codex_plus.fzf.subprocess.run")
    def test_choose_thread_enables_view_final_user_and_file_actions(self, run_mock) -> None:
        thread = sample_thread()
        run_mock.return_value = subprocess.CompletedProcess(
            ["fzf"],
            0,
            stdout=f"ctrl-o\n{thread.id}\t2026-07-10\n",
            stderr="",
        )

        selection = choose_thread([thread], mode="chat")

        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.action, "files")
        self.assertEqual(selection.value, thread.id)
        command = run_mock.call_args.args[0]
        self.assertIn("--expect=ctrl-v,ctrl-f,ctrl-u,ctrl-o", command)
        self.assertIn("ctrl-v views", " ".join(command))

    @patch("codex_plus.fzf.subprocess.run")
    def test_resume_picker_keeps_plain_resume_only_header(self, run_mock) -> None:
        thread = sample_thread()
        run_mock.return_value = subprocess.CompletedProcess(
            ["fzf"],
            0,
            stdout=f"{thread.id}\t2026-07-10\n",
            stderr="",
        )

        selection = choose_thread([thread], mode="chat", allow_actions=False)

        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.action, "resume")
        command = run_mock.call_args.args[0]
        self.assertNotIn("--expect=ctrl-v,ctrl-f,ctrl-u,ctrl-o", command)
        self.assertIn("enter resumes selected session", " ".join(command))

    @patch("codex_plus.fzf.subprocess.run")
    def test_choose_search_match_supports_same_session_actions(self, run_mock) -> None:
        thread = sample_thread()
        run_mock.return_value = subprocess.CompletedProcess(
            ["fzf"],
            0,
            stdout=f"ctrl-f\n{thread.id}\t2026-07-10\n",
            stderr="",
        )

        selection = choose_search_match([SearchMatch(thread, "assistant", "fixed it")], mode="chat")

        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.action, "final")
        self.assertEqual(selection.value, thread.id)


def sample_thread() -> ThreadRow:
    return ThreadRow(
        id="019f-test-basic",
        title="Find the bug",
        cwd="/tmp/project",
        source="cli",
        archived=False,
        rollout_path="/tmp/project/session.jsonl",
        created_at_ms=1783677600000,
        updated_at_ms=1783677605000,
        recency_at_ms=1783677605000,
        preview="",
        first_user_message="Find the bug",
    )


if __name__ == "__main__":
    unittest.main()

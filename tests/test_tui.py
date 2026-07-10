from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import path_bootstrap  # noqa: F401

from codex_plus.models import ThreadRow
from codex_plus.tui import stream_selected_thread, wrap_lines


class TuiTests(unittest.TestCase):
    @patch("codex_plus.tui.run_codex_json_stream")
    def test_stream_selected_thread_uses_codexplus_json_resume(self, stream_mock) -> None:
        stream_mock.return_value = 0
        thread = sample_thread()

        with patch("codex_plus.tui.real_codex_bin", return_value=Path("/tmp/codex")):
            result = stream_selected_thread(thread, "Continue from the selected session")

        self.assertEqual(result, 0)
        stream_mock.assert_called_once_with(
            [
                "/tmp/codex",
                "exec",
                "resume",
                "--json",
                "019f-test-tui",
                "Continue from the selected session",
            ],
            raw_json=False,
        )

    def test_wrap_lines_breaks_long_preview_text_to_fit_terminal_width(self) -> None:
        lines = wrap_lines(["abcdefghij"], width=5)

        self.assertEqual(lines, ["abcd", "efgh", "ij"])


def sample_thread() -> ThreadRow:
    return ThreadRow(
        id="019f-test-tui",
        title="Build a TUI",
        cwd="/tmp/project",
        source="cli",
        archived=False,
        rollout_path="/tmp/project/session.jsonl",
        created_at_ms=1783677600000,
        updated_at_ms=1783677605000,
        recency_at_ms=1783677605000,
        preview="",
        first_user_message="Build a TUI",
    )


if __name__ == "__main__":
    unittest.main()

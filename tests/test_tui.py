from __future__ import annotations

from io import StringIO
import unittest
from pathlib import Path
from unittest.mock import patch

import path_bootstrap  # noqa: F401

from codex_plus.models import ThreadRow
from codex_plus.tui import CursesStreamWriter, TuiApp, stream_selected_thread, visible_lines, wrap_lines


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

    @patch("codex_plus.tui.run_codex_json_stream")
    def test_stream_selected_thread_routes_output_back_to_tui(self, stream_mock) -> None:
        stream_mock.return_value = 0
        thread = sample_thread()
        output = StringIO()

        with patch("codex_plus.tui.real_codex_bin", return_value=Path("/tmp/codex")):
            result = stream_selected_thread(thread, "Continue", stdout=output)

        self.assertEqual(result, 0)
        stream_mock.assert_called_once_with(
            ["/tmp/codex", "exec", "resume", "--json", "019f-test-tui", "Continue"],
            raw_json=False,
            stdout=output,
            stderr_to_stdout=True,
        )

    def test_curses_stream_writer_buffers_partial_lines_and_draws(self) -> None:
        app = TuiApp([sample_thread()], lambda _thread, _prompt, _stdout: 0)
        draws: list[tuple[list[str], str | None]] = []

        def draw_stream(current_line: str | None = None) -> None:
            draws.append((list(app.stream_lines), current_line))

        app.draw_stream = draw_stream  # type: ignore[method-assign]
        writer = CursesStreamWriter(app)

        self.assertEqual(writer.write("hel"), 3)
        self.assertEqual(draws[-1], ([], "hel"))

        writer.write("lo\nnext\n")
        self.assertEqual(app.stream_lines, ["hello", "next"])
        self.assertEqual(draws[-1], (["hello", "next"], None))

        writer.write("partial")
        writer.close_line()
        self.assertEqual(app.stream_lines, ["hello", "next", "partial"])

    def test_wrap_lines_breaks_long_preview_text_to_fit_terminal_width(self) -> None:
        lines = wrap_lines(["abcdefghij"], width=5)

        self.assertEqual(lines, ["abcd", "efgh", "ij"])

    def test_preview_visible_lines_scroll_and_clamp(self) -> None:
        lines = ["one", "two", "three", "four"]

        self.assertEqual(visible_lines(lines, width=20, height=2, top=1), ["two", "three"])
        self.assertEqual(visible_lines(lines, width=20, height=2, top=99), ["three", "four"])

    def test_stream_view_auto_follows_until_user_scrolls(self) -> None:
        app = TuiApp([sample_thread()], lambda _thread, _prompt, _stdout: 0)
        app.stream_lines = ["one", "two", "three", "four"]

        self.assertEqual(app.visible_stream_lines(None, width=20, height=2), ["three", "four"])

        app.stream_top = 1
        self.assertEqual(app.visible_stream_lines(None, width=20, height=2), ["two", "three"])

    def test_stream_scrollback_clamps_to_available_output(self) -> None:
        app = TuiApp([sample_thread()], lambda _thread, _prompt, _stdout: 0)
        app.stream_lines = ["one", "two", "three", "four", "five"]

        app.scroll_stream_view(-2, width=20, height=2)
        self.assertEqual(app.stream_top, 1)
        self.assertEqual(app.status, "Stream scroll: line 2.")

        app.scroll_stream_view(99, width=20, height=2)
        self.assertEqual(app.stream_top, 3)

    def test_focus_toggle_moves_arrows_between_sessions_and_preview(self) -> None:
        app = TuiApp(
            [sample_thread("019f-test-one"), sample_thread("019f-test-two")],
            lambda _thread, _prompt, _stdout: 0,
        )

        app.move_focused(1)
        self.assertEqual(app.selected, 1)
        self.assertEqual(app.preview_top, 0)

        app.toggle_focus()
        app.move_focused(3)
        self.assertEqual(app.selected, 1)
        self.assertEqual(app.preview_top, 3)

    def test_selection_and_mode_changes_reset_preview_scroll(self) -> None:
        app = TuiApp(
            [sample_thread("019f-test-one"), sample_thread("019f-test-two")],
            lambda _thread, _prompt, _stdout: 0,
            preview_top=8,
        )

        app.move_selection(1)
        self.assertEqual(app.preview_top, 0)

        app.preview_top = 4
        app.set_mode("final")
        self.assertEqual(app.preview_top, 0)


def sample_thread(thread_id: str = "019f-test-tui") -> ThreadRow:
    return ThreadRow(
        id=thread_id,
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

from __future__ import annotations

from io import StringIO
import unittest
from pathlib import Path
from unittest.mock import patch

import path_bootstrap  # noqa: F401

from codex_tui.models import ThreadRow
from codex_tui.tui import CursesStreamWriter, TuiApp, stream_new_prompt, stream_selected_thread, visible_lines, wrap_lines


FIXTURES = Path(__file__).parent / "fixtures"


class TuiTests(unittest.TestCase):
    @patch("codex_tui.tui.run_codex_json_stream")
    def test_stream_selected_thread_uses_codextui_json_resume(self, stream_mock) -> None:
        stream_mock.return_value = 0
        thread = sample_thread()

        with patch("codex_tui.tui.real_codex_bin", return_value=Path("/tmp/codex")):
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

    @patch("codex_tui.tui.run_codex_json_stream")
    def test_stream_selected_thread_routes_output_back_to_tui(self, stream_mock) -> None:
        stream_mock.return_value = 0
        thread = sample_thread()
        output = StringIO()

        with patch("codex_tui.tui.real_codex_bin", return_value=Path("/tmp/codex")):
            result = stream_selected_thread(thread, "Continue", stdout=output)

        self.assertEqual(result, 0)
        stream_mock.assert_called_once_with(
            ["/tmp/codex", "exec", "resume", "--json", "019f-test-tui", "Continue"],
            raw_json=False,
            stdout=output,
            stderr_to_stdout=True,
        )

    @patch("codex_tui.tui.run_codex_json_stream")
    def test_stream_new_prompt_uses_codextui_json_exec(self, stream_mock) -> None:
        stream_mock.return_value = 0

        with patch("codex_tui.tui.real_codex_bin", return_value=Path("/tmp/codex")):
            result = stream_new_prompt("Start a new task")

        self.assertEqual(result, 0)
        stream_mock.assert_called_once_with(
            ["/tmp/codex", "exec", "--json", "Start a new task"],
            raw_json=False,
        )

    @patch("codex_tui.tui.run_codex_json_stream")
    def test_stream_new_prompt_routes_output_back_to_tui(self, stream_mock) -> None:
        stream_mock.return_value = 0
        output = StringIO()

        with patch("codex_tui.tui.real_codex_bin", return_value=Path("/tmp/codex")):
            result = stream_new_prompt("Start a new task", stdout=output)

        self.assertEqual(result, 0)
        stream_mock.assert_called_once_with(
            ["/tmp/codex", "exec", "--json", "Start a new task"],
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

    def test_files_preview_mode_lists_referenced_files(self) -> None:
        thread = sample_thread("019f-test-files")
        thread = ThreadRow(
            id=thread.id,
            title=thread.title,
            cwd="/tmp/project",
            source=thread.source,
            archived=thread.archived,
            rollout_path=str(FIXTURES / "rollout-files.jsonl"),
            created_at_ms=thread.created_at_ms,
            updated_at_ms=thread.updated_at_ms,
            recency_at_ms=thread.recency_at_ms,
            preview=thread.preview,
            first_user_message=thread.first_user_message,
        )
        app = TuiApp([thread], lambda _thread, _prompt, _stdout: 0)

        app.set_mode("files")
        lines = "\n".join(app.preview_lines(thread))

        self.assertIn("src/app.py", lines)
        self.assertIn("tests/test_app.py", lines)
        self.assertIn("pyproject.toml", lines)
        self.assertNotIn("src/hidden_tool_call.py", lines)

    def test_refresh_threads_preserves_selected_session_and_clears_preview_cache(self) -> None:
        refreshed = [sample_thread("019f-test-new"), sample_thread("019f-test-two")]
        app = TuiApp(
            [sample_thread("019f-test-one"), sample_thread("019f-test-two")],
            lambda _thread, _prompt, _stdout: 0,
            thread_loader=lambda: refreshed,
            selected=1,
            top=1,
            preview_top=6,
        )
        app.preview_cache[("019f-test-two", "chat")] = ["stale"]

        app.refresh_threads()

        self.assertEqual(app.threads, refreshed)
        self.assertEqual(app.selected, 1)
        self.assertEqual(app.top, 1)
        self.assertEqual(app.preview_top, 0)
        self.assertEqual(app.preview_cache, {})
        self.assertEqual(app.status, "Refreshed 2 sessions.")

    def test_refresh_threads_falls_back_when_selected_session_disappears(self) -> None:
        app = TuiApp(
            [sample_thread("019f-test-one"), sample_thread("019f-test-two")],
            lambda _thread, _prompt, _stdout: 0,
            thread_loader=lambda: [sample_thread("019f-test-new")],
            selected=1,
            top=1,
        )

        app.refresh_threads()

        self.assertEqual(app.selected, 0)
        self.assertEqual(app.top, 0)

    def test_refresh_threads_keeps_current_list_when_loader_returns_no_sessions(self) -> None:
        threads = [sample_thread("019f-test-one")]
        app = TuiApp(
            threads,
            lambda _thread, _prompt, _stdout: 0,
            thread_loader=lambda: [],
        )

        app.refresh_threads()

        self.assertEqual(app.threads, threads)
        self.assertEqual(app.status, "Refresh found no sessions; keeping current list.")

    def test_empty_tui_preview_guides_first_run_user(self) -> None:
        app = TuiApp([], lambda _thread, _prompt, _stdout: 0)

        lines = "\n".join(app.empty_preview_lines())

        self.assertIn("No Codex sessions found", lines)
        self.assertIn("Press n to start", lines)
        self.assertIn("ctui doctor", lines)

    def test_empty_tui_enter_does_not_crash_without_selected_session(self) -> None:
        app = TuiApp([], lambda _thread, _prompt, _stdout: 0)

        app.ask_selected()

        self.assertEqual(app.status, "No selected session. Press n to start a new Codex prompt.")

    def test_empty_tui_refresh_keeps_empty_state_with_guidance(self) -> None:
        app = TuiApp(
            [],
            lambda _thread, _prompt, _stdout: 0,
            thread_loader=lambda: [],
        )

        app.refresh_threads()

        self.assertEqual(app.threads, [])
        self.assertEqual(app.status, "Refresh found no sessions. Press n to start a new Codex prompt.")

    def test_new_prompt_streams_inside_tui_and_refreshes_sessions(self) -> None:
        prompts: list[str] = []
        refreshed = [sample_thread("019f-test-new"), sample_thread("019f-test-old")]

        def new_runner(prompt: str, stdout: StringIO) -> int:
            prompts.append(prompt)
            stdout.write("new answer\n")
            return 0

        app = TuiApp(
            [sample_thread("019f-test-old")],
            lambda _thread, _prompt, _stdout: 0,
            new_stream_runner=new_runner,
            thread_loader=lambda: refreshed,
        )
        app.read_prompt = lambda _label: "Start fresh"  # type: ignore[method-assign]
        app.draw_stream = lambda _current_line=None: None  # type: ignore[method-assign]
        app.review_stream = lambda: None  # type: ignore[method-assign]
        app.stdscr = FakeScreen()

        app.ask_new()

        self.assertEqual(prompts, ["Start fresh"])
        self.assertEqual(app.stream_lines[0], "CodexTUI streaming a new prompt via codex exec --json")
        self.assertEqual(app.stream_command_label, "codex exec --json")
        self.assertIn("new answer", app.stream_lines)
        self.assertEqual(app.threads, refreshed)
        self.assertEqual(app.selected, 0)
        self.assertEqual(app.top, 0)
        self.assertEqual(app.status, "Stream finished; refreshed 2 sessions.")


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


class FakeScreen:
    def clear(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()

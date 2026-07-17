from __future__ import annotations

import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import path_bootstrap  # noqa: F401

from codex_tui.models import ThreadRow
from codex_tui.theme import TuiTheme, build_curses_theme
from codex_tui.tui import (
    CursesStreamWriter,
    TuiApp,
    footer_help,
    line_attr,
    preview_header,
    scroll_position_label,
    session_row_lines,
    status_line_attr,
    stream_completion_line,
    stream_footer_help,
    stream_header,
    styled_lines,
    stream_new_prompt,
    stream_selected_thread,
    visible_lines,
    visible_session_count,
    wrap_lines,
)


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

    def test_line_attr_styles_roles_and_activity_rows(self) -> None:
        theme = TuiTheme(
            user_header=10,
            assistant_header=20,
            assistant_final_header=30,
            status_muted=40,
            status_error=50,
            tool_header=60,
            code=70,
        )

        self.assertEqual(line_attr("[1] 2026-07-17 10:00:00  YOU", theme), 10)
        self.assertEqual(line_attr("[2] 2026-07-17 10:00:01  CODEX", theme), 20)
        self.assertEqual(line_attr("[3] 2026-07-17 10:00:02  CODEX final", theme), 30)
        self.assertEqual(line_attr("YOU", theme), 10)
        self.assertEqual(line_attr("CODEX", theme), 20)
        self.assertEqual(line_attr("CODEX final", theme), 30)
        self.assertEqual(line_attr("YOU 10:00", theme), 10)
        self.assertEqual(line_attr("CODEX 10:01", theme), 20)
        self.assertEqual(line_attr("CODEX final 10:02", theme), 30)
        self.assertEqual(line_attr("[tool] exec_command: python3 -m unittest", theme), 60)
        self.assertEqual(line_attr("[tokens] input 10k, output 2k", theme), 40)
        self.assertEqual(line_attr("[task] Codex turn failed: auth expired", theme), 50)
        self.assertEqual(line_attr("[task] Codex turn aborted: interrupted", theme), 50)
        self.assertEqual(line_attr("```python", theme), 70)
        self.assertEqual(line_attr("~~~python", theme), 70)
        self.assertEqual(line_attr("ordinary assistant text", theme), 0)

    def test_styled_lines_styles_fenced_code_block_body(self) -> None:
        theme = TuiTheme(code=70, tool_header=60)

        rows = styled_lines(
            ["```python", "print('hi')", "```", "[tool] exec_command: pytest"],
            theme,
        )

        self.assertEqual(
            rows,
            [
                ("```python", 70),
                ("print('hi')", 70),
                ("```", 70),
                ("[tool] exec_command: pytest", 60),
            ],
        )

    def test_styled_lines_styles_tool_output_body_until_next_block(self) -> None:
        theme = TuiTheme(code=70, status_muted=40, tool_header=60)

        rows = styled_lines(
            [
                "[tool output] exec_command",
                "2 failed, 1 passed",
                "see tests/test_app.py",
                "[task] Stream finished.",
            ],
            theme,
        )

        self.assertEqual(
            rows,
            [
                ("[tool output] exec_command", 60),
                ("2 failed, 1 passed", 70),
                ("see tests/test_app.py", 70),
                ("[task] Stream finished.", 40),
            ],
        )

    def test_styled_lines_styles_error_activity_details_until_next_block(self) -> None:
        theme = TuiTheme(status_error=50, status_muted=40, tool_header=60)

        rows = styled_lines(
            [
                "[tool] apply_patch failed: app.py",
                "patch does not apply",
                "line 14 mismatch",
                "[task] Stream finished.",
            ],
            theme,
        )

        self.assertEqual(
            rows,
            [
                ("[tool] apply_patch failed: app.py", 50),
                ("patch does not apply", 50),
                ("line 14 mismatch", 50),
                ("[task] Stream finished.", 40),
            ],
        )

    def test_styled_lines_styles_role_block_bodies_until_next_block(self) -> None:
        theme = TuiTheme(
            user_header=10,
            assistant_header=20,
            assistant_final_header=30,
            user_body=11,
            assistant_body=21,
            assistant_final_body=31,
            status_error=50,
            tool_header=60,
            code=70,
        )

        rows = styled_lines(
            [
                "YOU",
                "  Inspect the TUI.",
                "",
                "  Keep the conversation readable.",
                "CODEX",
                "  I am checking the stream renderer.",
                "```python",
                "YOU",
                "print('hi')",
                "```",
                "  After code.",
                "[tool] exec_command failed: pytest",
                "1 failed",
                "CODEX final",
                "  Done.",
            ],
            theme,
        )

        self.assertEqual(
            rows,
            [
                ("YOU", 10),
                ("  Inspect the TUI.", 11),
                ("", 11),
                ("  Keep the conversation readable.", 11),
                ("CODEX", 20),
                ("  I am checking the stream renderer.", 21),
                ("```python", 70),
                ("YOU", 70),
                ("print('hi')", 70),
                ("```", 70),
                ("  After code.", 21),
                ("[tool] exec_command failed: pytest", 50),
                ("1 failed", 50),
                ("CODEX final", 30),
                ("  Done.", 31),
            ],
        )

    def test_styled_lines_styles_markdown_structure_inside_role_blocks(self) -> None:
        theme = TuiTheme(
            assistant_final_header=30,
            assistant_final_body=31,
            status_muted=40,
            divider=45,
            code=70,
        )

        rows = styled_lines(
            [
                "CODEX final",
                "  ## Result",
                "  Normal paragraph.",
                "  > Quoted detail.",
                "  ---",
                "  - List item.",
                "  ```markdown",
                "  ## Literal heading",
                "  ```",
                "  After code.",
            ],
            theme,
        )

        self.assertEqual(
            rows,
            [
                ("CODEX final", 30),
                ("  ## Result", 30),
                ("  Normal paragraph.", 31),
                ("  > Quoted detail.", 40),
                ("  ---", 45),
                ("  - List item.", 31),
                ("  ```markdown", 70),
                ("  ## Literal heading", 70),
                ("  ```", 70),
                ("  After code.", 31),
            ],
        )

    def test_styled_lines_styles_markdown_heading_with_plain_role_body(self) -> None:
        theme = TuiTheme(assistant_header=20)

        rows = styled_lines(["CODEX", "  ## Result", "  Body text."], theme)

        self.assertEqual(rows, [("CODEX", 20), ("  ## Result", 20), ("  Body text.", 0)])

    def test_styled_lines_styles_markdown_tables_inside_role_blocks(self) -> None:
        theme = TuiTheme(
            assistant_final_header=30,
            assistant_final_body=31,
            divider=45,
            code=70,
        )

        rows = styled_lines(
            [
                "CODEX final",
                "  | File | Status |",
                "  | ---- | ------ |",
                "  | src/tui.py | polished |",
                "  After table.",
            ],
            theme,
        )

        self.assertEqual(
            rows,
            [
                ("CODEX final", 30),
                ("  | File | Status |", 70),
                ("  | ---- | ------ |", 45),
                ("  | src/tui.py | polished |", 70),
                ("  After table.", 31),
            ],
        )

    def test_styled_lines_groups_plan_activity_details_until_next_block(self) -> None:
        theme = TuiTheme(
            status_muted=40,
            tool_header=60,
            divider=45,
            code=70,
        )

        rows = styled_lines(
            [
                "[plan] completed",
                "# Plan",
                "",
                "1. Inspect stream polish.",
                "| Area | Status |",
                "| ---- | ------ |",
                "| plan | grouped |",
                "[task] Stream finished.",
            ],
            theme,
        )

        self.assertEqual(
            rows,
            [
                ("[plan] completed", 60),
                ("# Plan", 60),
                ("", 40),
                ("1. Inspect stream polish.", 40),
                ("| Area | Status |", 70),
                ("| ---- | ------ |", 45),
                ("| plan | grouped |", 70),
                ("[task] Stream finished.", 40),
            ],
        )

    def test_draw_preview_styles_code_body_when_scrolled_inside_block(self) -> None:
        app = TuiApp(
            [sample_thread()],
            lambda _thread, _prompt, _stdout: 0,
            preview_top=1,
            theme=TuiTheme(code=70),
        )
        app.preview_lines = lambda _thread, _width=None: [  # type: ignore[method-assign]
            "```python",
            "print('hi')",
            "```",
        ]
        screen = RecordingWindow()
        app.stdscr = screen

        app.draw_preview(x=0, y=2, width=80, height=2)

        self.assertEqual(screen.text_at(2, 0), "print('hi')")
        self.assertEqual(screen.attr_at(2, 0), 70)
        self.assertEqual(screen.text_at(3, 0), "```")
        self.assertEqual(screen.attr_at(3, 0), 70)

    def test_visible_stream_rows_styles_code_body_when_scrolled_inside_block(self) -> None:
        app = TuiApp(
            [sample_thread()],
            lambda _thread, _prompt, _stdout: 0,
            theme=TuiTheme(code=70),
        )
        app.stream_lines = ["```python", "print('hi')", "```"]
        app.stream_top = 1

        rows = app.visible_stream_rows(None, width=80, height=1)

        self.assertEqual(rows, [("print('hi')", 70)])

    def test_visible_stream_rows_styles_tool_output_body_when_scrolled_inside_block(self) -> None:
        app = TuiApp(
            [sample_thread()],
            lambda _thread, _prompt, _stdout: 0,
            theme=TuiTheme(code=70, tool_header=60),
        )
        app.stream_lines = ["[tool output] exec_command", "2 failed, 1 passed", "[tool] apply_patch"]
        app.stream_top = 1

        rows = app.visible_stream_rows(None, width=80, height=1)

        self.assertEqual(rows, [("2 failed, 1 passed", 70)])

    def test_visible_stream_rows_keep_activity_style_on_wrapped_continuations(self) -> None:
        app = TuiApp(
            [sample_thread()],
            lambda _thread, _prompt, _stdout: 0,
            theme=TuiTheme(status_muted=40),
        )
        app.stream_lines = ["[tokens] last 71.6k, session 231.5k, context 231.5k / 258.4k (89.6%)"]

        rows = app.visible_stream_rows(None, width=24, height=5)

        self.assertGreater(len(rows), 1)
        self.assertTrue(rows[0][0].startswith("[tokens]"))
        self.assertTrue(all(attr == 40 for _line, attr in rows))

    def test_status_line_attr_marks_failures_prominently(self) -> None:
        theme = TuiTheme(status_muted=4, status_error=8)

        self.assertEqual(status_line_attr("Streaming response inside CodexTUI.", theme), 4)
        self.assertEqual(status_line_attr("Stream exited with status 1.", theme), 8)

    def test_curses_theme_uses_available_color_pairs(self) -> None:
        curses = FakeColorCurses()

        theme = build_curses_theme(curses)

        self.assertTrue(curses.started)
        self.assertIn((1, curses.COLOR_CYAN, -1), curses.pairs)
        self.assertIn((5, curses.COLOR_RED, -1), curses.pairs)
        self.assertEqual(theme.user_header, curses.A_BOLD | curses.color_pair(1))
        self.assertEqual(theme.user_body, curses.color_pair(1))
        self.assertEqual(theme.assistant_body, curses.color_pair(2))
        self.assertEqual(theme.assistant_final_body, curses.color_pair(3))
        self.assertNotEqual(theme.user_header, theme.user_body)
        self.assertEqual(theme.status_error, curses.A_BOLD | curses.color_pair(5))
        self.assertNotEqual(theme.user_header, theme.assistant_header)

    def test_preview_visible_lines_scroll_and_clamp(self) -> None:
        lines = ["one", "two", "three", "four"]

        self.assertEqual(visible_lines(lines, width=20, height=2, top=1), ["two", "three"])
        self.assertEqual(visible_lines(lines, width=20, height=2, top=99), ["three", "four"])

    def test_scroll_position_label_summarizes_visible_range(self) -> None:
        self.assertEqual(scroll_position_label(0, height=5, top=0), "empty")
        self.assertEqual(scroll_position_label(3, height=5, top=0), "all 3")
        self.assertEqual(scroll_position_label(20, height=7, top=5), "6-12/20")
        self.assertEqual(scroll_position_label(20, height=7, top=99), "14-20/20")

    def test_draw_header_includes_preview_scroll_position(self) -> None:
        app = TuiApp(
            [sample_thread()],
            lambda _thread, _prompt, _stdout: 0,
            preview_top=5,
        )
        app.preview_lines = lambda _thread, _width=None: [f"line {index}" for index in range(20)]  # type: ignore[method-assign]
        screen = RecordingWindow(height=12, width=80)
        app.stdscr = screen

        app.draw()

        self.assertEqual(screen.text_at(1, 28), "Preview [chat] asst final user files | 6-12/20")

    def test_draw_app_header_includes_selected_session_context(self) -> None:
        app = TuiApp(
            [sample_thread("019f-test-one"), sample_thread("019f-test-two")],
            lambda _thread, _prompt, _stdout: 0,
            selected=1,
        )
        screen = RecordingWindow(height=12, width=80)
        app.stdscr = screen

        app.draw()

        self.assertEqual(screen.text_at(0, 0), "CodexTUI | 2/2 019f-tes Build a TUI")

    def test_preview_header_shows_active_mode_tab_and_responsive_fallback(self) -> None:
        self.assertEqual(
            preview_header("assistant", "all 4", width=80),
            "Preview chat [asst] final user files | all 4",
        )
        self.assertEqual(preview_header("assistant", "6-12/20", width=28), "assistant | 6-12/20")

    def test_stream_header_keeps_context_in_chrome(self) -> None:
        self.assertEqual(
            stream_header("resume 019f-test Build a TUI", "2-5/6", width=50),
            " CodexTUI Stream | resume 019f-test B... | 2-5/6 ",
        )

    def test_stream_footer_tracks_live_and_review_state(self) -> None:
        self.assertEqual(
            stream_footer_help("codex exec --json", reviewing=False),
            "codex exec --json | live: capturing output",
        )
        self.assertEqual(
            stream_footer_help("codex exec resume --json", reviewing=True),
            "codex exec resume --json | review: arrows/PgUp/PgDn scroll | enter/q return",
        )

    def test_stream_footer_uses_width_aware_variants(self) -> None:
        review_footer = stream_footer_help("codex exec resume --json", reviewing=True, width=50)
        live_footer = stream_footer_help("codex exec --json", reviewing=False, width=30)

        self.assertEqual(review_footer, "resume | review: arrows/PgUp/PgDn | enter/q")
        self.assertEqual(live_footer, "exec | live capture")
        self.assertLessEqual(len(review_footer), 49)
        self.assertLessEqual(len(live_footer), 29)

    def test_stream_completion_line_is_task_status_activity(self) -> None:
        theme = TuiTheme(status_muted=4, status_error=8)

        self.assertEqual(
            stream_completion_line("Stream finished."),
            "[task] Stream finished.",
        )
        self.assertEqual(line_attr(stream_completion_line("Stream finished."), theme), 4)
        self.assertEqual(line_attr(stream_completion_line("Stream exited with status 1."), theme), 8)

    def test_footer_help_tracks_focus_and_empty_state(self) -> None:
        self.assertEqual(
            footer_help("sessions", has_threads=True),
            "sessions: arrows select | enter resume | n new | r refresh | tab preview | q quit",
        )
        self.assertIn("o files", footer_help("preview", has_threads=True))
        self.assertEqual(
            footer_help("sessions", has_threads=False),
            "n new prompt | r refresh | q quit | ctui doctor for setup",
        )

    def test_footer_help_uses_width_aware_variants(self) -> None:
        sessions_footer = footer_help("sessions", has_threads=True, width=80)
        preview_footer = footer_help("preview", has_threads=True, width=80)
        narrow_preview_footer = footer_help("preview", has_threads=True, width=50)

        self.assertEqual(
            sessions_footer,
            "sessions: up/down | enter resume | n new | r refresh | tab preview | q quit",
        )
        self.assertEqual(
            preview_footer,
            "preview: scroll | v chat | a asst | f final | u user | o files | tab | q quit",
        )
        self.assertEqual(narrow_preview_footer, "scroll | modes v/a/f/u/o | tab | q")
        self.assertLessEqual(len(sessions_footer), 79)
        self.assertLessEqual(len(preview_footer), 79)
        self.assertLessEqual(len(narrow_preview_footer), 49)

    def test_draw_footer_uses_terminal_width(self) -> None:
        app = TuiApp(
            [sample_thread()],
            lambda _thread, _prompt, _stdout: 0,
            focus="preview",
        )
        app.preview_lines = lambda _thread, _width=None: ["conversation"]  # type: ignore[method-assign]
        screen = RecordingWindow(height=12, width=80)
        app.stdscr = screen

        app.draw()

        self.assertEqual(
            screen.text_at(10, 0),
            "preview: scroll | v chat | a asst | f final | u user | o files | tab | q quit",
        )

    def test_draw_header_includes_session_scroll_position(self) -> None:
        threads = [sample_thread(f"019f-test-{idx}") for idx in range(8)]
        app = TuiApp(
            threads,
            lambda _thread, _prompt, _stdout: 0,
            selected=5,
        )
        screen = RecordingWindow(height=12, width=80)
        app.stdscr = screen

        app.draw()

        self.assertEqual(screen.text_at(1, 0), "Sessions | 3-6/8")

    def test_visible_session_count_tracks_two_line_rows(self) -> None:
        self.assertEqual(visible_session_count(1), 1)
        self.assertEqual(visible_session_count(4), 2)
        self.assertEqual(visible_session_count(5), 2)

    def test_session_row_lines_include_title_and_scan_metadata(self) -> None:
        thread = sample_thread("019f-session-row")

        title_line, metadata_line = session_row_lines(thread, ">", width=80)

        self.assertEqual(title_line, "> Build a TUI")
        self.assertIn("019f-ses", metadata_line)
        self.assertIn("cli", metadata_line)
        self.assertRegex(metadata_line, r"\d\d-\d\d \d\d:\d\d")
        self.assertNotIn("2026-", metadata_line)
        self.assertIn("/tmp/project", metadata_line)

    def test_session_row_lines_keep_project_visible_at_narrow_width(self) -> None:
        thread = sample_thread("019f-session-row")

        _title_line, metadata_line = session_row_lines(thread, ">", width=26)

        self.assertEqual(metadata_line, "  019f-ses cli project")
        self.assertLessEqual(len(metadata_line), 25)

    def test_session_row_lines_reserve_final_terminal_column(self) -> None:
        thread = sample_thread("019f-session-width")

        title_line, metadata_line = session_row_lines(thread, ">", width=12)

        self.assertLessEqual(len(title_line), 11)
        self.assertLessEqual(len(metadata_line), 11)

    def test_draw_sessions_uses_two_line_rows_and_metadata_styling(self) -> None:
        app = TuiApp(
            [sample_thread("019f-test-one"), sample_thread("019f-test-two")],
            lambda _thread, _prompt, _stdout: 0,
            selected=1,
            theme=TuiTheme(selection=9, status_muted=4),
        )
        screen = RecordingWindow()
        app.stdscr = screen

        app.draw_sessions(width=42, height=4)

        self.assertEqual(screen.text_at(2, 0), "  Build a TUI")
        self.assertIn("019f-tes", screen.text_at(3, 0))
        self.assertEqual(screen.attr_at(3, 0), 4)
        self.assertEqual(screen.text_at(4, 0), "> Build a TUI")
        self.assertEqual(screen.attr_at(4, 0), 9)
        self.assertEqual(screen.attr_at(5, 0), 9)

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

    def test_draw_stream_header_includes_scroll_position(self) -> None:
        app = TuiApp(
            [sample_thread()],
            lambda _thread, _prompt, _stdout: 0,
            stream_top=1,
        )
        app.stream_lines = ["one", "two", "three", "four", "five", "six"]
        screen = RecordingWindow(height=7, width=50)
        app.stdscr = screen

        app.draw_stream()

        self.assertEqual(screen.text_at(0, 0), " CodexTUI Stream | resume | 2-5/6 ")

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

    def test_tui_chat_preview_starts_at_conversation_without_metadata_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout.jsonl"
            records = [
                {
                    "timestamp": "2026-07-10T12:00:00.000Z",
                    "type": "session_meta",
                    "payload": {"id": "019f-test-conversation", "cwd": "/tmp/project", "source": "cli"},
                },
                {
                    "timestamp": "2026-07-10T12:00:01.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "Inspect the transcript",
                        "images": [],
                    },
                },
                {
                    "timestamp": "2026-07-10T12:00:02.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "phase": "final_answer",
                        "message": "The preview starts with chat.",
                    },
                },
            ]
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )
            thread = ThreadRow(
                id="019f-test-conversation",
                title="Conversation first",
                cwd="/tmp/project",
                source="cli",
                archived=False,
                rollout_path=str(path),
                created_at_ms=1783677600000,
                updated_at_ms=1783677605000,
                recency_at_ms=1783677605000,
                preview="",
                first_user_message="Inspect the transcript",
            )

            app = TuiApp([thread], lambda _thread, _prompt, _stdout: 0)
            lines = app.preview_lines(thread, width=80)

        rendered = "\n".join(lines)
        self.assertRegex(lines[0], r"^YOU \d\d:\d\d$")
        self.assertIn("Inspect the transcript", rendered)
        self.assertNotIn("2026-07-10", lines[0])
        self.assertNotIn("Codex session:", rendered)
        self.assertNotIn("File:", rendered)

    def test_preview_cache_tracks_terminal_width(self) -> None:
        thread = sample_thread("019f-test-width")
        app = TuiApp([thread], lambda _thread, _prompt, _stdout: 0)

        narrow = app.preview_lines(thread, width=30)
        wide = app.preview_lines(thread, width=80)

        self.assertIn((thread.id, "chat", 30), app.preview_cache)
        self.assertIn((thread.id, "chat", 80), app.preview_cache)
        self.assertIsNot(narrow, wide)

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
        self.assertEqual(app.stream_context_label, "new prompt")
        self.assertFalse(app.stream_reviewing)
        self.assertNotIn("CodexTUI streaming a new prompt via codex exec --json", app.stream_lines)
        self.assertEqual(app.stream_command_label, "codex exec --json")
        self.assertIn("new answer", app.stream_lines)
        self.assertIn(stream_completion_line("Stream finished."), app.stream_lines)
        self.assertEqual(app.threads, refreshed)
        self.assertEqual(app.selected, 0)
        self.assertEqual(app.top, 0)
        self.assertEqual(app.status, "Stream finished; refreshed 2 sessions.")

    def test_resume_stream_uses_selected_session_context_in_chrome(self) -> None:
        prompts: list[tuple[str, str]] = []

        def runner(thread: ThreadRow, prompt: str, stdout: StringIO) -> int:
            prompts.append((thread.id, prompt))
            stdout.write("resume answer\n")
            return 0

        app = TuiApp(
            [sample_thread("019f-test-old")],
            runner,
        )
        app.read_prompt = lambda _label: "Continue here"  # type: ignore[method-assign]
        app.draw_stream = lambda _current_line=None: None  # type: ignore[method-assign]
        app.review_stream = lambda: None  # type: ignore[method-assign]
        app.stdscr = FakeScreen()

        app.ask_selected()

        self.assertEqual(prompts, [("019f-test-old", "Continue here")])
        self.assertEqual(app.stream_context_label, "resume 019f-tes Build a TUI")
        self.assertNotIn("CodexTUI streaming 019f-tes via codex exec resume --json", app.stream_lines)
        self.assertEqual(app.stream_command_label, "codex exec resume --json")
        self.assertIn("resume answer", app.stream_lines)
        self.assertIn(stream_completion_line("Stream finished."), app.stream_lines)

    def test_resume_stream_refreshes_sessions_after_followup(self) -> None:
        prompts: list[tuple[str, str]] = []
        refreshed = [sample_thread("019f-test-new"), sample_thread("019f-test-old")]

        def runner(thread: ThreadRow, prompt: str, stdout: StringIO) -> int:
            prompts.append((thread.id, prompt))
            stdout.write("resume answer\n")
            return 0

        app = TuiApp(
            [sample_thread("019f-test-old")],
            runner,
            thread_loader=lambda: refreshed,
            preview_top=5,
        )
        app.preview_cache[("019f-test-old", "chat", 80)] = ["stale"]
        app.read_prompt = lambda _label: "Continue here"  # type: ignore[method-assign]
        app.draw_stream = lambda _current_line=None: None  # type: ignore[method-assign]
        app.review_stream = lambda: None  # type: ignore[method-assign]
        app.stdscr = FakeScreen()

        app.ask_selected()

        self.assertEqual(prompts, [("019f-test-old", "Continue here")])
        self.assertEqual(app.threads, refreshed)
        self.assertEqual(app.selected, 1)
        self.assertEqual(app.preview_top, 0)
        self.assertEqual(app.preview_cache, {})
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


class RecordingWindow:
    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.writes: list[tuple[int, int, str, int]] = []
        self.height = height
        self.width = width

    def getmaxyx(self) -> tuple[int, int]:
        return (self.height, self.width)

    def erase(self) -> None:
        self.writes.clear()

    def refresh(self) -> None:
        return None

    def addnstr(self, y: int, x: int, text: str, _limit: int, attr: int = 0) -> None:
        self.writes.append((y, x, text, attr))

    def text_at(self, y: int, x: int) -> str:
        for row, col, text, _attr in self.writes:
            if row == y and col == x:
                return text
        return ""

    def attr_at(self, y: int, x: int) -> int:
        for row, col, _text, attr in self.writes:
            if row == y and col == x:
                return attr
        return 0


class FakeColorCurses:
    A_REVERSE = 1
    A_BOLD = 2
    A_DIM = 4
    COLOR_CYAN = 10
    COLOR_BLUE = 11
    COLOR_GREEN = 12
    COLOR_YELLOW = 13
    COLOR_RED = 14
    COLOR_WHITE = 15
    COLOR_MAGENTA = 16

    def __init__(self) -> None:
        self.started = False
        self.pairs: list[tuple[int, int, int]] = []

    def has_colors(self) -> bool:
        return True

    def start_color(self) -> None:
        self.started = True

    def use_default_colors(self) -> None:
        return None

    def init_pair(self, pair: int, foreground: int, background: int) -> None:
        self.pairs.append((pair, foreground, background))

    def color_pair(self, pair: int) -> int:
        return pair * 256


if __name__ == "__main__":
    unittest.main()

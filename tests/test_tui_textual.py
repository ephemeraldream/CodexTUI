from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import path_bootstrap  # noqa: F401

from codex_tui.models import ThreadRow
from codex_tui import tui_textual
from codex_tui.tui_textual import (
    TRANSCRIPT_INNER_SCROLL_KEYS,
    TRANSCRIPT_SCROLL_STEP_LINES,
    build_history_entries,
    capture_clipboard_image,
    composer_display_text,
    entry_matches_query,
    image_paths_from_paste_text,
    is_conversation_thread,
    parse_composer_payload,
)


TEXTUAL_IMPORT_ERROR = tui_textual.TEXTUAL_IMPORT_ERROR


class TextualTuiModelTests(unittest.TestCase):
    def test_conversation_mode_hides_repeated_autonomous_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_one = thread_with_messages(root, "019f-run-one", "exec", ["Build the TUI"], ["{}"])
            run_two = thread_with_messages(root, "019f-run-two", "exec", ["Build the TUI"], ["{}"])
            dialog = thread_with_messages(root, "019f-dialog", "cli", ["Find bug"], ["Fixed."])

            conversations = build_history_entries([run_one, run_two, dialog], mode="conversations")
            runs = build_history_entries([run_one, run_two, dialog], mode="runs")
            all_entries = build_history_entries([run_one, run_two, dialog], mode="all")

        self.assertEqual([entry.thread.id for entry in conversations], ["019f-dialog"])
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].kind, "run_group")
        self.assertEqual(len(runs[0].threads), 2)
        self.assertEqual(len(all_entries), 2)

    def test_resumed_exec_thread_counts_as_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            thread = thread_with_messages(
                root,
                "019f-exec-dialog",
                "exec",
                ["Initial task", "Continue here"],
                ["First answer.", "Second answer."],
            )

            self.assertTrue(is_conversation_thread(thread))
            entries = build_history_entries([thread], mode="conversations")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].thread.id, "019f-exec-dialog")

    def test_single_turn_exec_with_human_answer_counts_as_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            status_run = thread_with_messages(root, "019f-status-run", "exec", ["Build the TUI"], ["{}"])
            dialog = thread_with_messages(root, "019f-new-dialog", "exec", ["Start something"], ["Done."])

            entries = build_history_entries([status_run, dialog], mode="conversations")

        self.assertEqual([entry.thread.id for entry in entries], ["019f-new-dialog"])

    def test_history_search_matches_transcript_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            thread = thread_with_messages(root, "019f-search", "cli", ["Question"], ["Needle appears here."])
            entries = build_history_entries([thread], mode="conversations", query="needle")
            entry = entries[0]

        self.assertEqual(len(entries), 1)
        self.assertTrue(entry_matches_query(entry, "needle"))
        self.assertFalse(entry_matches_query(entry, "missing"))

    def test_parse_composer_payload_extracts_image_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = parse_composer_payload(
                '/image screenshot.png @"nested/other image.jpg" describe both',
                cwd=root,
            )

        self.assertEqual(payload.prompt, "describe both")
        self.assertEqual(
            payload.image_paths,
            (
                str((root / "screenshot.png").resolve(strict=False)),
                str((root / "nested" / "other image.jpg").resolve(strict=False)),
            ),
        )
        self.assertIn("[Image 1] screenshot.png", composer_display_text(payload))
        self.assertIn("[Image 2] other image.jpg", composer_display_text(payload))

    def test_image_paths_from_paste_text_accepts_existing_image_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "screen shot.png"
            image_path.write_bytes(b"png")
            resolved = str(image_path.resolve())

            self.assertEqual(image_paths_from_paste_text(str(image_path)), (resolved,))
            self.assertEqual(image_paths_from_paste_text(image_path.as_uri()), (resolved,))
            self.assertEqual(image_paths_from_paste_text("ordinary pasted text"), ())

    def test_capture_clipboard_image_uses_pngpaste(self) -> None:
        def run_pngpaste(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            Path(command[1]).write_bytes(b"png")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("codex_tui.tui_textual.shutil.which", return_value="/usr/local/bin/pngpaste"):
            with patch("codex_tui.tui_textual.subprocess.run", side_effect=run_pngpaste):
                image_path, error = capture_clipboard_image()

        self.assertIsNone(error)
        self.assertIsNotNone(image_path)
        assert image_path is not None
        try:
            self.assertEqual(Path(image_path).suffix, ".png")
            self.assertTrue(Path(image_path).is_file())
        finally:
            Path(image_path).unlink(missing_ok=True)

    def test_capture_clipboard_image_reports_missing_pngpaste(self) -> None:
        with patch("codex_tui.tui_textual.shutil.which", return_value=None):
            image_path, error = capture_clipboard_image()

        self.assertIsNone(image_path)
        self.assertIn("pngpaste", error or "")

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_enter_opens_conversation_and_focuses_scrollable_transcript(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(
                    Path(temp_dir),
                    "019f-focus",
                    "cli",
                    ["Question"],
                    ["Answer\n\n" + "\n".join(f"line {index}" for index in range(40))],
                )
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 32)) as pilot:
                    await pilot.press("enter")
                    await pilot.pause()

                    self.assertEqual(getattr(app.focused, "id", ""), "transcript")

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_n_starts_new_dialog_and_focuses_composer(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(Path(temp_dir), "019f-existing", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.pause()

                    await pilot.press("n")
                    await pilot.pause()

                    title = str(app.query_one("#conversation-title", tui_textual.Static).render())
                    self.assertIsNone(app.current_thread)
                    self.assertTrue(app.new_dialog_active)
                    self.assertEqual(getattr(app.focused, "id", ""), "composer")
                    self.assertIn("New Codex dialog", title)
                    self.assertEqual(app.transcript_blocks[0].text, "New Codex dialog.")

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_i_and_c_focus_composer(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(Path(temp_dir), "019f-compose", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.pause()
                    self.assertEqual(getattr(app.focused, "id", ""), "thread-list")

                    await pilot.press("c")
                    await pilot.pause()
                    self.assertEqual(getattr(app.focused, "id", ""), "composer")
                    app.focus_history_list()
                    await pilot.pause()

                    await pilot.press("i")
                    await pilot.pause()
                    self.assertEqual(getattr(app.focused, "id", ""), "composer")

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_history_jk_moves_selection_and_updates_conversation(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                first = thread_with_messages(root, "019f-first", "cli", ["First"], ["First answer"])
                second = thread_with_messages(root, "019f-second", "cli", ["Second"], ["Second answer"])
                app = tui_textual.CodexTextualApp(lambda: [first, second])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.pause()
                    self.assertEqual(app.current_thread.id, "019f-first")

                    await pilot.press("j")
                    await pilot.pause()
                    self.assertEqual(app.current_thread.id, "019f-second")
                    self.assertEqual(getattr(app.focused, "id", ""), "thread-list")

                    await pilot.press("k")
                    await pilot.pause()
                    self.assertEqual(app.current_thread.id, "019f-first")

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_transcript_keys_move_between_blocks_and_scroll_view(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(
                    Path(temp_dir),
                    "019f-scroll",
                    "cli",
                    ["Question"],
                    ["Answer\n\n" + "\n".join(f"line {index}" for index in range(80))],
                )
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.press("enter")
                    await pilot.pause()
                    transcript = app.query_one("#transcript", tui_textual.ListView)
                    self.assertEqual(transcript.index, 0)

                    await pilot.press("j")
                    await pilot.pause()
                    self.assertEqual(transcript.index, 1)

                    await pilot.press("k")
                    await pilot.pause()
                    self.assertEqual(transcript.index, 0)

                    with patch.object(transcript, "action_page_up", wraps=transcript.action_page_up) as page_up:
                        await pilot.press("pageup")
                        await pilot.pause()
                    with patch.object(transcript, "action_page_down", wraps=transcript.action_page_down) as page_down:
                        await pilot.press("pagedown")
                        await pilot.pause()
                    with patch.object(transcript, "scroll_home", wraps=transcript.scroll_home) as home:
                        await pilot.press("home")
                        await pilot.pause()
                    with patch.object(transcript, "scroll_end", wraps=transcript.scroll_end) as end:
                        await pilot.press("end")
                        await pilot.pause()
                    with patch.object(transcript, "scroll_home", wraps=transcript.scroll_home) as gg_home:
                        await pilot.press("g", "g")
                        await pilot.pause()
                    with patch.object(transcript, "scroll_end", wraps=transcript.scroll_end) as shift_g:
                        await pilot.press("G")
                        await pilot.pause()

                    self.assertTrue(page_up.called)
                    self.assertTrue(page_down.called)
                    self.assertTrue(home.called)
                    self.assertTrue(end.called)
                    self.assertTrue(gg_home.called)
                    self.assertTrue(shift_g.called)

                    with patch.object(transcript, "scroll_relative", wraps=transcript.scroll_relative) as relative:
                        app.scroll_selected_transcript_block("ctrl+k")
                        app.scroll_selected_transcript_block("ctrl+j")

                    self.assertIn("ctrl+j", TRANSCRIPT_INNER_SCROLL_KEYS)
                    self.assertEqual(relative.call_args_list[0].kwargs["y"], -TRANSCRIPT_SCROLL_STEP_LINES)
                    self.assertEqual(relative.call_args_list[1].kwargs["y"], TRANSCRIPT_SCROLL_STEP_LINES)

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_history_pane_can_be_hidden_and_restored(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(Path(temp_dir), "019f-pane", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.press("enter")
                    await pilot.pause()

                    pane = app.query_one("#history-pane")
                    conversation = app.query_one("#conversation-pane")
                    width_with_history = conversation.region.width
                    self.assertTrue(pane.display)

                    with patch.object(app, "render_conversation", wraps=app.render_conversation) as render:
                        await pilot.press("b")
                        await pilot.pause()
                        self.assertEqual(render.call_count, 0)
                    self.assertFalse(pane.display)
                    self.assertEqual(str(pane.styles.width), "0")
                    self.assertEqual(str(pane.styles.min_width), "0")
                    self.assertGreater(conversation.region.width, width_with_history)
                    self.assertEqual(getattr(app.focused, "id", ""), "transcript")

                    await pilot.press("b")
                    await pilot.pause()
                    self.assertTrue(pane.display)
                    self.assertEqual(getattr(app.focused, "id", ""), "thread-list")

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_history_search_is_debounced_while_typing(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(Path(temp_dir), "019f-search-debounce", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    app.query_one("#history-search", tui_textual.Input).focus()
                    with patch.object(app, "refresh_history", wraps=app.refresh_history) as refresh:
                        await pilot.press("n", "e", "e", "d", "l", "e")
                        await pilot.pause()
                        self.assertEqual(refresh.call_count, 0)

                        await pilot.pause(tui_textual.SEARCH_DEBOUNCE_SECONDS + 0.1)
                        self.assertEqual(refresh.call_count, 1)

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_composer_paste_image_path_adds_pending_attachment(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                image_path = root / "screen.png"
                image_path.write_bytes(b"png")
                thread = thread_with_messages(root, "019f-paste-image", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.pause()

                    handled = app.handle_composer_paste_text(str(image_path))
                    await pilot.pause()

                    self.assertTrue(handled)
                    self.assertEqual(app.pending_image_paths, [str(image_path.resolve())])
                    self.assertEqual(getattr(app.focused, "id", ""), "composer")
                    help_text = str(app.query_one("#composer-help", tui_textual.Static).render())
                    self.assertIn("[Image 1]", help_text)

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_composer_ctrl_v_captures_clipboard_image_before_text_paste(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                image_path = root / "clipboard.png"
                image_path.write_bytes(b"png")
                thread = thread_with_messages(root, "019f-ctrl-v-image", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.pause()
                    composer = app.query_one("#composer", tui_textual.Input)
                    composer.focus()

                    with patch("codex_tui.tui_textual.capture_clipboard_image", return_value=(str(image_path), None)):
                        composer.action_paste()
                    await pilot.pause()

                    self.assertEqual(app.pending_image_paths, [str(image_path)])
                    self.assertIn("[Image 1]", str(app.query_one("#composer-help", tui_textual.Static).render()))

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_submit_from_new_dialog_starts_new_worker(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(Path(temp_dir), "019f-existing", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                calls: list[tuple[str, tuple[str, ...]]] = []
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.press("n")
                    await pilot.pause()
                    app.new_worker = lambda prompt, image_paths=(): calls.append((prompt, image_paths))  # type: ignore[method-assign]
                    app.run_worker = lambda worker, *_args, **_kwargs: worker()  # type: ignore[method-assign]

                    submitted = app.submit_composer("start a new one")
                    await pilot.pause()

                    self.assertTrue(submitted)
                    self.assertEqual(calls, [("start a new one", ())])
                    self.assertTrue(app.streaming)
                    self.assertEqual(app.thread_ids_before_new_stream, {"019f-existing"})

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_submit_writes_start_line_before_worker_output(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(Path(temp_dir), "019f-start-line", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.press("enter")
                    await pilot.pause()
                    app.run_worker = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

                    app.submit_composer("hello")
                    await pilot.pause()

                    rendered_lines = "\n".join(block.text for block in app.transcript_blocks)
                    self.assertIn("[task] Codex turn starting...", rendered_lines)
                    self.assertTrue(app.streaming)

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_finish_new_stream_opens_created_thread(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                existing = thread_with_messages(root, "019f-existing", "cli", ["Question"], ["Answer"])
                created = thread_with_messages(root, "019f-created", "exec", ["Start"], ["Created answer"])
                threads = [existing]
                app = tui_textual.CodexTextualApp(lambda: list(threads))
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.press("n")
                    await pilot.pause()
                    app.thread_ids_before_new_stream = {existing.id}
                    app.update_session_info_from_stream_record({"type": "thread.started", "thread_id": created.id})
                    threads.insert(0, created)

                    app.finish_new_stream(0)
                    await pilot.pause()

                    self.assertEqual(app.current_thread.id, created.id)
                    self.assertFalse(app.new_dialog_active)
                    self.assertEqual(app.entries[0].thread.id, created.id)
                    status = str(app.query_one("#status-line", tui_textual.Static).render())
                    self.assertIn("Codex finished.", status)

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_submit_uses_pending_clipboard_image_paths(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                image_path = root / "clipboard.png"
                image_path.write_bytes(b"png")
                thread = thread_with_messages(root, "019f-pending-image", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                calls: list[tuple[str, tuple[str, ...]]] = []

                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.press("enter")
                    await pilot.pause()
                    app.pending_image_paths.append(str(image_path))
                    app.resume_worker = lambda _thread, prompt, image_paths=(): calls.append((prompt, image_paths))  # type: ignore[method-assign]
                    app.run_worker = lambda worker, *_args, **_kwargs: worker()  # type: ignore[method-assign]

                    submitted = app.submit_composer("describe it")
                    await pilot.pause()

                    self.assertTrue(submitted)
                    self.assertEqual(calls, [("describe it", (str(image_path),))])
                    self.assertEqual(app.pending_image_paths, [])

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_submit_accepts_image_attachment_paths(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                image_path = root / "screen.png"
                image_path.write_bytes(b"png")
                thread = thread_with_messages(root, "019f-image", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.press("enter")
                    await pilot.pause()
                    app.run_worker = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

                    submitted = app.submit_composer(f"/image {image_path} describe it")
                    await pilot.pause()

                    rendered_lines = "\n".join(block.text for block in app.transcript_blocks)
                    self.assertTrue(submitted)
                    self.assertIn("[Image 1]", rendered_lines)
                    self.assertIn("screen.png", rendered_lines)
                    status = str(app.query_one("#status-line", tui_textual.Static).render())
                    self.assertIn("with 1 image(s)", status)

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_textual_stream_writer_flushes_multiline_blocks(self) -> None:
        class FakeApp:
            def __init__(self) -> None:
                self.blocks: list[str] = []

            def append_stream_block(self, block: str) -> None:
                self.blocks.append(block)

            def call_from_thread(self, callback, *args) -> None:
                callback(*args)

        app = FakeApp()
        writer = tui_textual.TextualStreamWriter(app)  # type: ignore[arg-type]

        writer.write("CODEX\n  hello")
        writer.write("\n")
        self.assertEqual(app.blocks, [])
        writer.flush()

        self.assertEqual(app.blocks, ["CODEX\n  hello"])

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_append_stream_block_skips_user_echo(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(Path(temp_dir), "019f-user-echo", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.press("enter")
                    await pilot.pause()
                    block_count = len(app.transcript_blocks)

                    app.append_stream_block("YOU\n  Question")
                    await pilot.pause()

                    self.assertEqual(len(app.transcript_blocks), block_count)

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_stream_renderable_uses_transcript_panel_for_codex_block(self) -> None:
        renderable = tui_textual.stream_renderable("CODEX final\n  Done.")

        self.assertIsInstance(renderable, tui_textual.Panel)

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_enter_expands_file_change_block(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(
                    Path(temp_dir),
                    "019f-diff",
                    "cli",
                    ["Question"],
                    ["Answer"],
                    extra_records=[
                        {
                            "timestamp": "2026-07-10T12:00:03.000Z",
                            "type": "response_item",
                            "payload": {
                                "type": "custom_tool_call",
                                "status": "completed",
                                "call_id": "call_1",
                                "name": "apply_patch",
                                "input": "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n*** End Patch\n",
                            },
                        }
                    ],
                )
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.press("enter")
                    await pilot.pause()
                    transcript = app.query_one("#transcript", tui_textual.ListView)
                    transcript.index = 2

                    await pilot.press("enter")
                    await pilot.pause()

                    self.assertEqual(app.transcript_blocks[2].kind, "file_change")
                    self.assertIn(app.transcript_blocks[2].id, app.expanded_block_ids)

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_status_line_shows_model_and_context_usage(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(
                    Path(temp_dir),
                    "019f-status",
                    "cli",
                    ["Question"],
                    ["Answer"],
                    extra_records=[
                        {
                            "timestamp": "2026-07-10T12:00:03.000Z",
                            "type": "response_item",
                            "payload": {"type": "message", "role": "assistant", "model": "gpt-5.5", "content": []},
                        },
                        {
                            "timestamp": "2026-07-10T12:00:04.000Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {"total_tokens": 1000},
                                    "model_context_window": 2000,
                                },
                            },
                        },
                    ],
                )
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.press("enter")
                    await pilot.pause()

                    status = str(app.query_one("#status-line", tui_textual.Static).render())
                    self.assertIn("model gpt-5.5", status)
                    self.assertIn("ctx 1k/2k 50%", status)

        asyncio.run(run_case())

    @unittest.skipIf(TEXTUAL_IMPORT_ERROR is not None, "Textual is not installed")
    def test_status_line_updates_from_live_token_event(self) -> None:
        async def run_case() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                thread = thread_with_messages(Path(temp_dir), "019f-live-status", "cli", ["Question"], ["Answer"])
                app = tui_textual.CodexTextualApp(lambda: [thread])
                async with app.run_test(size=(110, 24)) as pilot:
                    await pilot.press("enter")
                    await pilot.pause()

                    app.update_session_info_from_stream_record(
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {"total_tokens": 1500},
                                    "model_context_window": 3000,
                                },
                            },
                        }
                    )
                    await pilot.pause()

                    status = str(app.query_one("#status-line", tui_textual.Static).render())
                    self.assertIn("ctx 1.5k/3k 50%", status)

        asyncio.run(run_case())


def thread_with_messages(
    root: Path,
    thread_id: str,
    source: str,
    users: list[str],
    assistants: list[str],
    *,
    extra_records: list[dict[str, object]] | None = None,
) -> ThreadRow:
    path = root / f"{thread_id}.jsonl"
    records: list[dict[str, object]] = [
        {
            "timestamp": "2026-07-10T12:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": thread_id, "cwd": "/tmp/project", "source": source},
        }
    ]
    index = 1
    for user, assistant in zip(users, assistants):
        records.append(
            {
                "timestamp": f"2026-07-10T12:00:{index:02d}.000Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": user, "images": []},
            }
        )
        index += 1
        records.append(
            {
                "timestamp": f"2026-07-10T12:00:{index:02d}.000Z",
                "type": "event_msg",
                "payload": {"type": "agent_message", "phase": "final_answer", "message": assistant},
            }
        )
        index += 1
    if extra_records:
        records.extend(extra_records)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return ThreadRow(
        id=thread_id,
        title=users[0],
        cwd="/tmp/project",
        source=source,
        archived=False,
        rollout_path=str(path),
        created_at_ms=1783677600000,
        updated_at_ms=1783677605000,
        recency_at_ms=1783677605000,
        preview="",
        first_user_message=users[0],
    )


if __name__ == "__main__":
    unittest.main()

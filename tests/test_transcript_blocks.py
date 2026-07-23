from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import path_bootstrap  # noqa: F401

from codex_tui.models import ThreadRow
from codex_tui.transcript_blocks import (
    SessionInfo,
    context_text,
    patch_paths,
    session_footer_text,
    session_info_for_thread,
    session_info_from_record,
    transcript_blocks_for_thread,
)


class TranscriptBlockTests(unittest.TestCase):
    def test_patch_paths_extracts_changed_files(self) -> None:
        patch = """*** Begin Patch
*** Update File: src/app.py
@@
+print("hi")
*** Add File: tests/test_app.py
+def test_app():
+    pass
*** End Patch
"""

        self.assertEqual(patch_paths(patch), ["src/app.py", "tests/test_app.py"])

    def test_transcript_blocks_include_apply_patch_diff_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rollout = root / "rollout.jsonl"
            write_jsonl(
                rollout,
                [
                    session_meta("019f-blocks"),
                    user_message("2026-07-10T12:00:01.000Z", "Fix it"),
                    apply_patch_call(
                        "2026-07-10T12:00:02.000Z",
                        "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n*** End Patch\n",
                    ),
                    assistant_message("2026-07-10T12:00:03.000Z", "Done."),
                ],
            )
            thread = thread_for_rollout(rollout)

            blocks = transcript_blocks_for_thread(thread)

        self.assertEqual([block.kind for block in blocks], ["message", "file_change", "message"])
        change = blocks[1]
        self.assertEqual(change.text, "app.py")
        self.assertEqual(change.file_changes[0].path, "src/app.py")
        self.assertIn("-old", change.file_changes[0].diff)

    def test_transcript_blocks_include_top_level_apply_patch_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rollout = root / "rollout.jsonl"
            write_jsonl(
                rollout,
                [
                    session_meta("019f-top-level-patch"),
                    user_message("2026-07-10T12:00:01.000Z", "Fix it"),
                    top_level_apply_patch_item(
                        "2026-07-10T12:00:02.000Z",
                        "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n*** End Patch\n",
                    ),
                    assistant_message("2026-07-10T12:00:03.000Z", "Done."),
                ],
            )
            thread = thread_for_rollout(rollout)

            blocks = transcript_blocks_for_thread(thread)

        self.assertEqual([block.kind for block in blocks], ["message", "file_change", "message"])
        change = blocks[1]
        self.assertEqual(change.text, "app.py")
        self.assertEqual(change.file_changes[0].path, "src/app.py")

    def test_transcript_blocks_fold_large_tool_output_but_keep_detail(self) -> None:
        output = "\n".join(f"line {index}" for index in range(30))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rollout = root / "rollout.jsonl"
            write_jsonl(
                rollout,
                [
                    session_meta("019f-tool-output"),
                    user_message("2026-07-10T12:00:01.000Z", "Run tests"),
                    tool_call("2026-07-10T12:00:02.000Z", "call_1", "exec_command"),
                    tool_output("2026-07-10T12:00:03.000Z", "call_1", output),
                    assistant_message("2026-07-10T12:00:04.000Z", "Done."),
                ],
            )
            thread = thread_for_rollout(rollout)

            blocks = transcript_blocks_for_thread(thread)

        self.assertEqual([block.kind for block in blocks], ["message", "tool_output", "message"])
        folded = blocks[1]
        self.assertTrue(folded.expandable)
        self.assertIn("exec_command: folded 30 lines", folded.text)
        self.assertIn("line 7", folded.text)
        self.assertNotIn("line 29", folded.text)
        self.assertIn("line 29", folded.detail_text)

    def test_session_info_reads_model_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rollout = root / "rollout.jsonl"
            write_jsonl(
                rollout,
                [
                    session_meta("019f-info"),
                    {
                        "timestamp": "2026-07-10T12:00:01.000Z",
                        "type": "response_item",
                        "payload": {"type": "message", "role": "assistant", "model": "gpt-5.5", "content": []},
                    },
                    {
                        "timestamp": "2026-07-10T12:00:02.000Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {"total_tokens": 129200},
                                "model_context_window": 258400,
                            },
                            "rate_limits": None,
                        },
                    },
                ],
            )
            thread = thread_for_rollout(rollout)

            info = session_info_for_thread(thread)

        self.assertEqual(info.model, "gpt-5.5")
        self.assertEqual(info.context_tokens, 129200)
        self.assertEqual(info.context_window, 258400)
        self.assertEqual(session_footer_text(info), "model gpt-5.5 | ctx 129.2k/258.4k 50%")

    def test_context_text_handles_unknown_values(self) -> None:
        self.assertEqual(context_text(None, None), "?/?")
        self.assertEqual(context_text(1000, None), "1k")
        self.assertEqual(context_text(None, 2000), "?/2k")

    def test_session_info_from_record_updates_existing_footer_state(self) -> None:
        current = SessionInfo(model="gpt-5.5", context_tokens=100, context_window=None)
        record = {
            "timestamp": "2026-07-10T12:00:02.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"total_tokens": 2500},
                    "model_context_window": 5000,
                },
            },
        }

        info = session_info_from_record(record, current=current)

        self.assertEqual(info.model, "gpt-5.5")
        self.assertEqual(info.context_tokens, 2500)
        self.assertEqual(info.context_window, 5000)


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def session_meta(thread_id: str) -> dict[str, object]:
    return {
        "timestamp": "2026-07-10T12:00:00.000Z",
        "type": "session_meta",
        "payload": {"id": thread_id, "cwd": "/tmp/project", "source": "cli"},
    }


def user_message(timestamp: str, text: str) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {"type": "user_message", "message": text, "images": []},
    }


def assistant_message(timestamp: str, text: str) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {"type": "agent_message", "phase": "final_answer", "message": text},
    }


def apply_patch_call(timestamp: str, patch: str) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "apply_patch",
            "input": patch,
        },
    }


def top_level_apply_patch_item(timestamp: str, patch: str) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "item.completed",
        "item": {
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "apply_patch",
            "input": patch,
        },
    }


def tool_call(timestamp: str, call_id: str, name: str) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "status": "completed",
            "call_id": call_id,
            "name": name,
            "arguments": "{}",
        },
    }


def tool_output(timestamp: str, call_id: str, output: str) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        },
    }


def thread_for_rollout(path: Path) -> ThreadRow:
    return ThreadRow(
        id="019f-blocks",
        title="Fix it",
        cwd="/tmp/project",
        source="cli",
        archived=False,
        rollout_path=str(path),
        created_at_ms=1783677600000,
        updated_at_ms=1783677605000,
        recency_at_ms=1783677605000,
        preview="",
        first_user_message="Fix it",
    )


if __name__ == "__main__":
    unittest.main()

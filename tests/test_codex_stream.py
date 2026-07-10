from __future__ import annotations

import json
import unittest
from pathlib import Path

import path_bootstrap  # noqa: F401

from codex_plus.codex_stream import CodexStreamRenderer, codex_exec_command, text_from_json_line


class CodexStreamTests(unittest.TestCase):
    def test_event_agent_message_renders_as_stream_text(self) -> None:
        line = json_line(
            "event_msg",
            {"type": "agent_message", "phase": "commentary", "message": "I am checking the repo."},
        )

        self.assertEqual(text_from_json_line(line), "I am checking the repo.")

    def test_response_item_assistant_message_is_fallback_stream_text(self) -> None:
        line = json_line(
            "response_item",
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Fallback answer."}],
            },
        )

        self.assertEqual(text_from_json_line(line), "Fallback answer.")

    def test_renderer_suppresses_duplicate_high_level_and_response_messages(self) -> None:
        event_line = json_line(
            "event_msg",
            {"type": "agent_message", "phase": "final_answer", "message": "Done."},
        )
        response_line = json_line(
            "response_item",
            {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "Done."}],
            },
        )
        task_complete_line = json_line("event_msg", {"type": "task_complete", "last_agent_message": "Done."})
        renderer = CodexStreamRenderer()

        self.assertEqual(renderer.render_line(event_line), "Done.")
        self.assertIsNone(renderer.render_line(response_line))
        self.assertIsNone(renderer.render_line(task_complete_line))

    def test_renderer_passes_non_json_stdout_through(self) -> None:
        renderer = CodexStreamRenderer()

        self.assertEqual(renderer.render_line("plain output\n"), "plain output")

    def test_autonomous_status_json_is_not_streamed_as_codex_text(self) -> None:
        line = json_line(
            "event_msg",
            {
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
        )

        self.assertIsNone(text_from_json_line(line))

    def test_codex_exec_command_uses_json_mode_for_new_prompt(self) -> None:
        command = codex_exec_command(Path("/tmp/codex"), prompt="Fix the bug", resume_id=None)

        self.assertEqual(command, ["/tmp/codex", "exec", "--json", "Fix the bug"])

    def test_codex_exec_command_uses_json_mode_for_resume(self) -> None:
        command = codex_exec_command(Path("/tmp/codex"), prompt="Continue", resume_id="019f-test")

        self.assertEqual(command, ["/tmp/codex", "exec", "resume", "--json", "019f-test", "Continue"])


def json_line(record_type: str, payload: dict[str, object]) -> str:
    return json.dumps({"type": record_type, "payload": payload})


if __name__ == "__main__":
    unittest.main()

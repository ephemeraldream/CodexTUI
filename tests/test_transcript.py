from __future__ import annotations

import unittest
from pathlib import Path

import path_bootstrap  # noqa: F401

from codex_plus.models import ThreadRow
from codex_plus.transcript import filter_messages, read_messages, render_thread


FIXTURES = Path(__file__).parent / "fixtures"


class TranscriptTests(unittest.TestCase):
    def test_event_messages_hide_developer_and_tool_noise(self) -> None:
        messages = read_messages(FIXTURES / "rollout-basic.jsonl")
        self.assertEqual([message.role for message in messages], ["user", "assistant", "assistant"])
        text = "\n".join(message.text for message in messages)
        self.assertIn("Find the bug", text)
        self.assertIn("The bug is fixed.", text)
        self.assertNotIn("developer context", text)
        self.assertNotIn("pytest", text)

    def test_final_filter_returns_final_answer(self) -> None:
        messages = read_messages(FIXTURES / "rollout-basic.jsonl")
        finals = filter_messages(messages, "final")
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0].text, "The bug is fixed.")

    def test_response_item_fallback_skips_bootstrap_context(self) -> None:
        messages = read_messages(FIXTURES / "rollout-response-item-fallback.jsonl")
        self.assertEqual([message.role for message in messages], ["user", "assistant"])
        self.assertEqual(messages[0].text, "Run the autonomous iteration")
        self.assertEqual(messages[1].text, '{"success": true}')

    def test_event_user_messages_skip_bootstrap_context(self) -> None:
        messages = read_messages(FIXTURES / "rollout-event-bootstrap.jsonl")
        self.assertEqual([message.role for message in messages], ["user", "assistant"])
        self.assertEqual(messages[0].text, "Show me the final answer")
        self.assertNotIn("hidden bootstrap", "\n".join(message.text for message in messages))

    def test_render_thread_includes_header_and_messages(self) -> None:
        thread = ThreadRow(
            id="019f-test-basic",
            title="Find the bug",
            cwd="/tmp/project",
            source="cli",
            archived=False,
            rollout_path=str(FIXTURES / "rollout-basic.jsonl"),
            created_at_ms=1783677600000,
            updated_at_ms=1783677605000,
            recency_at_ms=1783677605000,
            preview="",
            first_user_message="Find the bug",
        )
        rendered = render_thread(thread, mode="assistant")
        self.assertIn("Codex session: 019f-tes", rendered)
        self.assertIn("I will inspect the failing path.", rendered)
        self.assertIn("The bug is fixed.", rendered)
        self.assertNotIn("Find the bug\n\n[", rendered)


if __name__ == "__main__":
    unittest.main()

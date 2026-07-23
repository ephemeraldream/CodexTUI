from __future__ import annotations

import json
from io import StringIO
import unittest
from pathlib import Path
from unittest.mock import patch

import path_bootstrap  # noqa: F401

from codex_tui.codex_stream import (
    CodexStreamRenderer,
    codex_exec_command,
    file_change_detail_from_stream_record,
    run_codex_json_stream,
    text_from_json_line,
)


class CodexStreamTests(unittest.TestCase):
    def test_event_agent_message_renders_as_stream_text(self) -> None:
        line = json_line(
            "event_msg",
            {
                "type": "agent_message",
                "phase": "commentary",
                "message": "I am checking the repo.\nNext step.",
            },
        )

        self.assertEqual(
            text_from_json_line(line), "CODEX\n  I am checking the repo.\n  Next step."
        )

    def test_response_item_assistant_message_is_fallback_stream_text(self) -> None:
        line = json_line(
            "response_item",
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Fallback answer."}],
            },
        )

        self.assertEqual(text_from_json_line(line), "CODEX\n  Fallback answer.")

    def test_renderer_suppresses_duplicate_high_level_and_response_messages(
        self,
    ) -> None:
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
        task_complete_line = json_line(
            "event_msg", {"type": "task_complete", "last_agent_message": "Done."}
        )
        renderer = CodexStreamRenderer()

        self.assertEqual(renderer.render_line(event_line), "CODEX final\n  Done.")
        self.assertIsNone(renderer.render_line(response_line))
        self.assertIsNone(renderer.render_line(task_complete_line))

    def test_renderer_keeps_repeated_identical_tool_activity(self) -> None:
        def tool_call_line(call_id: str) -> str:
            return json_line(
                "response_item",
                {
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": "python3 -m unittest discover -s tests"}),
                },
            )

        renderer = CodexStreamRenderer()
        rendered = "[tool] exec_command: python3 -m unittest discover -s tests"

        self.assertEqual(renderer.render_line(tool_call_line("call_1")), rendered)
        self.assertEqual(renderer.render_line(tool_call_line("call_2")), rendered)

    def test_renderer_allows_same_message_after_new_turn_starts(self) -> None:
        message_line = json_line(
            "event_msg",
            {"type": "agent_message", "phase": "final_answer", "message": "Done."},
        )
        renderer = CodexStreamRenderer()

        self.assertEqual(
            renderer.render_line(json.dumps({"type": "turn.started"})),
            "[task] Codex turn started.",
        )
        self.assertEqual(renderer.render_line(message_line), "CODEX final\n  Done.")
        self.assertEqual(
            renderer.render_line(json.dumps({"type": "turn.started"})),
            "[task] Codex turn started.",
        )
        self.assertEqual(renderer.render_line(message_line), "CODEX final\n  Done.")

    def test_renderer_passes_non_json_stdout_through(self) -> None:
        renderer = CodexStreamRenderer()

        self.assertEqual(renderer.render_line("plain output\n"), "plain output")

    def test_renderer_suppresses_json_events_without_user_text(self) -> None:
        renderer = CodexStreamRenderer()

        self.assertIsNone(
            renderer.render_line(
                json.dumps({"type": "thread.started", "thread_id": "019f-test"})
            )
        )
        self.assertIsNone(
            renderer.render_line(
                json.dumps({"type": "unknown.event", "detail": "hidden"})
            )
        )

    def test_renderer_streams_top_level_turn_started(self) -> None:
        renderer = CodexStreamRenderer()

        self.assertEqual(
            renderer.render_line(json.dumps({"type": "turn.started"})),
            "[task] Codex turn started.",
        )

    def test_renderer_streams_top_level_agent_message_item(self) -> None:
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "agent_message",
                    "text": "Ок, ничего не делаю.",
                },
            }
        )

        self.assertEqual(text_from_json_line(line), "CODEX\n  Ок, ничего не делаю.")

    def test_renderer_streams_top_level_completed_message_items(self) -> None:
        assistant_line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "Content reply."}],
                },
            }
        )
        user_line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_2",
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Content request."}],
                },
            }
        )

        self.assertEqual(
            text_from_json_line(assistant_line), "CODEX final\n  Content reply."
        )
        self.assertEqual(text_from_json_line(user_line), "YOU\n  Content request.")

    def test_renderer_streams_top_level_turn_usage(self) -> None:
        line = json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 3300206,
                    "cached_input_tokens": 2847488,
                    "output_tokens": 26526,
                    "reasoning_output_tokens": 10984,
                },
            }
        )

        self.assertEqual(
            text_from_json_line(line),
            "[tokens] input 3.3m, cached 2.8m, output 26.5k, reasoning 11k",
        )

    def test_renderer_summarizes_turn_failure_without_raw_json(self) -> None:
        line = json.dumps(
            {
                "type": "turn.failed",
                "error": {
                    "code": "auth_expired",
                    "metadata": {"token": "secret-ish"},
                    "retryable": False,
                },
            }
        )

        rendered = text_from_json_line(line)

        self.assertEqual(
            rendered, "[task] Codex turn failed: code auth_expired, not retryable"
        )
        self.assertNotIn("{", rendered or "")
        self.assertNotIn("metadata", rendered or "")
        self.assertNotIn("secret-ish", rendered or "")

    def test_renderer_streams_tool_call_and_output_activity(self) -> None:
        call_line = json_line(
            "response_item",
            {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "pytest", "workdir": "/tmp/project"}),
                "call_id": "call_1",
            },
        )
        output_line = json_line(
            "response_item",
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "2 failed, 1 passed\n",
            },
        )
        renderer = CodexStreamRenderer()

        self.assertEqual(
            renderer.render_line(call_line),
            "[tool] exec_command: pytest (cwd: /tmp/project)",
        )
        self.assertEqual(
            renderer.render_line(output_line),
            "[tool output] exec_command\n2 failed, 1 passed",
        )

    def test_renderer_folds_long_tool_output_activity(self) -> None:
        call_line = json_line(
            "response_item",
            {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "pytest"}),
                "call_id": "call_long",
            },
        )
        output = "\n".join(f"line {index}" for index in range(30))
        output_line = json_line(
            "response_item",
            {
                "type": "function_call_output",
                "call_id": "call_long",
                "output": output,
            },
        )
        renderer = CodexStreamRenderer()

        self.assertEqual(renderer.render_line(call_line), "[tool] exec_command: pytest")
        rendered = renderer.render_line(output_line)

        self.assertIsNotNone(rendered)
        assert rendered is not None
        self.assertIn("[tool output] exec_command: folded 30 lines", rendered)
        self.assertIn("showing preview\nline 0\nline 1", rendered)
        self.assertIn("line 7", rendered)
        self.assertNotIn("line 8", rendered)
        self.assertNotIn("line 29", rendered)

    def test_renderer_streams_patch_and_task_activity(self) -> None:
        task_line = json_line(
            "event_msg", {"type": "task_started", "turn_id": "turn_1"}
        )
        patch_line = json_line(
            "event_msg",
            {
                "type": "patch_apply_end",
                "call_id": "call_2",
                "success": True,
                "changes": {
                    "/tmp/project/src/app.py": {},
                    "/tmp/project/tests/test_app.py": {},
                },
            },
        )
        renderer = CodexStreamRenderer()

        self.assertEqual(renderer.render_line(task_line), "[task] Codex turn started.")
        self.assertEqual(
            renderer.render_line(patch_line),
            "[tool] apply_patch applied: app.py, test_app.py",
        )

    def test_renderer_streams_apply_patch_call_with_changed_paths(self) -> None:
        patch_text = (
            "*** Begin Patch\n"
            "*** Update File: src/app.py\n"
            "@@\n"
            "-old\n"
            "+new\n"
            "*** Add File: tests/test_app.py\n"
            "+def test_app():\n"
            "+    pass\n"
            "*** End Patch\n"
        )
        record = {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "apply_patch",
                "call_id": "call_patch",
                "input": patch_text,
            },
        }
        line = json.dumps(record)

        rendered = text_from_json_line(line)
        detail = file_change_detail_from_stream_record(record, call_labels={})

        self.assertEqual(rendered, "[tool] apply_patch: app.py, test_app.py")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail.rendered, "[tool] apply_patch: app.py, test_app.py")
        self.assertEqual(detail.patch_text, patch_text)
        self.assertNotIn("*** Begin Patch", rendered or "")

    def test_renderer_streams_completed_plan_and_thread_rollback(self) -> None:
        plan_line = json_line(
            "event_msg",
            {
                "type": "item_completed",
                "item": {
                    "type": "Plan",
                    "text": "# Plan\n\n1. Inspect the TUI.\n2. Add scrollback.",
                },
            },
        )
        rollback_line = json_line(
            "event_msg", {"type": "thread_rolled_back", "num_turns": 1}
        )
        renderer = CodexStreamRenderer()

        self.assertEqual(
            renderer.render_line(plan_line),
            "[plan] completed\n# Plan\n\n1. Inspect the TUI.\n2. Add scrollback.",
        )
        self.assertEqual(
            renderer.render_line(rollback_line), "[thread] rolled back 1 turn."
        )

    def test_renderer_streams_completed_reasoning_item_summary(self) -> None:
        line = json_line(
            "event_msg",
            {
                "type": "item_completed",
                "item": {
                    "type": "reasoning",
                    "summary": [
                        {
                            "type": "summary_text",
                            "text": "Checked the history fallback.",
                        }
                    ],
                },
            },
        )

        rendered = text_from_json_line(line)

        self.assertEqual(rendered, "[reasoning] Checked the history fallback.")
        self.assertNotIn("[item]", rendered or "")

    def test_renderer_summarizes_unknown_completed_item_without_raw_json(self) -> None:
        line = json_line(
            "event_msg",
            {
                "type": "item_completed",
                "item": {
                    "id": "item_opaque",
                    "type": "browser_action",
                    "name": "open_page",
                    "status": "completed",
                    "arguments": {"url": "https://example.com"},
                },
            },
        )

        rendered = text_from_json_line(line)

        self.assertEqual(rendered, "[item] browser_action completed: open_page")
        self.assertNotIn("{", rendered or "")
        self.assertNotIn("arguments", rendered or "")

    def test_renderer_streams_top_level_compaction_once(self) -> None:
        compacted_line = json.dumps(
            {
                "type": "compacted",
                "payload": {
                    "message": "",
                    "replacement_history": [],
                    "window_number": 2,
                },
            }
        )
        event_line = json_line("event_msg", {"type": "context_compacted"})
        renderer = CodexStreamRenderer()

        self.assertEqual(renderer.render_line(compacted_line), "[context] compacted")
        self.assertIsNone(renderer.render_line(event_line))

    def test_top_level_compaction_message_is_streamed(self) -> None:
        line = json.dumps(
            {
                "type": "compacted",
                "payload": {
                    "message": "older turns summarized",
                    "replacement_history": [],
                },
            }
        )

        self.assertEqual(
            text_from_json_line(line), "[context] compacted: older turns summarized"
        )

    def test_renderer_streams_token_count_status(self) -> None:
        line = json_line(
            "event_msg",
            {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"total_tokens": 71625},
                    "total_token_usage": {"total_tokens": 231517},
                    "model_context_window": 258400,
                },
                "rate_limits": {
                    "primary": {"used_percent": 6.0},
                    "secondary": {"used_percent": 43.5},
                },
            },
        )

        self.assertEqual(
            text_from_json_line(line),
            "[tokens] last 71.6k, session 231.5k, context 231.5k / 258.4k (89.6%), rate primary 6%/secondary 43.5%",
        )

    def test_renderer_streams_token_count_limit_status(self) -> None:
        line = json_line(
            "event_msg",
            {
                "type": "token_count",
                "rate_limits": {
                    "primary": {"used_percent": "100"},
                    "rate_limit_reached_type": "primary",
                },
            },
        )

        self.assertEqual(
            text_from_json_line(line),
            "[tokens] rate primary 100%, limit reached: primary",
        )

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

    def test_renderer_streams_user_message_events(self) -> None:
        line = json_line(
            "event_msg", {"type": "user_message", "message": "Fix the failing test."}
        )

        self.assertEqual(text_from_json_line(line), "YOU\n  Fix the failing test.")

    def test_renderer_cleans_autonomous_user_message_events(self) -> None:
        line = json_line(
            "event_msg",
            {
                "type": "user_message",
                "message": (
                    "You are working autonomously towards an objective given below.\n"
                    "This is iteration 26.\n\n"
                    "## Instructions\n\n"
                    "1. Read notes first.\n\n"
                    "## Objective\n\n"
                    "Build a CodexTUI-owned TUI."
                ),
            },
        )

        self.assertEqual(
            text_from_json_line(line), "YOU\n  Build a CodexTUI-owned TUI."
        )

    def test_renderer_suppresses_bootstrap_user_message_events(self) -> None:
        line = json_line(
            "event_msg",
            {
                "type": "user_message",
                "message": "# AGENTS.md instructions\n\n<environment_context>hidden</environment_context>",
            },
        )

        self.assertIsNone(text_from_json_line(line))
        self.assertIsNone(CodexStreamRenderer().render_line(line))

    def test_codex_exec_command_uses_json_mode_for_new_prompt(self) -> None:
        command = codex_exec_command(
            Path("/tmp/codex"), prompt="Fix the bug", resume_id=None
        )

        self.assertEqual(command, ["/tmp/codex", "exec", "--json", "Fix the bug"])

    def test_codex_exec_command_uses_json_mode_for_resume(self) -> None:
        command = codex_exec_command(
            Path("/tmp/codex"), prompt="Continue", resume_id="019f-test"
        )

        self.assertEqual(
            command, ["/tmp/codex", "exec", "resume", "--json", "019f-test", "Continue"]
        )

    def test_codex_exec_command_attaches_images_for_new_prompt(self) -> None:
        command = codex_exec_command(
            Path("/tmp/codex"),
            prompt="Describe this",
            resume_id=None,
            image_paths=["/tmp/one.png", "/tmp/two.jpg"],
        )

        self.assertEqual(
            command,
            [
                "/tmp/codex",
                "exec",
                "--json",
                "--image",
                "/tmp/one.png",
                "--image",
                "/tmp/two.jpg",
                "Describe this",
            ],
        )

    def test_codex_exec_command_attaches_images_for_resume_prompt(self) -> None:
        command = codex_exec_command(
            Path("/tmp/codex"),
            prompt="Continue with this",
            resume_id="019f-test",
            image_paths=["/tmp/one.png"],
        )

        self.assertEqual(
            command,
            [
                "/tmp/codex",
                "exec",
                "resume",
                "--json",
                "--image",
                "/tmp/one.png",
                "019f-test",
                "Continue with this",
            ],
        )

    def test_run_codex_json_stream_reports_structured_events_to_callback(self) -> None:
        lines = [
            json_line("event_msg", {"type": "task_started", "model_context_window": 258400}) + "\n",
            "plain stderr\n",
        ]
        process = FakeProcess(lines)
        events: list[dict[str, object]] = []
        output = StringIO()

        with patch("codex_tui.codex_stream.subprocess.Popen", return_value=process):
            code = run_codex_json_stream(["codex"], stdout=output, event_callback=events.append)

        self.assertEqual(code, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "event_msg")
        self.assertIn("[task] Codex turn started.", output.getvalue())
        self.assertIn("plain stderr", output.getvalue())


def json_line(record_type: str, payload: dict[str, object]) -> str:
    return json.dumps({"type": record_type, "payload": payload})


class FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = iter(lines)

    def wait(self) -> int:
        return 0


if __name__ == "__main__":
    unittest.main()

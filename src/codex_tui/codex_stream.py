from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, TextIO

from .transcript import (
    clean_user_text,
    looks_like_autonomous_status_update,
    looks_like_bootstrap_context,
    text_from_payload,
)


TOOL_OUTPUT_FOLD_LINE_LIMIT = 20
TOOL_OUTPUT_FOLD_BYTE_LIMIT = 4_000
TOOL_OUTPUT_PREVIEW_LINES = 8
TOOL_OUTPUT_PREVIEW_CHARS = 1_200


@dataclass
class CodexStreamRenderer:
    last_text: str = ""
    call_labels: dict[str, str] = field(default_factory=dict)

    def render_line(self, line: str) -> str | None:
        parsed, text = parse_stream_line(line, call_labels=self.call_labels)
        if not parsed:
            return line.rstrip("\n") if line.strip() else None
        if text is None:
            return None
        stripped = text.rstrip()
        if not stripped or stripped == self.last_text:
            return None
        self.last_text = stripped
        return stripped


def codex_exec_command(
    codex_bin: Path,
    *,
    prompt: str | None,
    resume_id: str | None = None,
    image_paths: Iterable[str | Path] = (),
) -> list[str]:
    command = [str(codex_bin), "exec"]
    if resume_id:
        command.extend(["resume", "--json"])
        for image_path in image_paths:
            command.extend(["--image", str(image_path)])
        command.append(resume_id)
    else:
        command.append("--json")
        for image_path in image_paths:
            command.extend(["--image", str(image_path)])
    if prompt:
        command.append(prompt)
    return command


def text_from_json_line(
    line: str,
    *,
    call_labels: dict[str, str] | None = None,
) -> str | None:
    parsed, text = parse_stream_line(line, call_labels=call_labels)
    return text if parsed else None


def parse_stream_line(
    line: str,
    *,
    call_labels: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return False, None
    if not isinstance(record, dict):
        return True, None
    return True, text_from_stream_record(record, call_labels=call_labels)


def stream_record_from_line(line: str) -> dict[str, object] | None:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def text_from_stream_record(
    record: dict[str, object],
    *,
    call_labels: dict[str, str] | None = None,
) -> str | None:
    record_type = record.get("type")
    if record_type == "thread.started":
        return None
    if record_type == "turn.started":
        return "[task] Codex turn started."
    if record_type == "turn.completed":
        return render_turn_completed(record)
    if record_type == "turn.failed":
        return render_turn_failed(record)
    if record_type == "item.completed":
        return text_from_top_level_item(record.get("item"), call_labels=call_labels)
    payload = record.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    payload_type = payload.get("type")
    if record_type == "compacted":
        return render_compacted_record(payload)
    if record_type == "event_msg":
        event_text = text_from_event_payload(payload, call_labels=call_labels)
        if event_text:
            return event_text
    if record_type == "event_msg" and payload_type == "agent_message":
        text = text_from_payload(payload)
        phase = str(payload.get("phase") or "")
        if text and not looks_like_autonomous_status_update(text, phase):
            return render_assistant_message(text, phase)
    if record_type == "event_msg" and payload_type == "task_complete":
        text = str(payload.get("last_agent_message") or "")
        return render_assistant_message(text, "final_answer") if text else None
    if record_type == "response_item" and payload_type == "message" and payload.get("role") == "assistant":
        text = text_from_payload(payload)
        phase = str(payload.get("phase") or "")
        if text and not looks_like_autonomous_status_update(text, phase):
            return render_assistant_message(text, phase)
    if record_type == "response_item":
        return text_from_response_item(payload, call_labels=call_labels)
    return None


def text_from_top_level_item(
    item: object,
    *,
    call_labels: dict[str, str] | None = None,
) -> str | None:
    if not isinstance(item, dict):
        return None
    item_type = str(item.get("type") or "")
    if item_type == "agent_message":
        text = str(item.get("text") or "")
        phase = str(item.get("phase") or "")
        if text and not looks_like_autonomous_status_update(text, phase):
            return render_assistant_message(text, phase)
        return None
    if item_type == "user_message":
        return render_user_message(item)
    if item_type == "reasoning":
        summary = reasoning_summary_text(item.get("summary"))
        return f"[reasoning] {summary}" if summary else None
    if item_type in {"function_call", "custom_tool_call", "tool_search_call", "web_search_call"}:
        return text_from_response_item(item, call_labels=call_labels)
    if item_type in {"function_call_output", "custom_tool_call_output", "tool_search_output"}:
        return text_from_response_item(item, call_labels=call_labels)
    return render_completed_item({"item": item})


def text_from_event_payload(
    payload: dict[str, object],
    *,
    call_labels: dict[str, str] | None = None,
) -> str | None:
    payload_type = payload.get("type")
    if payload_type in {"agent_message", "task_complete"}:
        return None
    if payload_type == "user_message":
        return render_user_message(payload)
    if payload_type == "task_started":
        return "[task] Codex turn started."
    if payload_type == "turn_aborted":
        reason = str(payload.get("reason") or "unknown")
        return f"[task] Codex turn aborted: {reason}."
    if payload_type == "web_search_end":
        query = str(payload.get("query") or "").strip()
        return f"[search] {query}" if query else "[search] completed"
    if payload_type == "patch_apply_end":
        label = store_call_label(payload, call_labels, "apply_patch")
        return render_patch_result(payload, label)
    if payload_type == "mcp_tool_call_end":
        label = label_from_mcp_invocation(payload.get("invocation"))
        store_call_label(payload, call_labels, label)
        duration = duration_text(payload.get("duration"))
        suffix = f" in {duration}" if duration else ""
        return f"[tool] {label} completed{suffix}."
    if payload_type == "context_compacted":
        return "[context] compacted"
    if payload_type == "token_count":
        return render_token_count(payload)
    if payload_type == "item_completed":
        return render_completed_item(payload)
    if payload_type == "thread_rolled_back":
        return render_thread_rollback(payload)
    return None


def render_user_message(payload: dict[str, object]) -> str | None:
    text = text_from_payload(payload)
    if not text or looks_like_bootstrap_context(text):
        return None
    return f"YOU\n  {compact_value(clean_user_text(text), limit=800)}"


def render_assistant_message(text: str, phase: str) -> str:
    role = "CODEX final" if phase == "final_answer" else "CODEX"
    return f"{role}\n{indent_stream_text(text)}"


def indent_stream_text(text: str) -> str:
    return "\n".join(f"  {line}" if line else "  " for line in text.rstrip().splitlines())


def text_from_response_item(
    payload: dict[str, object],
    *,
    call_labels: dict[str, str] | None = None,
) -> str | None:
    payload_type = payload.get("type")
    if payload_type == "function_call":
        return render_function_call(payload, call_labels=call_labels)
    if payload_type == "function_call_output":
        return render_tool_output(payload, call_labels=call_labels)
    if payload_type == "custom_tool_call":
        return render_custom_tool_call(payload, call_labels=call_labels)
    if payload_type == "custom_tool_call_output":
        return render_tool_output(payload, call_labels=call_labels)
    if payload_type == "tool_search_call":
        return render_tool_search_call(payload, call_labels=call_labels)
    if payload_type == "tool_search_output":
        return render_tool_search_output(payload, call_labels=call_labels)
    if payload_type == "web_search_call":
        action = compact_value(payload.get("action"))
        return f"[search] {action}" if action else "[search] started"
    if payload_type == "reasoning":
        summary = reasoning_summary_text(payload.get("summary"))
        return f"[reasoning] {summary}" if summary else None
    return None


def render_function_call(
    payload: dict[str, object],
    *,
    call_labels: dict[str, str] | None = None,
) -> str:
    name = str(payload.get("name") or "tool")
    namespace = str(payload.get("namespace") or "")
    label = f"{namespace}.{name}" if namespace else name
    store_call_label(payload, call_labels, label)
    arguments = parse_jsonish(payload.get("arguments"))
    if isinstance(arguments, dict) and name == "exec_command":
        command = compact_value(arguments.get("cmd")) or "(no command)"
        workdir = compact_value(arguments.get("workdir"))
        cwd = f" (cwd: {workdir})" if workdir else ""
        return f"[tool] exec_command: {command}{cwd}"
    rendered_args = compact_value(arguments if arguments is not None else payload.get("arguments"))
    suffix = f": {rendered_args}" if rendered_args else ""
    return f"[tool] {label}{suffix}"


def render_custom_tool_call(
    payload: dict[str, object],
    *,
    call_labels: dict[str, str] | None = None,
) -> str:
    name = str(payload.get("name") or "custom_tool")
    store_call_label(payload, call_labels, name)
    value = payload.get("input")
    if name == "apply_patch":
        return "[tool] apply_patch"
    rendered_input = compact_value(parse_jsonish(value) if isinstance(value, str) else value)
    suffix = f": {rendered_input}" if rendered_input else ""
    return f"[tool] {name}{suffix}"


def render_tool_output(
    payload: dict[str, object],
    *,
    call_labels: dict[str, str] | None = None,
) -> str | None:
    label = call_label(payload, call_labels)
    output = payload.get("output")
    text = stringify_output(output).rstrip()
    prefix = f"[tool output] {label}" if label else "[tool output]"
    if not text:
        return f"{prefix}: (no output)"
    folded = folded_tool_output(text)
    if folded:
        return f"{prefix}: {folded}"
    return f"{prefix}\n{text}"


def render_tool_search_call(
    payload: dict[str, object],
    *,
    call_labels: dict[str, str] | None = None,
) -> str:
    store_call_label(payload, call_labels, "tool_search")
    arguments = payload.get("arguments")
    rendered = compact_value(arguments)
    return f"[tool] tool_search: {rendered}" if rendered else "[tool] tool_search"


def render_tool_search_output(
    payload: dict[str, object],
    *,
    call_labels: dict[str, str] | None = None,
) -> str:
    label = call_label(payload, call_labels) or "tool_search"
    tools = payload.get("tools")
    if isinstance(tools, list):
        return f"[tool output] {label}: {len(tools)} tool group(s)"
    status = str(payload.get("status") or "").strip()
    suffix = f": {status}" if status else ""
    return f"[tool output] {label}{suffix}"


def render_patch_result(payload: dict[str, object], label: str) -> str:
    success = bool(payload.get("success"))
    status = "applied" if success else "failed"
    changes = payload.get("changes")
    changed_paths: list[str] = []
    if isinstance(changes, dict):
        changed_paths = [Path(path).name for path in changes.keys()]
    path_text = ", ".join(changed_paths[:6])
    if len(changed_paths) > 6:
        path_text += f", +{len(changed_paths) - 6} more"
    detail = f": {path_text}" if path_text else ""
    stderr = stringify_output(payload.get("stderr")).strip()
    if stderr:
        detail = f"{detail}\n{stderr}" if detail else f"\n{stderr}"
    return f"[tool] {label} {status}{detail}"


def render_completed_item(payload: dict[str, object]) -> str:
    item = payload.get("item")
    if not isinstance(item, dict):
        return "[item] completed"
    item_type = str(item.get("type") or "item").strip() or "item"
    prefix = "[plan]" if item_type.casefold() == "plan" else f"[item] {item_type}"
    text = str(item.get("text") or "").strip()
    if text:
        return f"{prefix} completed\n{text}"
    detail = compact_value(item)
    suffix = f": {detail}" if detail else ""
    return f"{prefix} completed{suffix}"


def render_thread_rollback(payload: dict[str, object]) -> str:
    turns = payload.get("num_turns")
    try:
        count = int(turns)
    except (TypeError, ValueError):
        return "[thread] rolled back."
    label = "turn" if count == 1 else "turns"
    return f"[thread] rolled back {count} {label}."


def render_turn_completed(record: dict[str, object]) -> str | None:
    usage = record.get("usage")
    if not isinstance(usage, dict):
        return "[task] Codex turn completed."
    parts: list[str] = []
    labels = [
        ("input_tokens", "input"),
        ("cached_input_tokens", "cached"),
        ("output_tokens", "output"),
        ("reasoning_output_tokens", "reasoning"),
    ]
    for key, label in labels:
        value = number_value(usage.get(key))
        if value is not None:
            parts.append(f"{label} {format_number(value)}")
    return f"[tokens] {', '.join(parts)}" if parts else "[task] Codex turn completed."


def render_turn_failed(record: dict[str, object]) -> str:
    error = record.get("error")
    if isinstance(error, dict):
        message = compact_value(error.get("message") or error.get("detail") or error)
    else:
        message = compact_value(error)
    return f"[task] Codex turn failed: {message}" if message else "[task] Codex turn failed."


def render_compacted_record(payload: dict[str, object]) -> str:
    message = compact_value(payload.get("message"))
    if message:
        return f"[context] compacted: {message}"
    return "[context] compacted"


def render_token_count(payload: dict[str, object]) -> str:
    info = payload.get("info")
    rate_limits = payload.get("rate_limits")
    parts: list[str] = []
    if isinstance(info, dict):
        last_usage = info.get("last_token_usage")
        total_usage = info.get("total_token_usage")
        last_total = token_total(last_usage)
        session_total = token_total(total_usage)
        if last_total:
            parts.append(f"last {format_number(last_total)}")
        if session_total:
            parts.append(f"session {format_number(session_total)}")
        context_window = number_value(info.get("model_context_window"))
        if session_total and context_window:
            percent = session_total / context_window * 100
            parts.append(f"context {format_number(session_total)} / {format_number(context_window)} ({format_percent(percent)})")
        elif context_window:
            parts.append(f"context window {format_number(context_window)}")
    if isinstance(rate_limits, dict):
        rate_text = render_rate_limits(rate_limits)
        if rate_text:
            parts.append(rate_text)
    return f"[tokens] {', '.join(parts)}" if parts else "[tokens] updated"


def token_total(value: object) -> float | None:
    if not isinstance(value, dict):
        return None
    return number_value(value.get("total_tokens"))


def render_rate_limits(rate_limits: dict[str, object]) -> str:
    labels: list[str] = []
    for key in ("primary", "secondary"):
        value = rate_limits.get(key)
        if not isinstance(value, dict):
            continue
        used = number_value(value.get("used_percent"))
        if used is not None:
            labels.append(f"{key} {format_percent(used)}")
    reached = str(rate_limits.get("rate_limit_reached_type") or "").strip()
    prefix = f"rate {'/'.join(labels)}" if labels else ""
    if reached:
        return f"{prefix}, limit reached: {reached}" if prefix else f"limit reached: {reached}"
    return prefix


def label_from_mcp_invocation(value: object) -> str:
    if not isinstance(value, dict):
        return "mcp_tool"
    server = str(value.get("server") or "").strip()
    tool = str(value.get("tool") or "").strip()
    if server and tool:
        return f"{server}.{tool}"
    return tool or server or "mcp_tool"


def store_call_label(
    payload: dict[str, object],
    call_labels: dict[str, str] | None,
    label: str,
) -> str:
    call_id = str(payload.get("call_id") or "")
    if call_id and call_labels is not None:
        call_labels[call_id] = label
    return label


def call_label(payload: dict[str, object], call_labels: dict[str, str] | None) -> str:
    call_id = str(payload.get("call_id") or "")
    if call_id and call_labels is not None:
        return call_labels.get(call_id, "")
    return ""


def parse_jsonish(value: object) -> object:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "{[":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def compact_value(value: object, *, limit: int = 240) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = " ".join(text.strip().split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 12)].rstrip() + " ... [more]"


def stringify_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def folded_tool_output(text: str) -> str:
    lines = text.splitlines()
    byte_count = len(text.encode("utf-8"))
    if len(lines) <= TOOL_OUTPUT_FOLD_LINE_LIMIT and byte_count <= TOOL_OUTPUT_FOLD_BYTE_LIMIT:
        return ""
    preview = tool_output_preview(lines)
    line_label = "line" if len(lines) == 1 else "lines"
    return f"folded {len(lines)} {line_label}, {format_bytes(byte_count)}; showing preview\n{preview}"


def tool_output_preview(lines: list[str]) -> str:
    preview_lines = lines[:TOOL_OUTPUT_PREVIEW_LINES]
    preview = "\n".join(preview_lines)
    if len(preview) <= TOOL_OUTPUT_PREVIEW_CHARS:
        return preview
    return preview[:TOOL_OUTPUT_PREVIEW_CHARS].rstrip() + "\n... [preview truncated]"


def format_bytes(count: int) -> str:
    label = "byte" if count == 1 else "bytes"
    return f"{format_number(float(count))} {label}"


def duration_text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    seconds = value.get("secs")
    nanos = value.get("nanos")
    try:
        total = float(seconds or 0) + float(nanos or 0) / 1_000_000_000
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    if total < 10:
        return f"{total:.1f}s"
    return f"{total:.0f}s"


def number_value(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def format_number(value: float) -> str:
    if value >= 1_000_000:
        return trim_decimal(value / 1_000_000) + "m"
    if value >= 1_000:
        return trim_decimal(value / 1_000) + "k"
    if value.is_integer():
        return str(int(value))
    return trim_decimal(value)


def format_percent(value: float) -> str:
    return trim_decimal(value) + "%"


def trim_decimal(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def reasoning_summary_text(value: object) -> str:
    if isinstance(value, str):
        return compact_value(value, limit=800)
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = text_from_payload(item) or str(item.get("summary") or "")
            if text:
                parts.append(text)
    return compact_value("\n".join(parts), limit=800)


def run_codex_json_stream(
    command: list[str],
    *,
    raw_json: bool = False,
    stdout: TextIO | None = None,
    stderr_to_stdout: bool = False,
    event_callback: Callable[[dict[str, object]], None] | None = None,
) -> int:
    out = stdout or sys.stdout
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if stderr_to_stdout else None,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        print(f"ctui: unable to start Codex: {exc}", file=sys.stderr)
        return 2
    assert process.stdout is not None
    renderer = CodexStreamRenderer()
    for line in process.stdout:
        if event_callback is not None:
            record = stream_record_from_line(line)
            if record is not None:
                event_callback(record)
        rendered = line.rstrip("\n") if raw_json else renderer.render_line(line)
        if rendered is None:
            continue
        print(rendered, file=out, flush=True)
    return process.wait()

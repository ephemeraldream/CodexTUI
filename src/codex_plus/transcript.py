from __future__ import annotations

import datetime as dt
import json
import re
import textwrap
from pathlib import Path
from typing import Iterable

from .models import ChatMessage, ThreadRow


def read_messages(path: Path) -> list[ChatMessage]:
    event_messages: list[ChatMessage] = []
    fallback_user: list[ChatMessage] = []
    fallback_assistant: list[ChatMessage] = []
    task_complete_message: ChatMessage | None = None
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return []
    with handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = str(record.get("timestamp") or "")
            record_type = record.get("type")
            payload = record.get("payload") or {}
            if record_type == "event_msg":
                payload_type = payload.get("type")
                if payload_type == "user_message":
                    text = text_from_payload(payload)
                    if text:
                        event_messages.append(ChatMessage(timestamp, "user", "", text))
                elif payload_type == "agent_message":
                    text = text_from_payload(payload)
                    if text:
                        phase = str(payload.get("phase") or "")
                        event_messages.append(ChatMessage(timestamp, "assistant", phase, text))
                elif payload_type == "task_complete":
                    text = str(payload.get("last_agent_message") or "")
                    if text:
                        task_complete_message = ChatMessage(timestamp, "assistant", "final_answer", text)
            elif record_type == "response_item" and payload.get("type") == "message":
                role = str(payload.get("role") or "")
                text = text_from_payload(payload)
                if not text:
                    continue
                phase = str(payload.get("phase") or "")
                if role == "assistant":
                    fallback_assistant.append(ChatMessage(timestamp, "assistant", phase, text))
                elif role == "user" and not looks_like_bootstrap_context(text):
                    fallback_user.append(ChatMessage(timestamp, "user", phase, text))
    has_event_assistant = any(message.role == "assistant" for message in event_messages)
    has_event_user = any(message.role == "user" for message in event_messages)
    messages: list[ChatMessage] = []
    if has_event_user or has_event_assistant:
        messages.extend(event_messages)
        if not has_event_assistant:
            messages.extend(fallback_assistant)
        if not has_event_user:
            messages.extend(fallback_user)
    else:
        messages.extend(fallback_user)
        messages.extend(fallback_assistant)
    if task_complete_message and not has_similar_message(messages, task_complete_message):
        messages.append(task_complete_message)
    messages.sort(key=lambda message: message.timestamp)
    return dedupe_messages(messages)


def text_from_payload(payload: dict[str, object]) -> str:
    direct = payload.get("message")
    if isinstance(direct, str):
        return direct
    direct = payload.get("text")
    if isinstance(direct, str):
        return direct
    content = payload.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            for key in ("text", "output_text", "input_text"):
                value = item.get(key)
                if isinstance(value, str):
                    parts.append(value)
                    break
        return "\n".join(parts)
    if isinstance(content, str):
        return content
    return ""


def looks_like_bootstrap_context(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("# AGENTS.md instructions") or "<environment_context>" in stripped


def has_similar_message(messages: Iterable[ChatMessage], candidate: ChatMessage) -> bool:
    candidate_text = candidate.text.strip()
    return any(message.role == candidate.role and message.text.strip() == candidate_text for message in messages)


def dedupe_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    seen: set[tuple[str, str, str]] = set()
    result: list[ChatMessage] = []
    for message in messages:
        key = (message.role, message.phase, message.text.strip())
        if key in seen:
            continue
        seen.add(key)
        result.append(message)
    return result


def filter_messages(
    messages: list[ChatMessage],
    mode: str,
    phases: set[str] | None = None,
) -> list[ChatMessage]:
    if phases:
        messages = [message for message in messages if message.phase in phases]
    if mode == "chat":
        return [message for message in messages if message.role in {"user", "assistant"}]
    if mode == "assistant":
        return [message for message in messages if message.role == "assistant"]
    if mode == "final":
        finals = [message for message in messages if message.role == "assistant" and message.phase == "final_answer"]
        return finals if finals else [message for message in messages if message.role == "assistant"][-1:]
    if mode == "user":
        return [message for message in messages if message.role == "user"]
    return messages


def render_thread(
    thread: ThreadRow,
    *,
    mode: str = "chat",
    phases: set[str] | None = None,
    color: bool = False,
) -> str:
    path = Path(thread.rollout_path)
    messages = filter_messages(read_messages(path), mode, phases)
    lines = [
        f"Codex session: {short_id(thread.id)}",
        f"Title: {truncate(thread.title or thread.first_user_message, 220) or '(untitled)'}",
        f"Updated: {format_ms(thread.recency_at_ms)}",
        f"Source: {thread.source or '?'}",
        f"CWD: {thread.cwd or '?'}",
        f"File: {thread.rollout_path}",
        "",
    ]
    if not messages:
        lines.append("(No chat messages found in this session.)")
        return "\n".join(lines)
    for idx, message in enumerate(messages, start=1):
        role = role_label(message)
        header = f"[{idx}] {format_timestamp(message.timestamp)}  {role}"
        if color:
            header = colorize_header(header, message.role, message.phase)
        lines.append(header)
        lines.append(textwrap.indent(message.text.rstrip(), "  "))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def role_label(message: ChatMessage) -> str:
    if message.role == "user":
        return "YOU"
    if message.phase == "final_answer":
        return "CODEX final"
    return "CODEX"


def colorize_header(text: str, role: str, phase: str) -> str:
    if role == "user":
        return f"\033[1;36m{text}\033[0m"
    if phase == "final_answer":
        return f"\033[1;32m{text}\033[0m"
    return f"\033[1;34m{text}\033[0m"


def format_timestamp(value: str) -> str:
    if not value:
        return "????-??-?? ??:??:??"
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def format_ms(value: int) -> str:
    if not value:
        return "unknown"
    seconds = value / 1000
    return dt.datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")


def short_id(value: str) -> str:
    return value[:8] if value else "????????"


def one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def truncate(value: str, width: int) -> str:
    clean = one_line(value)
    if len(clean) <= width:
        return clean
    return clean[: max(0, width - 1)] + "..."

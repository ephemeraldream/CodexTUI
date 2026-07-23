from __future__ import annotations

import datetime as dt
import json
import re
import textwrap
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

from .models import ChatMessage, ThreadRow
from .terminal_markdown import render_code_block_lines, render_markdown_lines


AUTONOMOUS_STATUS_KEYS = {"success", "summary", "key_changes_made", "key_learnings"}


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
                    text = user_text_from_payload(payload)
                    if text:
                        event_messages.append(ChatMessage(timestamp, "user", "", text))
                elif payload_type == "agent_message":
                    text = text_from_payload(payload)
                    if text:
                        phase = str(payload.get("phase") or "")
                        if not looks_like_autonomous_status_update(text, phase):
                            event_messages.append(ChatMessage(timestamp, "assistant", phase, text))
                elif payload_type == "task_complete":
                    text = str(payload.get("last_agent_message") or "")
                    if text:
                        task_complete_message = ChatMessage(timestamp, "assistant", "final_answer", text)
            elif record_type == "response_item" and payload.get("type") == "message":
                role = str(payload.get("role") or "")
                text = text_from_payload(payload)
                phase = str(payload.get("phase") or "")
                if role == "assistant":
                    if not text:
                        continue
                    if not looks_like_autonomous_status_update(text, phase):
                        fallback_assistant.append(ChatMessage(timestamp, "assistant", phase, text))
                elif role == "user":
                    display_text = user_text_from_payload(payload)
                    if display_text:
                        fallback_user.append(ChatMessage(timestamp, "user", phase, display_text))
            elif record_type == "item.completed":
                item = record.get("item") or {}
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "")
                role = str(item.get("role") or "")
                if item_type == "agent_message" or (item_type == "message" and role == "assistant"):
                    text = text_from_payload(item)
                    phase = str(item.get("phase") or "")
                    if text and not looks_like_autonomous_status_update(text, phase):
                        fallback_assistant.append(ChatMessage(timestamp, "assistant", phase, text))
                elif item_type == "user_message" or (item_type == "message" and role == "user"):
                    text = user_text_from_payload(item)
                    phase = str(item.get("phase") or "")
                    if text:
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


def user_text_from_payload(payload: dict[str, object]) -> str:
    text = text_from_payload(payload)
    if text and looks_like_bootstrap_context(text):
        return ""
    text = clean_user_text(text) if text else ""
    images = image_attachment_text(payload)
    if text and images:
        return f"{text}\n\n{images}"
    return text or images


def image_attachment_text(payload: dict[str, object]) -> str:
    images = payload.get("images")
    if not isinstance(images, list):
        return ""
    labels = [image_attachment_label(image, index) for index, image in enumerate(images, start=1)]
    return "\n".join(label for label in labels if label)


def image_attachment_label(image: object, index: int) -> str:
    name = image_attachment_name(image)
    suffix = f" {name}" if name else ""
    return f"[Image {index}]{suffix}"


def image_attachment_name(image: object) -> str:
    if isinstance(image, str):
        return image_name_from_value(image)
    if isinstance(image, dict):
        for key in ("path", "file", "filename", "name", "source", "url"):
            value = image.get(key)
            if isinstance(value, str):
                name = image_name_from_value(value)
                if name:
                    return name
    return ""


def image_name_from_value(value: str) -> str:
    stripped = value.strip()
    if not stripped or stripped.startswith("data:"):
        return ""
    parsed = urlparse(stripped)
    raw_path = parsed.path if parsed.scheme else stripped
    name = Path(unquote(raw_path)).name
    if name and len(name) <= 120:
        return name
    return ""


def looks_like_bootstrap_context(text: str) -> bool:
    stripped = text.lstrip()
    return (
        stripped.startswith("# AGENTS.md instructions")
        or stripped.startswith("<turn_aborted>")
        or "<environment_context>" in stripped
    )


def looks_like_autonomous_status_update(text: str, phase: str) -> bool:
    if phase == "final_answer":
        return False
    stripped = text.strip()
    if not stripped.startswith("{"):
        return False
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return isinstance(value, dict) and AUTONOMOUS_STATUS_KEYS.issubset(value)


def clean_user_text(text: str) -> str:
    return unwrap_autonomous_objective(text)


def clean_metadata_text(text: str) -> str:
    return unwrap_autonomous_objective(text)


def unwrap_autonomous_objective(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("You are working autonomously towards an objective given below."):
        return text
    match = re.search(r"(?ms)^## Objective\s*\n+(.*)\Z", stripped)
    if not match:
        return text
    objective = match.group(1).strip()
    return objective or text


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
    width: int | None = None,
    include_metadata: bool = True,
    header_style: str = "full",
) -> str:
    path = Path(thread.rollout_path)
    messages = filter_messages(read_messages(path), mode, phases)
    lines: list[str] = []
    if include_metadata:
        lines.extend(
            [
                f"Codex session: {short_id(thread.id)}",
                f"Title: {truncate(thread.title or thread.first_user_message, 220) or '(untitled)'}",
                f"Updated: {format_ms(thread.recency_at_ms)}",
                f"Source: {thread.source or '?'}",
                f"CWD: {thread.cwd or '?'}",
                f"File: {thread.rollout_path}",
                "",
            ]
        )
    if not messages:
        lines.append("(No chat messages found in this session.)")
        return "\n".join(lines)
    for idx, message in enumerate(messages, start=1):
        header = message_header(idx, message, header_style)
        if color:
            header = colorize_header(header, message.role, message.phase)
        lines.append(header)
        lines.append(textwrap.indent(render_message_text(message, width=message_width(width)), "  "))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_message_text(message: ChatMessage, *, width: int | None = None) -> str:
    text = message.text.rstrip()
    if message.role == "assistant":
        pretty = pretty_json_text(text)
        if width is not None:
            if pretty != text:
                return "\n".join(render_code_block_lines(pretty, width=width, language="json"))
            return "\n".join(render_markdown_lines(pretty, width=width))
        return pretty
    if width is not None:
        return "\n".join(render_markdown_lines(text, width=width))
    return text


def message_width(width: int | None) -> int | None:
    if width is None:
        return None
    return max(1, width - 4)


def pretty_json_text(text: str) -> str:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return text
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(value, (dict, list)):
        return text
    return json.dumps(value, ensure_ascii=False, indent=2)


def role_label(message: ChatMessage) -> str:
    if message.role == "user":
        return "YOU"
    if message.phase == "final_answer":
        return "CODEX final"
    return "CODEX"


def message_header(idx: int, message: ChatMessage, header_style: str) -> str:
    role = role_label(message)
    if header_style == "compact":
        timestamp = compact_timestamp(message.timestamp)
        return f"{role} {timestamp}" if timestamp else role
    return f"[{idx}] {format_timestamp(message.timestamp)}  {role}"


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


def compact_timestamp(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%H:%M")
    except ValueError:
        return ""


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
    if width <= 0:
        return ""
    if len(clean) <= width:
        return clean
    if width <= 3:
        return "." * width
    return clean[: width - 3] + "..."

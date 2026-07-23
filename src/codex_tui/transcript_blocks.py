from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .codex_stream import folded_tool_output, stringify_output
from .models import ChatMessage, ThreadRow
from .paths import codex_home
from .transcript import (
    compact_timestamp,
    filter_messages,
    read_messages,
    role_label,
    truncate,
)


@dataclass(frozen=True)
class FileChange:
    path: str
    diff: str


@dataclass(frozen=True)
class TranscriptBlock:
    id: str
    kind: str
    title: str
    subtitle: str
    text: str
    role: str = ""
    phase: str = ""
    timestamp: str = ""
    file_changes: tuple[FileChange, ...] = ()
    detail_text: str = ""

    @property
    def expandable(self) -> bool:
        return bool(self.file_changes or self.detail_text)


@dataclass(frozen=True)
class SessionInfo:
    model: str = ""
    context_tokens: int | None = None
    context_window: int | None = None


def transcript_blocks_for_thread(thread: ThreadRow) -> list[TranscriptBlock]:
    path = Path(thread.rollout_path)
    blocks: list[TranscriptBlock] = []
    for index, message in enumerate(
        filter_messages(read_messages(path), "chat"),
        start=1,
    ):
        blocks.append(block_from_message(message, index))
    blocks.extend(file_change_blocks(path, start=len(blocks) + 1))
    blocks.extend(tool_output_blocks(path, start=len(blocks) + 1))
    blocks.sort(key=block_sort_key)
    return with_stable_indices(blocks)


def block_from_message(message: ChatMessage, index: int) -> TranscriptBlock:
    title = role_label(message)
    subtitle = compact_timestamp(message.timestamp)
    return TranscriptBlock(
        id=f"message-{index}",
        kind="message",
        title=title,
        subtitle=subtitle,
        text=message.text,
        role=message.role,
        phase=message.phase,
        timestamp=message.timestamp,
    )


def file_change_blocks(path: Path, *, start: int = 1) -> list[TranscriptBlock]:
    blocks: list[TranscriptBlock] = []
    for timestamp, patch_text in apply_patch_calls(path):
        changes = tuple(
            FileChange(path=changed_path, diff=patch_text)
            for changed_path in patch_paths(patch_text)
        )
        if not changes:
            continue
        title = "File change"
        subtitle = compact_timestamp(timestamp)
        summary = changed_paths_summary(change.path for change in changes)
        blocks.append(
            TranscriptBlock(
                id=f"file-change-{start + len(blocks)}",
                kind="file_change",
                title=title,
                subtitle=subtitle,
                text=summary,
                timestamp=timestamp,
                file_changes=changes,
            )
        )
    return blocks


def tool_output_blocks(path: Path, *, start: int = 1) -> list[TranscriptBlock]:
    blocks: list[TranscriptBlock] = []
    call_labels: dict[str, str] = {}
    for record in rollout_records(path):
        for payload in tool_payloads(record):
            payload_type = str(payload.get("type") or "")
            if payload_type in {"function_call", "custom_tool_call"}:
                remember_tool_call(payload, call_labels)
                continue
            if payload_type in {"function_call_output", "custom_tool_call_output"}:
                block = tool_output_block(payload, record, call_labels, start=start + len(blocks))
                if block is not None:
                    blocks.append(block)
    return blocks


def rollout_records(path: Path) -> Iterable[dict[str, object]]:
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return
    with handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def tool_payloads(record: dict[str, object]) -> Iterable[dict[str, object]]:
    payload = record.get("payload")
    if isinstance(payload, dict):
        yield payload
    item = record.get("item")
    if isinstance(item, dict):
        yield item


def remember_tool_call(payload: dict[str, object], call_labels: dict[str, str]) -> None:
    call_id = str(payload.get("call_id") or "")
    if call_id:
        call_labels[call_id] = tool_call_label(payload)


def tool_call_label(payload: dict[str, object]) -> str:
    name = str(payload.get("name") or "").strip()
    namespace = str(payload.get("namespace") or "").strip()
    if namespace and name:
        return f"{namespace}.{name}"
    return name or "tool"


def tool_output_block(
    payload: dict[str, object],
    record: dict[str, object],
    call_labels: dict[str, str],
    *,
    start: int,
) -> TranscriptBlock | None:
    output = stringify_output(payload.get("output")).rstrip()
    if not output:
        return None
    folded = folded_tool_output(output)
    if not folded:
        return None
    label = call_labels.get(str(payload.get("call_id") or ""), "tool")
    return TranscriptBlock(
        id=f"tool-output-{start}",
        kind="tool_output",
        title="Tool output",
        subtitle=compact_timestamp(str(record.get("timestamp") or "")),
        text=f"{label}: {folded}",
        timestamp=str(record.get("timestamp") or ""),
        detail_text=f"{label}\n{output}",
    )


def apply_patch_calls(path: Path) -> Iterable[tuple[str, str]]:
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return
    with handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            for payload in tool_payloads(record):
                if payload.get("type") != "custom_tool_call" or payload.get("name") != "apply_patch":
                    continue
                patch_text = str(payload.get("input") or "")
                if patch_text:
                    yield str(record.get("timestamp") or ""), patch_text


def patch_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    prefixes = (
        "*** Add File: ",
        "*** Update File: ",
        "*** Delete File: ",
        "*** Move to: ",
    )
    for line in patch_text.splitlines():
        for prefix in prefixes:
            if line.startswith(prefix):
                value = line.removeprefix(prefix).strip()
                if value and value not in paths:
                    paths.append(value)
                break
    return paths


def changed_paths_summary(paths: Iterable[str]) -> str:
    names = [Path(path).name or path for path in paths]
    if not names:
        return "No file paths found."
    if len(names) <= 3:
        return ", ".join(names)
    return ", ".join(names[:3]) + f", +{len(names) - 3} more"


def with_stable_indices(blocks: list[TranscriptBlock]) -> list[TranscriptBlock]:
    result: list[TranscriptBlock] = []
    for index, block in enumerate(blocks, start=1):
        result.append(
            TranscriptBlock(
                id=f"{block.kind}-{index}",
                kind=block.kind,
                title=block.title,
                subtitle=block.subtitle,
                text=block.text,
                role=block.role,
                phase=block.phase,
                timestamp=block.timestamp,
                file_changes=block.file_changes,
                detail_text=block.detail_text,
            )
        )
    return result


def block_sort_key(block: TranscriptBlock) -> tuple[str, str]:
    return (block.timestamp, block.id)


def session_info_for_thread(thread: ThreadRow) -> SessionInfo:
    info = session_info_from_rollout(Path(thread.rollout_path))
    if info.model:
        return info
    return SessionInfo(
        model=codex_config_model(),
        context_tokens=info.context_tokens,
        context_window=info.context_window,
    )


def default_session_info() -> SessionInfo:
    return SessionInfo(model=codex_config_model())


def session_info_from_rollout(path: Path) -> SessionInfo:
    info = SessionInfo()
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return SessionInfo()
    with handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            info = session_info_from_record(record, current=info)
    return info


def session_info_from_record(
    record: dict[str, object],
    *,
    current: SessionInfo | None = None,
) -> SessionInfo:
    existing = current or SessionInfo()
    model = model_value(record) or existing.model
    context_tokens = existing.context_tokens
    context_window = existing.context_window
    payload = record.get("payload")
    if isinstance(payload, dict):
        model = model_value(payload) or model
        if payload.get("type") == "task_started":
            task_window = int_value(payload.get("model_context_window"))
            context_window = task_window if task_window is not None else context_window
        if payload.get("type") == "token_count":
            token_count, token_window = token_context_from_payload(payload)
            context_tokens = token_count if token_count is not None else context_tokens
            context_window = token_window if token_window is not None else context_window
    if record.get("type") == "turn.completed":
        usage = record.get("usage")
        if isinstance(usage, dict):
            usage_total = int_value(usage.get("total_tokens"))
            context_tokens = usage_total if usage_total is not None else context_tokens
    return SessionInfo(model=model, context_tokens=context_tokens, context_window=context_window)


def model_value(record: dict[str, object]) -> str:
    for key in ("model", "model_slug", "model_name"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def token_context_from_payload(payload: dict[str, object]) -> tuple[int | None, int | None]:
    info = payload.get("info")
    if not isinstance(info, dict):
        return None, None
    total = token_total(info.get("total_token_usage"))
    if total is None:
        total = token_total(info.get("last_token_usage"))
    window = int_value(info.get("model_context_window"))
    return total, window


def token_total(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    return int_value(value.get("total_tokens"))


def int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def codex_config_model() -> str:
    path = codex_home() / "config.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    value = data.get("model")
    return value.strip() if isinstance(value, str) else ""


def session_footer_text(info: SessionInfo) -> str:
    model = info.model or "?"
    context = context_text(info.context_tokens, info.context_window)
    return f"model {truncate(model, 24)} | ctx {context}"


def context_text(tokens: int | None, window: int | None) -> str:
    if tokens is None and window is None:
        return "?/?"
    if tokens is None:
        return f"?/{format_short_number(float(window))}"
    if window is None or window <= 0:
        return format_short_number(float(tokens))
    percent = tokens / window * 100
    return f"{format_short_number(float(tokens))}/{format_short_number(float(window))} {percent:.0f}%"


def format_short_number(value: float) -> str:
    if value >= 1_000_000:
        return trim_decimal(value / 1_000_000) + "m"
    if value >= 1_000:
        return trim_decimal(value / 1_000) + "k"
    if value.is_integer():
        return str(int(value))
    return trim_decimal(value)


def trim_decimal(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text

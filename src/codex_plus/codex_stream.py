from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .transcript import looks_like_autonomous_status_update, text_from_payload


@dataclass
class CodexStreamRenderer:
    last_text: str = ""

    def render_line(self, line: str) -> str | None:
        text = text_from_json_line(line)
        if text is None:
            return line.rstrip("\n") if line.strip() else None
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
) -> list[str]:
    command = [str(codex_bin), "exec"]
    if resume_id:
        command.extend(["resume", "--json", resume_id])
    else:
        command.append("--json")
    if prompt:
        command.append(prompt)
    return command


def text_from_json_line(line: str) -> str | None:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    payload = record.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    record_type = record.get("type")
    payload_type = payload.get("type")
    if record_type == "event_msg" and payload_type == "agent_message":
        text = text_from_payload(payload)
        phase = str(payload.get("phase") or "")
        if text and not looks_like_autonomous_status_update(text, phase):
            return text
    if record_type == "event_msg" and payload_type == "task_complete":
        text = str(payload.get("last_agent_message") or "")
        return text or None
    if record_type == "response_item" and payload_type == "message" and payload.get("role") == "assistant":
        text = text_from_payload(payload)
        phase = str(payload.get("phase") or "")
        if text and not looks_like_autonomous_status_update(text, phase):
            return text
    return None


def run_codex_json_stream(
    command: list[str],
    *,
    raw_json: bool = False,
    stdout: TextIO | None = None,
    stderr_to_stdout: bool = False,
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
        print(f"cxp: unable to start Codex: {exc}", file=sys.stderr)
        return 2
    assert process.stdout is not None
    renderer = CodexStreamRenderer()
    for line in process.stdout:
        rendered = line.rstrip("\n") if raw_json else renderer.render_line(line)
        if rendered is None:
            continue
        print(rendered, file=out, flush=True)
    return process.wait()

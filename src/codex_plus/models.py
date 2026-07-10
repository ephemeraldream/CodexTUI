from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThreadRow:
    id: str
    title: str
    cwd: str
    source: str
    archived: bool
    rollout_path: str
    created_at_ms: int
    updated_at_ms: int
    recency_at_ms: int
    preview: str
    first_user_message: str


@dataclass(frozen=True)
class ChatMessage:
    timestamp: str
    role: str
    phase: str
    text: str

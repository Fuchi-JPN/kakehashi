"""Canonical intermediate representation (design doc 4.3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: Union[str, list]
    is_error: bool = False


ContentBlock = Union[TextBlock, ToolUseBlock, ToolResultBlock]


@dataclass
class Message:
    role: Role
    content: Union[str, list[ContentBlock]]


@dataclass
class CanonicalRequest:
    messages: list[Message]
    tools: list[dict] = field(default_factory=list)
    tool_choice: Any = None
    stream: bool = False
    params: dict = field(default_factory=dict)
    model: str = ""
    raw_ingress: Literal["openai", "anthropic"] = "openai"


@dataclass
class CanonicalResponse:
    text: str = ""
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    stop_reason: str = "end_turn"  # end_turn|max_tokens|tool_use|error
    usage: dict = field(default_factory=dict)


# stop_reason normalization
OPENAI_TO_CANON_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "error",
}
CANON_TO_OPENAI_STOP = {v: k for k, v in OPENAI_TO_CANON_STOP.items()}

ANTHROPIC_TO_CANON_STOP = {
    "end_turn": "end_turn",
    "max_tokens": "max_tokens",
    "tool_use": "tool_use",
    "stop_sequence": "end_turn",
}
CANON_TO_ANTHROPIC_STOP = {
    "end_turn": "end_turn",
    "max_tokens": "max_tokens",
    "tool_use": "tool_use",
    "error": "end_turn",
}


def normalize_stop_to_canon(value: str | None, proto: str) -> str:
    if not value:
        return "end_turn"
    if proto == "openai":
        return OPENAI_TO_CANON_STOP.get(value, value)
    return ANTHROPIC_TO_CANON_STOP.get(value, value)

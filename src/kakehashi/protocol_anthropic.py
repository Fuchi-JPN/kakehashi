"""Anthropic <-> Canonical conversion."""
from __future__ import annotations

import logging
import uuid

from .models_canonical import (
    CANON_TO_ANTHROPIC_STOP,
    CanonicalRequest,
    CanonicalResponse,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    normalize_stop_to_canon,
)

log = logging.getLogger("kakehashi.protocol_anthropic")


def _blocks_to_text(blocks) -> str:
    return "\n".join(b.text for b in blocks if isinstance(b, TextBlock))


def anthropic_to_canonical(body: dict) -> CanonicalRequest:
    messages: list[Message] = []
    system = body.get("system")
    if system is not None:
        if isinstance(system, str):
            messages.append(Message(role="system", content=system))
        elif isinstance(system, list):
            texts = [b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") in ("text",)]
            messages.append(Message(role="system", content="\n".join(texts)))
    for m in body.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, str):
            messages.append(Message(role=role, content=content))
        elif isinstance(content, list):
            blocks: list = []
            for b in content:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "text":
                    blocks.append(TextBlock(text=b.get("text", "")))
                elif t == "tool_use":
                    blocks.append(ToolUseBlock(id=b.get("id", ""), name=b.get("name", ""),
                                               input=b.get("input", {})))
                elif t == "tool_result":
                    c = b.get("content", "")
                    if isinstance(c, list):
                        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                    blocks.append(ToolResultBlock(tool_use_id=b.get("tool_use_id", ""),
                                                  content=c if isinstance(c, str) else str(c),
                                                  is_error=bool(b.get("is_error", False))))
                else:
                    log.warning("anthropic ingress: unknown block type=%s", t)
            # assistant with only tool_use -> keep; user with tool_result -> role tool internally?
            # Keep original role; engine only translates user TextBlocks.
            messages.append(Message(role=role, content=blocks))
        else:
            messages.append(Message(role=role, content=""))

    tools = []
    for t in body.get("tools", []) or []:
        tools.append({
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "input_schema": t.get("input_schema", {}),
        })

    params: dict = {}
    if body.get("max_tokens") is not None:
        params["max_tokens"] = body["max_tokens"]
    elif True:
        params["max_tokens"] = 4000
        log.warning("anthropic ingress: max_tokens missing, default 4000")
    for k in ("temperature", "top_p"):
        if body.get(k) is not None:
            params[k] = body[k]
    if body.get("stop_sequences") is not None:
        params["stop"] = body["stop_sequences"]
    # stash unknown
    passthrough = {}
    for k, v in body.items():
        if k not in ("model", "system", "messages", "tools", "tool_choice", "stream",
                     "max_tokens", "temperature", "top_p", "stop_sequences"):
            passthrough[k] = v
    if passthrough:
        params["_passthrough_anthropic"] = passthrough

    return CanonicalRequest(
        messages=messages,
        tools=tools,
        tool_choice=body.get("tool_choice"),
        stream=bool(body.get("stream", False)),
        params=params,
        model=body.get("model", ""),
        raw_ingress="anthropic",
    )


def canonical_to_anthropic_payload(canon: CanonicalRequest) -> dict:
    system_parts = []
    messages = []
    for m in canon.messages:
        if m.role == "system":
            if isinstance(m.content, str):
                system_parts.append(m.content)
            else:
                system_parts.append(_blocks_to_text(m.content))
            continue
        if m.role == "tool":
            # tool result -> user message with tool_result blocks
            blocks = []
            for b in (m.content if isinstance(m.content, list) else []):
                if isinstance(b, ToolResultBlock):
                    blocks.append({"type": "tool_result", "tool_use_id": b.tool_use_id,
                                   "content": b.content})
            messages.append({"role": "user", "content": blocks or ""})
            continue
        if isinstance(m.content, str):
            messages.append({"role": m.role, "content": m.content})
        else:
            blocks = []
            for b in m.content:
                if isinstance(b, TextBlock):
                    blocks.append({"type": "text", "text": b.text})
                elif isinstance(b, ToolUseBlock):
                    blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                elif isinstance(b, ToolResultBlock):
                    blocks.append({"type": "tool_result", "tool_use_id": b.tool_use_id,
                                   "content": b.content})
            messages.append({"role": m.role, "content": blocks})
    payload: dict = {
        "model": canon.model,
        "messages": messages,
        "stream": canon.stream,
        "max_tokens": canon.params.get("max_tokens", 4000),
    }
    if system_parts:
        payload["system"] = "\n".join(system_parts)
    if canon.tools:
        payload["tools"] = [
            {"name": t.get("name", ""), "description": t.get("description", ""),
             "input_schema": t.get("input_schema", {"type": "object"})}
            for t in canon.tools
        ]
    if canon.tool_choice is not None:
        payload["tool_choice"] = canon.tool_choice
    for k in ("temperature", "top_p"):
        if canon.params.get(k) is not None:
            payload[k] = canon.params[k]
    if canon.params.get("stop") is not None:
        payload["stop_sequences"] = canon.params["stop"]
    return payload


def anthropic_response_to_canonical(data: dict) -> CanonicalResponse:
    content = data.get("content", [])
    texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    tool_uses = [ToolUseBlock(id=b.get("id", ""), name=b.get("name", ""), input=b.get("input", {}))
                 for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
    stop = normalize_stop_to_canon(data.get("stop_reason", "end_turn"), "anthropic")
    return CanonicalResponse(text="\n".join(texts), tool_uses=tool_uses,
                             stop_reason=stop, usage=data.get("usage", {}))


def canonical_to_anthropic_response(canon: CanonicalResponse, model: str) -> dict:
    content: list = []
    if canon.text:
        content.append({"type": "text", "text": canon.text})
    for tu in canon.tool_uses:
        content.append({"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input})
    return {
        "id": f"msg_kxh_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": CANON_TO_ANTHROPIC_STOP.get(canon.stop_reason, "end_turn"),
        "usage": canon.usage or {},
    }

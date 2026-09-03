"""OpenAI <-> Canonical conversion."""
from __future__ import annotations

import logging
import time
import uuid

from .models_canonical import (
    CANON_TO_OPENAI_STOP,
    CanonicalRequest,
    CanonicalResponse,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    normalize_stop_to_canon,
)

log = logging.getLogger("kakehashi.protocol_openai")


def _content_parts_to_text(content) -> str:
    """Extract plain text from OpenAI content (str or part list) without corruption."""
    import json
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if not isinstance(part, dict):
                texts.append(str(part))
                continue
            t = part.get("type", "text")
            if t == "text":
                texts.append(part.get("text", ""))
            elif t == "image_url":
                texts.append("[image]")
            elif t == "input_audio":
                texts.append("[audio]")
            else:
                log.warning("openai ingress: unknown content part type=%s, JSON-kept", t)
                try:
                    texts.append(json.dumps(part, ensure_ascii=False))
                except (TypeError, ValueError):
                    texts.append(str(part))
        return "\n".join(x for x in texts if x)
    return str(content) if content is not None else ""


def ingress_message_summary(messages) -> list[str]:
    """Compact structural summary of ingress messages (no raw content)."""
    out = []
    for m in messages if isinstance(messages, list) else []:
        if not isinstance(m, dict):
            out.append("?")
            continue
        c = m.get("content")
        kinds = "str" if isinstance(c, str) else (
            "+".join(sorted({p.get("type", "?") for p in c if isinstance(p, dict)})) if isinstance(c, list) else type(c).__name__)
        out.append(f"{m.get('role', '?')}({kinds},tc={len(m.get('tool_calls') or [])})")
    return out[:30]


def openai_to_canonical(body: dict) -> CanonicalRequest:
    messages: list[Message] = []
    for m in body.get("messages", []):
        role = m.get("role", "user")
        if role == "developer":
            role = "system"
        if role == "tool":
            # tool result -> internal tool message (array content-safe)
            content = _content_parts_to_text(m.get("content", ""))
            blocks = [ToolResultBlock(
                tool_use_id=m.get("tool_call_id", ""),
                content=content,
            )]
            messages.append(Message(role="tool", content=blocks))
            continue
        content = m.get("content")
        tool_calls = m.get("tool_calls", [])
        if isinstance(content, list):
            blocks: list = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                t = part.get("type", "text")
                if t == "text":
                    blocks.append(TextBlock(text=part.get("text", "")))
                else:
                    # keep unknown parts as JSON text (str() would corrupt to single quotes)
                    log.warning("openai ingress: unknown content part type=%s, JSON-kept", t)
                    blocks.append(TextBlock(text=_content_parts_to_text([part])))
            for tc in tool_calls:
                fn = tc.get("function", {})
                import json
                try:
                    args = json.loads(fn.get("arguments", "{}") or "{}")
                except Exception:
                    args = {}
                blocks.append(ToolUseBlock(id=tc.get("id", ""), name=fn.get("name", ""), input=args))
            messages.append(Message(role=role, content=blocks))
        else:
            if tool_calls:
                blocks = [TextBlock(text=content or "")]
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    import json
                    try:
                        args = json.loads(fn.get("arguments", "{}") or "{}")
                    except Exception:
                        args = {}
                    blocks.append(ToolUseBlock(id=tc.get("id", ""), name=fn.get("name", ""), input=args))
                messages.append(Message(role=role, content=blocks))
            else:
                messages.append(Message(role=role, content=content or ""))

    # tools normalize -> {name, description, input_schema}
    tools = []
    for t in body.get("tools", []) or []:
        if t.get("type") == "function" and "function" in t:
            fn = t["function"]
            tools.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {}),
            })
        else:
            tools.append(t)

    params: dict = {}
    for k in ("temperature", "max_tokens", "top_p", "stop"):
        if body.get(k) is not None:
            params[k] = body[k]
    # stash unknown fields for openai->openai passthrough
    passthrough = {}
    for k, v in body.items():
        if k not in ("model", "messages", "tools", "tool_choice", "stream",
                     "temperature", "max_tokens", "top_p", "stop"):
            passthrough[k] = v
    if passthrough:
        params["_passthrough_openai"] = passthrough

    return CanonicalRequest(
        messages=messages,
        tools=tools,
        tool_choice=body.get("tool_choice"),
        stream=bool(body.get("stream", False)),
        params=params,
        model=body.get("model", ""),
        raw_ingress="openai",
    )


def canonical_to_openai_payload(canon: CanonicalRequest) -> dict:
    """Canonical -> OpenAI chat/completions request payload (for egress)."""
    messages = []
    for m in canon.messages:
        if m.role == "tool":
            for b in (m.content if isinstance(m.content, list) else []):
                if isinstance(b, ToolResultBlock):
                    c = b.content if isinstance(b.content, str) else str(b.content)
                    messages.append({"role": "tool", "tool_call_id": b.tool_use_id, "content": c})
            continue
        if isinstance(m.content, str):
            messages.append({"role": m.role, "content": m.content})
        else:
            text_parts = [b.text for b in m.content if isinstance(b, TextBlock)]
            tool_uses = [b for b in m.content if isinstance(b, ToolUseBlock)]
            text = "\n".join(text_parts)
            entry: dict = {"role": m.role, "content": text}
            if tool_uses:
                import json
                entry["tool_calls"] = [
                    {"id": tu.id, "type": "function",
                     "function": {"name": tu.name, "arguments": json.dumps(tu.input, ensure_ascii=False)}}
                    for tu in tool_uses
                ]
            messages.append(entry)
    tools = []
    for t in canon.tools:
        if "input_schema" in t and "name" in t:
            tools.append({"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            }})
        else:
            tools.append(t)
    payload: dict = {"model": canon.model, "messages": messages, "stream": canon.stream}
    for k in ("temperature", "max_tokens", "top_p", "stop"):
        if canon.params.get(k) is not None:
            payload[k] = canon.params[k]
    if canon.tool_choice is not None:
        payload["tool_choice"] = canon.tool_choice
    if tools:
        payload["tools"] = tools
    pt = canon.params.get("_passthrough_openai")
    if isinstance(pt, dict):
        for k, v in pt.items():
            payload.setdefault(k, v)
    return payload


def openai_response_to_canonical(data: dict) -> CanonicalResponse:
    import json
    choices = data.get("choices", [{}])
    ch = choices[0] if choices else {}
    msg = ch.get("message", {})
    text = msg.get("content") or ""
    tool_uses = []
    for tc in msg.get("tool_calls", []) or []:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments", "{}") or "{}")
        except Exception:
            args = {}
        tool_uses.append(ToolUseBlock(id=tc.get("id", ""), name=fn.get("name", ""), input=args))
    stop = normalize_stop_to_canon(ch.get("finish_reason", "stop"), "openai")
    if tool_uses and stop == "end_turn":
        stop = "tool_use"
    return CanonicalResponse(text=text if isinstance(text, str) else str(text),
                             tool_uses=tool_uses, stop_reason=stop,
                             usage=data.get("usage", {}))


def canonical_to_openai_response(canon: CanonicalResponse, model: str) -> dict:
    import json
    import time
    msg: dict = {"role": "assistant", "content": canon.text}
    finish = CANON_TO_OPENAI_STOP.get(canon.stop_reason, "stop")
    if canon.tool_uses:
        msg["tool_calls"] = [
            {"id": tu.id, "type": "function",
             "function": {"name": tu.name, "arguments": json.dumps(tu.input, ensure_ascii=False)}}
            for tu in canon.tool_uses
        ]
        finish = "tool_calls"
    return {
        "id": f"chatcmpl-kxh-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": canon.usage or {},
    }


def build_openai_sse_chunk(content: str, model: str, cid: str, created: int) -> str:
    import json
    obj = {"id": cid, "object": "chat.completion.chunk", "created": created,
           "model": model,
           "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]}
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def build_openai_sse_reasoning(content: str, model: str, cid: str, created: int) -> str:
    import json
    obj = {"id": cid, "object": "chat.completion.chunk", "created": created,
           "model": model,
           "choices": [{"index": 0, "delta": {"reasoning_content": content}, "finish_reason": None}]}
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def build_openai_sse_done(model: str, cid: str, created: int, finish: str = "stop") -> str:
    import json
    obj = {"id": cid, "object": "chat.completion.chunk", "created": created,
           "model": model,
           "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\ndata: [DONE]\n\n"


def extract_openai_stream_deltas(obj: dict) -> tuple[str, str]:
    """Return (content_delta, reasoning_delta) from an OpenAI stream chunk."""
    try:
        choices = obj.get("choices", [])
        delta = (choices[0].get("delta", {}) if choices else {}) or {}
    except (AttributeError, TypeError):
        return "", ""
    if not isinstance(delta, dict):
        return "", ""
    c = delta.get("content") or ""
    r = delta.get("reasoning_content") or ""
    return (c if isinstance(c, str) else "", r if isinstance(r, str) else "")


def extract_openai_tool_deltas(obj: dict) -> list[dict]:
    """Extract streamed tool_calls fragments: [{index,id,name,arguments}]."""
    try:
        choices = obj.get("choices", [])
    except AttributeError:
        return []
    if not choices or not isinstance(choices[0], dict):
        return []
    delta = choices[0].get("delta", {}) or {}
    if not isinstance(delta, dict):
        return []
    out = []
    for tc in delta.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function", {}) or {}
        if not isinstance(fn, dict):
            fn = {}
        args = fn.get("arguments", "") or ""
        out.append({"index": tc.get("index", 0),
                    "id": tc.get("id", "") or "",
                    "name": fn.get("name", "") or "",
                    "arguments": args if isinstance(args, str) else ""})
    return out

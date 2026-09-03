import json

from kakehashi.protocol_openai import extract_openai_tool_deltas
from kakehashi.routes.openai_ingress import (
    _accumulate_tool,
    _build_openai_tool_chunk,
    _extract_delta,
)


def _chunk(delta):
    return json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


def test_tool_fragments_accumulate():
    acc: dict = {}
    c1 = _chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                 "function": {"name": "write_file", "arguments": '{"path":'}}]})
    c2 = _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"a.py"}'}}]})
    for line in (c1, c2):
        content, reasoning, frags = _extract_delta("openai", line)
        assert content == "" and reasoning == ""
        for f in frags:
            _accumulate_tool(acc, f)
    assert extract_openai_tool_deltas(json.loads(c1))[0]["name"] == "write_file"
    out = _build_openai_tool_chunk(acc, "m", "cid", 1)
    obj = json.loads(out[len("data: "):].strip())
    tc = obj["choices"][0]["delta"]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["arguments"] == '{"path":"a.py"}'


def test_anthropic_tool_use_fragments():
    from kakehashi.routes.anthropic_ingress import _extract
    start = json.dumps({"type": "content_block_start", "index": 1,
                        "content_block": {"type": "tool_use", "id": "tu1", "name": "read"}})
    d = json.dumps({"type": "content_block_delta", "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '{"a":'}})
    _, _, f1 = _extract("anthropic", start)
    _, _, f2 = _extract("anthropic", d)
    assert f1[0]["id"] == "tu1" and f1[0]["name"] == "read"
    assert f2[0]["arguments"] == '{"a":'

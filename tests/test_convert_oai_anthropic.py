from kakehashi.models_canonical import CanonicalRequest, Message
from kakehashi.protocol_anthropic import anthropic_to_canonical, canonical_to_anthropic_response
from kakehashi.protocol_openai import canonical_to_openai_response, openai_to_canonical


def test_openai_roundtrip_system_tool():
    body = {
        "model": "x",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ],
        "tools": [{"type": "function", "function": {"name": "f", "description": "d",
                                                    "parameters": {"type": "object"}}}],
    }
    canon = openai_to_canonical(body)
    assert canon.messages[0].role == "system"
    assert canon.tools[0]["name"] == "f"


def test_anthropic_system_fold():
    body = {"model": "x", "system": "sys", "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100}
    canon = anthropic_to_canonical(body)
    assert canon.messages[0].role == "system"
    assert canon.params["max_tokens"] == 100


def test_stop_mapping():
    from kakehashi.models_canonical import CanonicalResponse
    r = CanonicalResponse(text="hi", stop_reason="end_turn")
    oai = canonical_to_openai_response(r, "m")
    assert oai["choices"][0]["finish_reason"] == "stop"
    ant = canonical_to_anthropic_response(r, "m")
    assert ant["stop_reason"] == "end_turn"

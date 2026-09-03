from kakehashi.config import AppConfig, EgressConfig, EgressProvider
from kakehashi.egress import apply_output_guard, resolve_output_guard
from kakehashi.models_canonical import CanonicalRequest, Message
from kakehashi.protocol_anthropic import canonical_to_anthropic_payload
from kakehashi.protocol_openai import canonical_to_openai_payload


def _canon():
    return CanonicalRequest(messages=[Message(role="user", content="hi")], model="m")


def test_guard_default_on():
    cfg = AppConfig(egress=EgressConfig(active_provider="a",
                                        providers=[EgressProvider(id="a", base_url="http://x/v1", model="m")]))
    assert resolve_output_guard(cfg) != ""


def test_guard_appended_openai():
    c = _canon()
    assert apply_output_guard(c, "ENGLISH ONLY") is True
    p = canonical_to_openai_payload(c)
    assert p["messages"][-1] == {"role": "system", "content": "ENGLISH ONLY"}


def test_guard_appended_anthropic():
    c = _canon()
    apply_output_guard(c, "ENGLISH ONLY")
    p = canonical_to_anthropic_payload(c)
    assert "ENGLISH ONLY" in p["system"]


def test_guard_empty_noop():
    c = _canon()
    assert apply_output_guard(c, "  ") is False
    assert len(c.messages) == 1

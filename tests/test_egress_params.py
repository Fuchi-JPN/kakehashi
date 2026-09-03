from kakehashi.config import EgressProvider
from kakehashi.egress import apply_egress_overrides
from kakehashi.models_canonical import CanonicalRequest, Message


def _canon():
    return CanonicalRequest(messages=[Message(role="user", content="hi")],
                            params={"temperature": 0.9}, model="req-model")


def test_override_wins():
    p = EgressProvider(id="a", protocol="openai", base_url="http://x/v1",
                       model="real", params={"merge_policy": "override", "temperature": 0.2})
    c = _canon()
    req, applied = apply_egress_overrides(c, p)
    assert req == "req-model" and applied == "real"
    assert c.params["temperature"] == 0.2


def test_client_wins():
    p = EgressProvider(id="a", protocol="openai", base_url="http://x/v1",
                       model="real", params={"merge_policy": "client_wins", "temperature": 0.2})
    c = _canon()
    apply_egress_overrides(c, p)
    assert c.params["temperature"] == 0.9


def test_auto_keeps():
    p = EgressProvider(id="a", protocol="openai", base_url="http://x/v1", model="auto")
    c = _canon()
    _, applied = apply_egress_overrides(c, p)
    assert applied == "req-model"

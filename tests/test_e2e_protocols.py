"""E2E protocol matrix with mocked upstream."""
import json

import pytest
from fastapi.testclient import TestClient

from kakehashi.app import create_app
from kakehashi.config import AppConfig, EgressConfig, EgressProvider


def _app(tmp_path, protocol):
    cfg_path = tmp_path / "c.yaml"
    app = create_app(str(cfg_path))
    store = app.state.store
    store.update(lambda old: AppConfig(
        server=old.server,
        egress=EgressConfig(active_provider="up", providers=[
            EgressProvider(id="up", protocol=protocol,
                           base_url="http://upstream.test/v1", model="real-model")]),
        translation=old.translation, logging=old.logging, webui=old.webui))
    # disable translation for pure protocol test
    store.update(lambda old: _disable_tr(old))
    return app


def _disable_tr(old: AppConfig):
    d = old.model_dump()
    d["translation"]["enabled"] = False
    return AppConfig.model_validate(d)


@pytest.mark.parametrize("egress_proto,resp_body", [
    ("openai", {"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]}),
    ("anthropic", {"content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn"}),
])
def test_matrix(tmp_path, monkeypatch, egress_proto, resp_body):
    import httpx

    class FakeResp:
        status_code = 200

        def json(self):
            return resp_body

    captured = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResp()

    monkeypatch.setattr("kakehashi.egress.httpx.AsyncClient", FakeClient)
    app = _app(tmp_path, egress_proto)
    c = TestClient(app)
    # OpenAI ingress
    r = c.post("/v1/chat/completions", json={"model": "any", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert captured["json"]["model"] == "real-model"
    # Anthropic ingress
    r2 = c.post("/v1/messages", json={"model": "any", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 100})
    assert r2.status_code == 200, r2.text

import pytest

from kakehashi.config import AppConfig, EgressConfig, EgressProvider, TranslateBackend, TranslationConfig
from kakehashi.translate.client import translate_text


def _cfg(monkeypatch, statuses):
    calls = {"n": 0}

    async def fake_call(backend, text, direction, cfg=None):
        from kakehashi.translate.client import BackendStatusError
        code = statuses[min(calls["n"], len(statuses) - 1)]
        calls["n"] += 1
        if code == 200:
            return "translated!"
        raise BackendStatusError(code, "err")

    monkeypatch.setattr("kakehashi.translate.client._call_backend", fake_call)
    cfg = AppConfig(egress=EgressConfig(active_provider="a",
                                        providers=[EgressProvider(id="a", base_url="http://x/v1", model="m")]),
                    translation=TranslationConfig(backends=[
                        TranslateBackend(id="b1", base_url="http://t1/v1", model="m1"),
                        TranslateBackend(id="b2", base_url="http://t2/v1", model="m2"),
                    ]))
    cfg.translation.retry.cooldown_s = 0
    return cfg


@pytest.mark.asyncio
async def test_fallback_next(monkeypatch):
    cfg = _cfg(monkeypatch, [429, 200])
    out, used, fb = await translate_text("hello", "ja2en", cfg)
    assert out == "translated!" and used == "b2" and fb == 1


@pytest.mark.asyncio
async def test_all_dead_passthrough(monkeypatch):
    cfg = _cfg(monkeypatch, [500, 503])
    out, used, fb = await translate_text("hello", "ja2en", cfg)
    assert out == "hello" and used is None and fb == 2

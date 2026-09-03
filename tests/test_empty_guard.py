import pytest

from kakehashi.config import AppConfig, EgressConfig, EgressProvider, TranslateBackend, TranslationConfig
from kakehashi.translate.client import translate_text


def _cfg():
    return AppConfig(
        egress=EgressConfig(active_provider="a",
                            providers=[EgressProvider(id="a", base_url="http://x/v1", model="m")]),
        translation=TranslationConfig(backends=[
            TranslateBackend(id="b1", base_url="http://t1/v1", model="m1"),
        ]))


@pytest.mark.asyncio
async def test_whitespace_never_sent(monkeypatch):
    """Whitespace-only input must short-circuit without touching any backend."""
    async def _boom(backend, text, direction, cfg=None):
        raise AssertionError("backend must not be called for whitespace input")

    monkeypatch.setattr("kakehashi.translate.client._call_backend", _boom)
    cfg = _cfg()
    for t in ["", "   ", "\n\n", " \t\n "]:
        out, used, fb = await translate_text(t, "en2ja", cfg)
        assert out == t and used is None and fb == 0


@pytest.mark.asyncio
async def test_response_whitespace_skipped():
    from kakehashi.models_canonical import CanonicalResponse
    from kakehashi.translate.engine import translate_response
    cfg = _cfg()
    cr = CanonicalResponse(text="\n\n")
    bid, fb, ph = await translate_response(cr, cfg)
    assert bid is None and cr.text == "\n\n"

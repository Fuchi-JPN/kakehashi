"""Translation backend client with priority fallback chain + cooldown."""
from __future__ import annotations

import logging
import time

import httpx

from ..config import AppConfig, TranslateBackend, resolve_backend_secret
from .prompts import get_prompt

log = logging.getLogger("kakehashi.translate.client")

_cooldowns: dict[str, float] = {}


def _cooldown_active(backend_id: str) -> bool:
    until = _cooldowns.get(backend_id, 0)
    return time.monotonic() < until


def _mark_cooldown(backend_id: str, seconds: int):
    _cooldowns[backend_id] = time.monotonic() + seconds


async def _call_backend(backend: TranslateBackend, text: str, direction: str, cfg=None) -> str:
    secret = resolve_backend_secret(backend)
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    system = get_prompt(direction, cfg)
    base = backend.base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        url = base
    elif base.endswith("/chat"):
        url = base[: -len("/chat")] + "/chat/completions"
    elif base.endswith("/messages"):
        url = base[: -len("/messages")] + "/chat/completions"
    else:
        url = f"{base}/chat/completions"
    payload = {
        "model": backend.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "temperature": 0.1,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=backend.timeout_s) as client:
        r = await client.post(url, json=payload, headers=headers)
        if r.status_code in (429, 500, 502, 503, 504):
            raise BackendStatusError(r.status_code, r.text[:500])
        r.raise_for_status()
        data = r.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise BackendStatusError(502, f"bad translate response: {e}")


class BackendStatusError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"translate backend {status}: {body[:300]}")
        self.status = status
        self.body = body


async def translate_text(text: str, direction: str, cfg: AppConfig) -> tuple[str, str | None, int]:
    """Return (translated_or_original, backend_id_used_or_None, fallbacks)."""
    # Never send empty/whitespace-only input: backends may return
    # meta-commentary (e.g. "入力テキストが空のため…") which would
    # otherwise leak into the user-visible response.
    if not text or not text.strip():
        return text, None, 0
    fallbacks = 0
    retry = cfg.translation.retry
    for backend in cfg.translation.backends:
        if not backend.enabled:
            continue
        if _cooldown_active(backend.id):
            continue
        try:
            out = await _call_backend(backend, text, direction, cfg)
        except BackendStatusError as e:
            if e.status in retry.on_status:
                fallbacks += 1
                _mark_cooldown(backend.id, retry.cooldown_s)
                log.warning("translate backend %s failed %s, fallback", backend.id, e)
                continue
            raise
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if retry.on_timeout:
                fallbacks += 1
                _mark_cooldown(backend.id, retry.cooldown_s)
                log.warning("translate backend %s timeout/error %s, fallback", backend.id, e)
                continue
            raise
        # validation: empty or identical (for non-trivial text) -> treat as failure, try next
        if not out.strip():
            fallbacks += 1
            _mark_cooldown(backend.id, retry.cooldown_s)
            log.warning("translate backend %s empty response, fallback", backend.id)
            continue
        return out, backend.id, fallbacks
    log.warning("all translate backends failed/unavailable, passthrough")
    return text, None, fallbacks


async def batch_translate(texts: list[str], direction: str, cfg: AppConfig) -> list[str]:
    """Translate multiple short single-line texts in ONE backend call.

    Items are numbered as `[KXH-i] text`; the reply is split back by those
    markers. On any mismatch, falls back to per-item translate_text calls.
    Full backend failure returns the originals unchanged.
    """
    if not texts:
        return []
    if len(texts) == 1:
        out, _, _ = await translate_text(texts[0], direction, cfg)
        return [out]
    batch = "\n".join(f"[KXH-{i}] {t}" for i, t in enumerate(texts))
    out, bid, _ = await translate_text(batch, direction, cfg)
    if bid is None:
        return list(texts)
    import re
    found: dict[int, str] = {}
    for line in out.splitlines():
        m = re.match(r"\[KXH-(\d+)\]\s?(.*)$", line.strip())
        if m:
            found[int(m.group(1))] = m.group(2)
    if len(found) == len(texts) and set(found) == set(range(len(texts))):
        return [found[i] for i in range(len(texts))]
    log.warning("batch_translate marker mismatch (%d/%d), per-item fallback",
                len(found), len(texts))
    results = []
    for t in texts:
        o, _, _ = await translate_text(t, direction, cfg)
        results.append(o)
    return results

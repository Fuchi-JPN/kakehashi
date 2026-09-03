"""Egress: forward CanonicalRequest to active upstream provider."""
from __future__ import annotations

import logging
import time

import httpx

from .config import AppConfig, EgressProvider, resolve_provider_secret
from .models_canonical import CanonicalRequest, CanonicalResponse
from .protocol_anthropic import anthropic_response_to_canonical, canonical_to_anthropic_payload
from .protocol_openai import canonical_to_openai_payload, openai_response_to_canonical

log = logging.getLogger("kakehashi.egress")


def active_provider(cfg: AppConfig) -> EgressProvider:
    for p in cfg.egress.providers:
        if p.id == cfg.egress.active_provider:
            return p
    raise RuntimeError(f"active_provider '{cfg.egress.active_provider}' not found")


def egress_url(p: EgressProvider) -> str:
    base = p.base_url.strip().rstrip("/")
    if base.endswith("/chat/completions") or base.endswith("/messages"):
        return base
    if base.endswith("/chat"):
        base = base[: -len("/chat")]
        if p.protocol == "openai":
            return f"{base}/chat/completions"
        return f"{base}/messages"
    if p.protocol == "openai":
        return f"{base}/chat/completions"
    return f"{base}/messages"


def apply_egress_overrides(canon: CanonicalRequest, p: EgressProvider) -> tuple[str, str]:
    requested = canon.model
    applied = requested
    if p.model and p.model != "auto":
        canon.model = p.model
        applied = p.model
    for k in ("temperature", "max_tokens", "top_p", "stop"):
        v = getattr(p.params, k, None)
        if v is not None:
            if p.params.merge_policy == "override" or canon.params.get(k) is None:
                canon.params[k] = v
    return requested, applied


def resolve_output_guard(cfg: AppConfig) -> str:
    """Return configured upstream output-language guard (empty = disabled)."""
    try:
        return (cfg.translation.prompts.output_guard or "").strip()
    except AttributeError:
        return ""


def apply_output_guard(canon: CanonicalRequest, guard: str) -> bool:
    """Append a system instruction forcing English-only upstream output.

    Returns True when applied. The message is added at the END so it acts
    as a final constraint without touching the harness system prompt.
    """
    from .models_canonical import Message
    if not guard or not guard.strip():
        return False
    canon.messages.append(Message(role="system", content=guard.strip()))
    return True


def egress_headers(p: EgressProvider) -> dict:
    secret = resolve_provider_secret(p)
    h: dict = {"Content-Type": "application/json"}
    if p.protocol == "openai":
        if secret:
            h["Authorization"] = f"Bearer {secret}"
    else:
        if secret:
            h["x-api-key"] = secret
        h["anthropic-version"] = "2023-06-01"
    return h


async def send_non_streaming(canon: CanonicalRequest, provider: EgressProvider) -> CanonicalResponse:
    url = egress_url(provider)
    headers = egress_headers(provider)
    if provider.protocol == "openai":
        payload = canonical_to_openai_payload(canon)
        extra = dict(provider.params.extra_body or {})
        for k, v in extra.items():
            payload.setdefault(k, v)
    else:
        if provider.params.extra_body:
            log.warning("egress %s: extra_body ignored for anthropic protocol", provider.id)
        if canon.params.get("max_tokens") is None:
            canon.params["max_tokens"] = provider.params.max_tokens or 4000
        payload = canonical_to_anthropic_payload(canon)
    log.info("egress->%s payload %s", provider.id, payload_summary(payload))
    async with httpx.AsyncClient(timeout=provider.timeout_s) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise UpstreamError(resp.status_code, resp.text[:2000])
        data = resp.json()
    if provider.protocol == "openai":
        return openai_response_to_canonical(data)
    return anthropic_response_to_canonical(data)


class UpstreamError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"upstream {status}: {body[:500]}")
        self.status = status
        self.body = body


def payload_summary(payload: dict) -> dict:
    """Compact structural summary of an egress payload (no raw content)."""
    msgs = payload.get("messages", [])
    roles = []
    for m in msgs:
        if not isinstance(m, dict):
            roles.append("?")
            continue
        r = m.get("role", "?")
        tcs = m.get("tool_calls")
        c = m.get("content", "")
        clen = len(c) if isinstance(c, str) else len(str(c))
        roles.append(f"{r}(len={clen},tc={len(tcs) if isinstance(tcs, list) else 0})")
    total = sum(len(str(m.get('content', ''))) for m in msgs if isinstance(m, dict))
    return {"model": payload.get("model"), "n_messages": len(msgs),
            "roles": roles[:30], "total_content_chars": total,
            "stream": payload.get("stream"),
            "keys": sorted(payload.keys())[:20]}


def log_egress_payload(request_id: str, provider_id: str, payload: dict) -> None:
    log.info("[%s] egress->%s payload %s", request_id, provider_id, payload_summary(payload))


async def fetch_models(protocol: str, base_url: str, api_key: str = "",
                       api_key_env: str = "") -> tuple[list[str], str | None]:
    """Return (models, error). Never raises."""
    import os
    secret = ""
    if api_key_env:
        secret = os.environ.get(api_key_env, "")
    secret = secret or api_key
    base = base_url.strip().rstrip("/")
    # strip endpoint suffixes for /models probing:
    #  /chat/completions -> base, /messages -> base, /chat -> base
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    elif base.endswith("/messages"):
        base = base[: -len("/messages")]
    elif base.endswith("/chat"):
        base = base[: -len("/chat")]
    url = f"{base}/models"
    headers: dict = {}
    if protocol == "openai":
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
    else:
        if secret:
            headers["x-api-key"] = secret
        headers["anthropic-version"] = "2023-06-01"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=headers)
            if r.status_code >= 400:
                return [], f"HTTP {r.status_code}: {r.text[:500]}"
            data = r.json()
            items = data.get("data", data.get("models", []))
            ids = []
            for it in items if isinstance(items, list) else []:
                if isinstance(it, dict):
                    ids.append(it.get("id", str(it)))
                elif isinstance(it, str):
                    ids.append(it)
            return ids, None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"


async def check_upstream(provider: EgressProvider) -> dict:
    t0 = time.monotonic()
    models, err = await fetch_models(provider.protocol, provider.base_url,
                                     provider.api_key, provider.api_key_env)
    dt = int((time.monotonic() - t0) * 1000)
    if err and not models:
        return {"status": "degraded", "provider": provider.id, "latency_ms": dt, "error": err}
    return {"status": "ok", "provider": provider.id, "latency_ms": dt, "models": models[:20]}

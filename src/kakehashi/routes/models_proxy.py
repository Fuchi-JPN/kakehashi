"""GET /v1/models passthrough + /healthz/upstream."""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request

from ..config import resolve_provider_secret
from ..egress import active_provider, check_upstream

log = logging.getLogger("kakehashi.routes.models")

router = APIRouter()


@router.get("/v1/models")
async def list_models(request: Request):
    store = request.app.state.store
    cfg = store.get()
    try:
        provider = active_provider(cfg)
    except RuntimeError as e:
        return {"data": [], "error": str(e)}
    base = provider.base_url.rstrip("/")
    if base.endswith("/chat/completions") or base.endswith("/messages"):
        base = base.rsplit("/", 1)[0]
    url = f"{base}/models"
    headers: dict = {}
    secret = resolve_provider_secret(provider)
    if provider.protocol == "openai":
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
                log.warning("models passthrough failed %s", r.status_code)
                return {"data": []}
            return r.json()
    except Exception as e:
        log.warning("models passthrough error: %s", e)
        return {"data": []}


@router.get("/healthz/upstream")
async def upstream_health(request: Request):
    store = request.app.state.store
    cfg = store.get()
    try:
        provider = active_provider(cfg)
    except RuntimeError as e:
        return {"status": "degraded", "error": str(e)}
    return await check_upstream(provider)

"""Web UI config REST API: /api/config/*"""
from __future__ import annotations

import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..config import (
    AppConfig,
    EgressParams,
    EgressProvider,
    TranslateBackend,
    mask_config_dict,
)
from ..egress import fetch_models
from ..translate_log import TranslateLogger

log = logging.getLogger("kakehashi.webui")

router = APIRouter(prefix="/api/config")


def _store(request: Request):
    return request.app.state.store


def _require_auth(request: Request) -> bool:
    key = _store(request).get().server.api_key
    if not key:
        return True
    return (request.headers.get("X-API-Key") == key
            or request.headers.get("x-api-key") == key
            or request.headers.get("authorization") == f"Bearer {key}")


def _guard(request: Request):
    if not _require_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return None


@router.get("/full")
async def get_full(request: Request):
    if (r := _guard(request)) is not None:
        return r
    cfg = _store(request).get()
    return mask_config_dict(cfg.model_dump())


@router.post("/reload")
async def reload_disk(request: Request):
    if (r := _guard(request)) is not None:
        return r
    try:
        cfg = _store(request).reload_from_disk()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return mask_config_dict(cfg.model_dump())


@router.put("/server")
async def put_server(request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    try:
        cfg = _store(request).update(lambda old: _apply_server(old, body))
    except ValidationError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    return mask_config_dict(cfg.model_dump()["server"])


def _apply_server(old: AppConfig, body: dict) -> AppConfig:
    data = old.model_dump()
    srv = data.get("server", {})
    for k in ("host", "port", "api_key"):
        if k in body:
            srv[k] = body[k]
    data["server"] = srv
    return AppConfig.model_validate(data)


# ---- providers ----
@router.get("/providers")
async def list_providers(request: Request):
    if (r := _guard(request)) is not None:
        return r
    cfg = _store(request).get()
    out = []
    for p in cfg.egress.providers:
        out.append({"id": p.id, "name": p.name, "protocol": p.protocol,
                    "base_url": p.base_url, "model": p.model,
                    "timeout_s": p.timeout_s,
                    "params": p.params.model_dump(),
                    "active": p.id == cfg.egress.active_provider,
                    "api_key_env": p.api_key_env, "api_key_set": bool(p.api_key)})
    return out


@router.post("/providers")
async def create_provider(request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    try:
        cfg = _store(request).update(lambda old: _add_provider(old, body))
    except ValidationError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True}


def _add_provider(old: AppConfig, body: dict) -> AppConfig:
    data = old.model_dump()
    providers = data["egress"].get("providers", [])
    if any(p["id"] == body.get("id") for p in providers):
        raise ValueError(f"provider id '{body.get('id')}' already exists")
    if not body.get("id"):
        body["id"] = f"p-{uuid.uuid4().hex[:8]}"
    p = EgressProvider.model_validate(body)
    providers.append(p.model_dump())
    data["egress"]["providers"] = providers
    return AppConfig.model_validate(data)


@router.put("/providers/{pid}")
async def update_provider(pid: str, request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    try:
        cfg = _store(request).update(lambda old: _edit_provider(old, pid, body))
    except ValidationError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return {"ok": True}


def _edit_provider(old: AppConfig, pid: str, body: dict) -> AppConfig:
    data = old.model_dump()
    providers = data["egress"].get("providers", [])
    for i, p in enumerate(providers):
        if p["id"] == pid:
            merged = {**p, **body, "id": pid}
            # params merge
            if "params" in body and isinstance(body["params"], dict):
                merged_params = {**(p.get("params") or {}), **body["params"]}
                merged["params"] = merged_params
            providers[i] = EgressProvider.model_validate(merged).model_dump()
            data["egress"]["providers"] = providers
            return AppConfig.model_validate(data)
    raise ValueError(f"provider '{pid}' not found")


@router.delete("/providers/{pid}")
async def delete_provider(pid: str, request: Request):
    if (r := _guard(request)) is not None:
        return r
    store = _store(request)
    if store.get().egress.active_provider == pid:
        return JSONResponse({"error": "cannot delete active provider, switch first"}, status_code=400)
    try:
        store.update(lambda old: _del_provider(old, pid))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return {"ok": True}


def _del_provider(old: AppConfig, pid: str) -> AppConfig:
    data = old.model_dump()
    providers = [p for p in data["egress"].get("providers", []) if p["id"] != pid]
    if len(providers) == len(data["egress"].get("providers", [])):
        raise ValueError(f"provider '{pid}' not found")
    data["egress"]["providers"] = providers
    return AppConfig.model_validate(data)


@router.post("/providers/active")
async def switch_active(request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    pid = body.get("id", "")
    try:
        _store(request).update(lambda old: _set_active(old, pid))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return {"ok": True, "active": pid}


def _set_active(old: AppConfig, pid: str) -> AppConfig:
    data = old.model_dump()
    ids = {p["id"] for p in data["egress"].get("providers", [])}
    if pid not in ids:
        raise ValueError(f"provider '{pid}' not found")
    data["egress"]["active_provider"] = pid
    return AppConfig.model_validate(data)


@router.post("/providers/fetch-models")
async def api_fetch_models(request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    models, err = await fetch_models(body.get("protocol", "openai"), body.get("base_url", ""),
                                     body.get("api_key", ""), body.get("api_key_env", ""))
    return {"models": models, "error": err}


@router.post("/providers/{pid}/test")
async def test_provider(pid: str, request: Request):
    if (r := _guard(request)) is not None:
        return r
    cfg = _store(request).get()
    provider = next((p for p in cfg.egress.providers if p.id == pid), None)
    if provider is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    from ..egress import check_upstream
    result = await check_upstream(provider)
    result["ok"] = result.get("status") == "ok"
    return result


# ---- backends ----
@router.get("/backends")
async def list_backends(request: Request):
    if (r := _guard(request)) is not None:
        return r
    cfg = _store(request).get()
    return [{"id": b.id, "name": b.name, "protocol": getattr(b, "protocol", "openai"),
             "base_url": b.base_url, "model": b.model,
             "timeout_s": b.timeout_s, "enabled": b.enabled,
             "api_key_env": b.api_key_env, "api_key_set": bool(b.api_key)}
            for b in cfg.translation.backends]


@router.post("/backends/fetch-models")
async def api_fetch_backend_models(request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    models, err = await fetch_models(body.get("protocol", "openai"), body.get("base_url", ""),
                                     body.get("api_key", ""), body.get("api_key_env", ""))
    return {"models": models, "error": err}


@router.post("/backends")
async def create_backend(request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    try:
        _store(request).update(lambda old: _add_backend(old, body))
    except ValidationError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True}


def _add_backend(old: AppConfig, body: dict) -> AppConfig:
    data = old.model_dump()
    bl = data["translation"].get("backends", [])
    if any(b["id"] == body.get("id") for b in bl):
        raise ValueError("backend id exists")
    if not body.get("id"):
        body["id"] = f"tb-{uuid.uuid4().hex[:8]}"
    bl.append(TranslateBackend.model_validate(body).model_dump())
    data["translation"]["backends"] = bl
    return AppConfig.model_validate(data)


@router.put("/backends/{bid}")
async def update_backend(bid: str, request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    try:
        _store(request).update(lambda old: _edit_backend(old, bid, body))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return {"ok": True}


def _edit_backend(old: AppConfig, bid: str, body: dict) -> AppConfig:
    data = old.model_dump()
    bl = data["translation"].get("backends", [])
    for i, b in enumerate(bl):
        if b["id"] == bid:
            merged = {**b, **body, "id": bid}
            bl[i] = TranslateBackend.model_validate(merged).model_dump()
            data["translation"]["backends"] = bl
            return AppConfig.model_validate(data)
    raise ValueError("not found")


@router.delete("/backends/{bid}")
async def delete_backend(bid: str, request: Request):
    if (r := _guard(request)) is not None:
        return r
    _store(request).update(lambda old: _del_backend(old, bid))
    return {"ok": True}


def _del_backend(old: AppConfig, bid: str) -> AppConfig:
    data = old.model_dump()
    data["translation"]["backends"] = [b for b in data["translation"].get("backends", []) if b["id"] != bid]
    return AppConfig.model_validate(data)


@router.post("/backends/reorder")
async def reorder_backends(request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    ids: list[str] = body.get("ids", [])
    def _mut(old: AppConfig):
        data = old.model_dump()
        by_id = {b["id"]: b for b in data["translation"].get("backends", [])}
        data["translation"]["backends"] = [by_id[i] for i in ids if i in by_id]
        return AppConfig.model_validate(data)
    _store(request).update(_mut)
    return {"ok": True}


@router.post("/backends/{bid}/test")
async def test_backend(bid: str, request: Request):
    if (r := _guard(request)) is not None:
        return r
    from ..translate.client import translate_text
    cfg = _store(request).get()
    t0 = time.monotonic()
    out, used, fb = await translate_text("こんにちは", "ja2en", cfg)
    dt = int((time.monotonic() - t0) * 1000)
    return {"ok": used is not None, "output": out, "backend_used": used,
            "fallbacks": fb, "latency_ms": dt}


# ---- prompts ----
@router.get("/prompts")
async def get_prompts(request: Request):
    if (r := _guard(request)) is not None:
        return r
    return _store(request).get().translation.prompts.model_dump()


@router.put("/prompts")
async def put_prompts(request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    def _mut(old: AppConfig):
        data = old.model_dump()
        pr = data["translation"].get("prompts", {})
        for k in ("ja2en", "en2ja"):
            if k in body and isinstance(body[k], str) and body[k].strip():
                pr[k] = body[k]
        if "output_guard" in body and isinstance(body["output_guard"], str):
            pr["output_guard"] = body["output_guard"]
        data["translation"]["prompts"] = pr
        return AppConfig.model_validate(data)
    cfg = _store(request).update(_mut)
    return cfg.translation.prompts.model_dump()


@router.post("/prompts/reset")
async def reset_prompts(request: Request):
    if (r := _guard(request)) is not None:
        return r
    from ..config import TranslationPrompts
    defaults = TranslationPrompts().model_dump()
    def _mut(old: AppConfig):
        data = old.model_dump()
        data["translation"]["prompts"] = defaults
        return AppConfig.model_validate(data)
    _store(request).update(_mut)
    return defaults


# ---- code strings ----
@router.get("/code-strings")
async def get_code_strings(request: Request):
    if (r := _guard(request)) is not None:
        return r
    return _store(request).get().translation.code_strings.model_dump()


@router.put("/code-strings")
async def put_code_strings(request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    def _mut(old: AppConfig):
        data = old.model_dump()
        cs = data["translation"].get("code_strings", {})
        if "enabled" in body:
            cs["enabled"] = bool(body["enabled"])
        if "min_length" in body:
            cs["min_length"] = max(1, int(body["min_length"]))
        data["translation"]["code_strings"] = cs
        return AppConfig.model_validate(data)
    cfg = _store(request).update(_mut)
    return cfg.translation.code_strings.model_dump()


# ---- logging / dashboard ----
@router.get("/logging")
async def get_logging(request: Request):
    if (r := _guard(request)) is not None:
        return r
    return _store(request).get().logging.model_dump()


@router.put("/logging")
async def put_logging(request: Request):
    if (r := _guard(request)) is not None:
        return r
    body = await request.json()
    def _mut(old: AppConfig):
        data = old.model_dump()
        data["logging"] = {**data["logging"], **body}
        return AppConfig.model_validate(data)
    cfg = _store(request).update(_mut)
    return cfg.logging.model_dump()


@router.get("/logging/tail")
async def logging_tail(request: Request, n: int = 50):
    if (r := _guard(request)) is not None:
        return r
    cfg = _store(request).get().logging
    tl = TranslateLogger(cfg.translation_log_dir, cfg.translation_log_max_mb,
                         cfg.translation_log_backups, cfg.translation_log_enabled)
    return {"entries": tl.tail(n)}


@router.get("/dashboard")
async def dashboard(request: Request):
    if (r := _guard(request)) is not None:
        return r
    cfg = _store(request).get()
    lg = cfg.logging
    tl = TranslateLogger(lg.translation_log_dir, lg.translation_log_max_mb,
                         lg.translation_log_backups, lg.translation_log_enabled)
    entries = tl.tail(1000)
    n = len(entries)
    fb = sum(1 for e in entries if (e.get("translate_fallbacks") or 0) > 0)
    ph_fail = sum(int(e.get("placeholder_fail") or 0) for e in entries)
    try:
        size = tl.path.stat().st_size if tl.path.exists() else 0
    except OSError:
        size = 0
    # latency averages
    def _avg(key):
        vals = [e.get("latency_ms", {}).get(key) for e in entries
                if isinstance(e.get("latency_ms"), dict)
                and isinstance(e["latency_ms"].get(key), (int, float))
                and e["latency_ms"][key] >= 0]
        return round(sum(vals) / len(vals)) if vals else None
    latency_avg = {k: _avg(k) for k in ("translate_in", "upstream", "translate_out", "total")}
    # breakdowns
    ingress_breakdown: dict[str, int] = {}
    for e in entries:
        k = e.get("ingress_protocol", "?") or "?"
        ingress_breakdown[k] = ingress_breakdown.get(k, 0) + 1
    egress_breakdown: dict[str, int] = {}
    for e in entries:
        k = e.get("egress_protocol", "?") or "?"
        egress_breakdown[k] = egress_breakdown.get(k, 0) + 1
    stream_n = sum(1 for e in entries if e.get("stream"))
    # active provider detail
    ap = next((p for p in cfg.egress.providers if p.id == cfg.egress.active_provider), None)
    active_detail = None
    if ap is not None:
        active_detail = {"id": ap.id, "name": ap.name, "protocol": ap.protocol,
                         "base_url": ap.base_url, "model": ap.model,
                         "timeout_s": ap.timeout_s, "params": ap.params.model_dump()}
    # translation chain (priority order)
    chain = [{"id": b.id, "name": b.name, "protocol": getattr(b, "protocol", "openai"),
              "base_url": b.base_url, "model": b.model, "enabled": b.enabled,
              "timeout_s": b.timeout_s} for b in cfg.translation.backends]
    last_used = None
    for e in reversed(entries):
        if e.get("translate_backend_used"):
            last_used = e["translate_backend_used"]
            break
    # upstream health (lightweight, 10s)
    upstream = {"status": "unknown"}
    if ap is not None:
        try:
            from ..egress import check_upstream
            upstream = await check_upstream(ap)
        except Exception as e:
            upstream = {"status": "degraded", "error": str(e)[:300]}
    # recent 10 (newest first) with human fields
    recent = []
    for e in reversed(entries[-10:]):
        lm = e.get("latency_ms", {}) if isinstance(e.get("latency_ms"), dict) else {}
        recent.append({
            "ts": e.get("ts", ""), "request_id": e.get("request_id", ""),
            "route": f"{e.get('ingress_protocol', '?')}→{e.get('egress_protocol', '?')}",
            "model": (e.get("model_override") or {}).get("applied", ""),
            "backend": e.get("translate_backend_used"),
            "fallbacks": e.get("translate_fallbacks", 0),
            "total_ms": lm.get("total"),
            "stream": bool(e.get("stream")),
        })
    return {
        "requests": n, "sample_size": 1000,
        "fallback_count": fb, "fallback_rate": round(fb / n, 3) if n else 0.0,
        "placeholder_fail_total": ph_fail,
        "log_size_bytes": size, "log_path": lg.translation_log_dir,
        "translation_enabled": cfg.translation.enabled,
        "active_provider": active_detail or cfg.egress.active_provider,
        "providers_count": len(cfg.egress.providers),
        "upstream": upstream,
        "translation_chain": chain, "backends": len(chain),
        "last_backend_used": last_used,
        "latency_avg_ms": latency_avg,
        "ingress_breakdown": ingress_breakdown,
        "egress_breakdown": egress_breakdown,
        "stream_count": stream_n, "non_stream_count": n - stream_n,
        "recent": recent,
    }

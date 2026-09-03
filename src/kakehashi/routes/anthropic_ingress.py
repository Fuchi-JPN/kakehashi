"""POST /v1/messages (Anthropic ingress)."""
from __future__ import annotations

import json
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import ConfigStore
from ..egress import UpstreamError, active_provider, apply_egress_overrides, apply_output_guard, egress_headers, egress_url, log_egress_payload, resolve_output_guard
from ..protocol_anthropic import (
    anthropic_to_canonical,
    canonical_to_anthropic_payload,
    canonical_to_anthropic_response,
)
from ..protocol_openai import canonical_to_openai_payload
from ..translate.code_strings import translate_stream_tool_acc, translate_tool_input
from ..translate.engine import translate_request, translate_response, translate_tool_args
from .pipeline import KEEPALIVE_S, base_log_entry, check_auth, get_logger, unauthorized_response

log = logging.getLogger("kakehashi.routes.anthropic")

router = APIRouter()


@router.post("/v1/messages")
async def create_message(request: Request):
    store: ConfigStore = request.app.state.store
    if not check_auth(request, store):
        return JSONResponse({"type": "error", "error": {"message": "unauthorized"}}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"type": "error", "error": {"message": "invalid json"}}, status_code=400)

    request_id = uuid.uuid4().hex[:12]
    t0 = time.monotonic()
    try:
        canon = anthropic_to_canonical(body)
    except Exception as e:
        return JSONResponse({"type": "error", "error": {"message": f"bad request: {e}"}}, status_code=400)

    cfg = store.get()
    try:
        provider = active_provider(cfg)
    except RuntimeError as e:
        return JSONResponse({"type": "error", "error": {"message": str(e)}}, status_code=500)

    # translate IN (streaming defers it into the generator with keep-alive)
    req_original = _snapshot_user_texts(canon)
    logger = get_logger(store)

    if canon.stream:
        return await _streaming(request, store, canon, provider, request_id, t0,
                                req_original, body.get("model", ""), logger)

    t_in0 = time.monotonic()
    try:
        tr_in_backend, tr_in_fb, tr_in_ph = await translate_request(canon, cfg)
    except Exception as e:
        log.warning("[%s] translate_in failed: %s", request_id, e)
        tr_in_backend, tr_in_fb, tr_in_ph = None, 0, 0
    t_in = int((time.monotonic() - t_in0) * 1000)
    req_translated = _snapshot_user_texts(canon)
    requested, applied = apply_egress_overrides(canon, provider)
    guard_on = apply_output_guard(canon, resolve_output_guard(cfg))
    out_model = applied if applied != "auto" else body.get("model", "")

    t_up0 = time.monotonic()
    try:
        from ..egress import send_non_streaming
        canon_resp = await send_non_streaming(canon, provider)
    except UpstreamError as e:
        logger.append({**base_log_entry(request_id, canon, provider),
                       "model_override": {"requested": requested, "applied": applied},
                       "upstream_error": {"status": e.status, "body": e.body[:500]},
                       "latency_ms": {"total": int((time.monotonic() - t0) * 1000)}})
        return JSONResponse({"type": "error", "error": {"message": e.body[:1000]}},
                            status_code=e.status if e.status < 600 else 502)
    except Exception as e:
        log.exception("[%s] egress failed", request_id)
        return JSONResponse({"type": "error", "error": {"message": f"egress failed: {e}"}},
                            status_code=502)
    t_up = int((time.monotonic() - t_up0) * 1000)
    t_out0 = time.monotonic()
    resp_upstream = canon_resp.text
    try:
        tr_out_backend, tr_out_fb, tr_out_ph = await translate_response(canon_resp, cfg)
    except Exception as e:
        log.warning("[%s] translate_out failed: %s", request_id, e)
        tr_out_backend, tr_out_fb, tr_out_ph = None, 0, 0
    t_out = int((time.monotonic() - t_out0) * 1000)
    total = int((time.monotonic() - t0) * 1000)
    try:
        code_strings_n = await translate_tool_args(canon_resp, cfg)
    except Exception as e:
        log.warning("[%s] tool-args translate failed: %s", request_id, e)
        code_strings_n = 0
    logger.append({
        **base_log_entry(request_id, canon, provider),
        "model_override": {"requested": requested, "applied": applied},
        "direction": "ja2en+en2ja", "phase": "non_stream",
        "output_guard": guard_on,
        "translate_backend_used": tr_out_backend or tr_in_backend,
        "translate_fallbacks": tr_in_fb + tr_out_fb,
        "placeholder_fail": tr_in_ph + tr_out_ph,
        "request_original": req_original,
        "request_translated": req_translated,
        "response_upstream": resp_upstream,
        "response_final": canon_resp.text,
        "code_strings": code_strings_n,
        "latency_ms": {"translate_in": t_in, "upstream": t_up,
                       "translate_out": t_out, "total": total},
        "stream": False,
    })
    return canonical_to_anthropic_response(canon_resp, out_model)


async def _streaming(request, store, canon, provider, request_id, t0,
                     req_original, req_model, logger):
    import asyncio

    from .pipeline import SENTENCE_END
    msg_id = f"msg_kxh_{request_id}"
    full_upstream_texts: list[str] = []
    full_upstream_texts: list[str] = []

    def _delta_event(text: str) -> str:
        return (f"event: content_block_delta\ndata: "
                f"{json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text}}, ensure_ascii=False)}\n\n")

    async def gen():
        translate_task = None
        pump_task = None
        t_in = 0
        tr_in_backend, tr_in_fb, tr_in_ph = None, 0, 0
        code_strings_n = 0
        requested, applied = canon.model, canon.model
        out_model = req_model
        try:
            requested, applied = apply_egress_overrides(canon, provider)
            guard_on = apply_output_guard(canon, resolve_output_guard(store.get()))
            out_model = applied if applied != "auto" else req_model
            yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'content': [], 'model': out_model, 'stop_reason': None}}, ensure_ascii=False)}\n\n"
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}, ensure_ascii=False)}\n\n"

            # ---- phase 1: translate_in with keep-alive ----
            cfg = store.get()
            translate_task = asyncio.create_task(translate_request(canon, cfg))
            t_in0 = time.monotonic()
            while not translate_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(translate_task), timeout=KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": ping waiting translate_in\n\n"
                except Exception as e:
                    log.warning("[%s] translate_in failed: %s", request_id, e)
                    break
            if translate_task.done() and not translate_task.cancelled():
                try:
                    tr_in_backend, tr_in_fb, tr_in_ph = translate_task.result()
                except Exception as e:
                    log.warning("[%s] translate_in failed: %s", request_id, e)
            t_in = int((time.monotonic() - t_in0) * 1000)

            # ---- phase 2: egress streaming via queue pump ----
            url = egress_url(provider)
            headers = egress_headers(provider)
            headers["Accept"] = "text/event-stream"
            if provider.protocol == "openai":
                payload = canonical_to_openai_payload(canon)
            else:
                if canon.params.get("max_tokens") is None:
                    canon.params["max_tokens"] = provider.params.max_tokens or 4000
                payload = canonical_to_anthropic_payload(canon)
            payload["stream"] = True
            log_egress_payload(request_id, provider.id, payload)

            q: asyncio.Queue = asyncio.Queue()

            async def _pump():
                try:
                    async with httpx.AsyncClient(timeout=provider.timeout_s) as client:
                        async with client.stream("POST", url, json=payload, headers=headers) as resp:
                            if resp.status_code >= 400:
                                body = await resp.aread()
                                await q.put(("uperror", body.decode(errors="replace")[:1000]))
                            else:
                                async for line in resp.aiter_lines():
                                    await q.put(("line", line))
                except Exception as e:
                    await q.put(("error", f"{type(e).__name__}: {str(e)[:400]}"))
                finally:
                    await q.put(("done", ""))

            pump_task = asyncio.create_task(_pump())
            buf = ""
            ups_error = None
            tool_acc: dict = {}
            while True:
                try:
                    kind, data = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": ping waiting upstream\n\n"
                    continue
                if kind == "done":
                    break
                if kind in ("uperror", "error"):
                    ups_error = data
                    break
                if not data.startswith("data:"):
                    continue
                ds = data[5:].strip()
                if ds == "[DONE]":
                    break
                delta, reasoning, tool_frags = _extract(provider.protocol, ds)
                for frag in tool_frags:
                    _accumulate_tool(tool_acc, frag)
                if reasoning:
                    # Thinking has no signatured block here; forward as plain
                    # text (untranslated) so the client still sees progress.
                    yield _delta_event(reasoning)
                if delta:
                    full_upstream_texts.append(delta)
                    full_upstream_texts.append(delta)
                    buf += delta
                    sents, rest = _split(buf)
                    for s in sents:
                        t = await _tr_piece(s, store)
                        yield _delta_event(t)
                    buf = rest
            for t_ in (translate_task, pump_task):
                if t_ is not None and not t_.done():
                    t_.cancel()
            if ups_error is not None:
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'message': ups_error}})}\n\n"
            else:
                if buf:
                    t = await _tr_piece(buf, store)
                    yield _delta_event(t)
                if tool_acc:
                    try:
                        code_strings_n = await translate_stream_tool_acc(tool_acc, store.get())
                    except Exception as e:
                        log.warning("[%s] stream tool-args translate failed: %s", request_id, e)
                for idx in sorted(tool_acc):
                    a = tool_acc[idx]
                    tid = a["id"] or f"toolu_kxh_{request_id}_{idx}"
                    yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 1 + idx, 'content_block': {'type': 'tool_use', 'id': tid, 'name': a['name'], 'input': {}}}, ensure_ascii=False)}\n\n"
                    yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 1 + idx, 'delta': {'type': 'input_json_delta', 'partial_json': ''.join(a['args'])}}, ensure_ascii=False)}\n\n"
                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 1 + idx}, ensure_ascii=False)}\n\n"
            stop_reason = "tool_use" if tool_acc else "end_turn"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0}, ensure_ascii=False)}\n\n"
            yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason}, 'usage': {}}, ensure_ascii=False)}\n\n"
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            log.exception("[%s] streaming failed", request_id)
            try:
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'message': str(e)[:500]}})}\n\n"
            except Exception:
                pass
        finally:
            for t_ in (translate_task, pump_task):
                if t_ is not None and not t_.done():
                    t_.cancel()
        logger.append({**base_log_entry(request_id, canon, provider),
                       "model_override": {"requested": requested, "applied": applied},
                       "phase": "stream", "translate_backend_used": tr_in_backend,
                       "translate_fallbacks": tr_in_fb, "placeholder_fail": tr_in_ph,
                       "output_guard": guard_on,
                       "request_original": req_original,
                       "request_translated": _snapshot_user_texts(canon),
                       "response_upstream": "".join(full_upstream_texts),
                       "tool_calls": len(tool_acc),
                       "code_strings": code_strings_n,
                       "latency_ms": {"translate_in": t_in, "total": int((time.monotonic() - t0) * 1000)},
                       "stream": True})

    return StreamingResponse(gen(), media_type="text/event-stream")


def _extract(proto: str, ds: str) -> tuple[str, str, list]:
    """Return (content_delta, reasoning_delta, tool_fragments) from one SSE data payload."""
    from ..protocol_openai import extract_openai_stream_deltas, extract_openai_tool_deltas
    try:
        obj = json.loads(ds)
    except Exception:
        return "", "", []
    if not isinstance(obj, dict):
        return "", "", []
    if proto == "openai":
        c, r = extract_openai_stream_deltas(obj)
        return c, r, extract_openai_tool_deltas(obj)
    if obj.get("type") == "content_block_start" and isinstance(obj.get("content_block"), dict):
        cb = obj["content_block"]
        if cb.get("type") == "tool_use":
            return "", "", [{"index": obj.get("index", 0), "id": cb.get("id", "") or "",
                             "name": cb.get("name", "") or "", "arguments": ""}]
    if obj.get("type") == "content_block_delta" and isinstance(obj.get("delta"), dict):
        d = obj["delta"]
        if d.get("type") == "thinking_delta":
            th = d.get("thinking", "")
            return "", th if isinstance(th, str) else "", []
        if d.get("type") == "input_json_delta":
            pj = d.get("partial_json", "")
            return "", "", [{"index": obj.get("index", 0), "id": "", "name": "",
                             "arguments": pj if isinstance(pj, str) else ""}]
        tx = d.get("text", "")
        return (tx if isinstance(tx, str) else ""), "", []
    return "", "", []


def _accumulate_tool(tool_acc: dict, frag: dict) -> None:
    acc = tool_acc.setdefault(frag.get("index", 0), {"id": "", "name": "", "args": []})
    if frag.get("id"):
        acc["id"] = frag["id"]
    if frag.get("name"):
        acc["name"] = frag["name"]
    if frag.get("arguments"):
        acc["args"].append(frag["arguments"])


def _split(buf: str):
    from .pipeline import SENTENCE_END
    sents: list[str] = []
    cur = ""
    for ch in buf:
        cur += ch
        if ch in SENTENCE_END and len(cur.strip()) >= 8:
            sents.append(cur)
            cur = ""
    return sents, cur


async def _tr_piece(text: str, store) -> str:
    cfg = store.get()
    if not cfg.translation.enabled or not text:
        return text
    from ..models_canonical import CanonicalResponse
    cr = CanonicalResponse(text=text)
    try:
        await translate_response(cr, cfg)
    except Exception:
        return text
    return cr.text


def _snapshot_user_texts(canon) -> list[str]:
    from ..models_canonical import TextBlock
    out: list[str] = []
    for m in canon.messages:
        if m.role != "user":
            continue
        if isinstance(m.content, str):
            out.append(m.content)
        else:
            for b in m.content:
                if isinstance(b, TextBlock):
                    out.append(b.text)
    return out

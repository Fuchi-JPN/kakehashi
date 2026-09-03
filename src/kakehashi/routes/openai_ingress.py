"""POST /v1/chat/completions (OpenAI ingress)."""
from __future__ import annotations

import json
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import ConfigStore
from ..egress import (
    UpstreamError,
    active_provider,
    apply_egress_overrides,
    apply_output_guard,
    resolve_output_guard,
    egress_headers,
    egress_url,
    log_egress_payload,
)
from ..models_canonical import CanonicalResponse
from ..protocol_anthropic import anthropic_response_to_canonical, canonical_to_anthropic_payload
from ..protocol_openai import (
    build_openai_sse_chunk,
    build_openai_sse_done,
    build_openai_sse_reasoning,
    canonical_to_openai_payload,
    canonical_to_openai_response,
    openai_response_to_canonical,
    openai_to_canonical,
)
from ..translate.code_strings import translate_stream_tool_acc, translate_tool_input
from ..translate.engine import translate_request, translate_response, translate_tool_args
from .pipeline import SENTENCE_END, KEEPALIVE_S, base_log_entry, check_auth, get_logger, unauthorized_response

log = logging.getLogger("kakehashi.routes.openai")

router = APIRouter()


def _state_store(request: Request) -> ConfigStore:
    return request.app.state.store


@router.post("/v1/chat/completions")
@router.post("/chat/completions")
async def chat_completions(request: Request):
    store: ConfigStore = _state_store(request)
    if not check_auth(request, store):
        return unauthorized_response()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "invalid json"}}, status_code=400)

    request_id = uuid.uuid4().hex[:12]
    t0 = time.monotonic()
    try:
        canon = openai_to_canonical(body)
    except Exception as e:
        log.warning("[%s] ingress parse error: %s", request_id, e)
        return JSONResponse({"error": {"message": f"bad request: {e}"}}, status_code=400)
    try:
        from ..protocol_openai import ingress_message_summary
        log.info("[%s] ingress messages %s", request_id,
                 ingress_message_summary(body.get("messages", [])))
    except Exception:
        pass

    cfg = store.get()
    try:
        provider = active_provider(cfg)
    except RuntimeError as e:
        return JSONResponse({"error": {"message": str(e)}}, status_code=500)

    # translate IN (streaming defers it into the generator with keep-alive)
    req_original = _snapshot_user_texts(canon)
    logger = get_logger(store)

    if canon.stream:
        return await _streaming_response(request, store, canon, provider, request_id,
                                         t0, req_original, logger)

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

    # non-streaming
    t_up0 = time.monotonic()
    try:
        from ..egress import send_non_streaming
        canon_resp = await send_non_streaming(canon, provider)
    except UpstreamError as e:
        logger.append({**base_log_entry(request_id, canon, provider),
                       "model_override": {"requested": requested, "applied": applied},
                       "upstream_error": {"status": e.status, "body": e.body[:500]},
                       "latency_ms": {"total": int((time.monotonic() - t0) * 1000)}})
        return JSONResponse({"error": {"message": e.body[:1000], "type": "upstream_error"}},
                            status_code=e.status if e.status < 600 else 502)
    except Exception as e:
        log.exception("[%s] egress failed", request_id)
        return JSONResponse({"error": {"message": f"egress failed: {e}"}}, status_code=502)
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
        "direction": "ja2en+en2ja",
        "output_guard": guard_on,
        "phase": "non_stream",
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
    return canonical_to_openai_response(canon_resp, applied if applied != "auto" else (body.get("model", "")))


async def _streaming_response(request, store, canon, provider, request_id, t0,
                              req_original, logger):
    """Forward egress SSE with keep-alive.

    translate_in runs inside the generator so the client receives
    `: ping` comments during long translation AND long upstream waits.
    """
    import asyncio

    cid = f"chatcmpl-kxh-{request_id}"
    created = int(time.time())
    full_upstream_texts: list[str] = []
    full_reasoning: list[str] = []
    tool_acc: dict = {}

    async def gen():
        translate_task = None
        pump_task = None
        t_in = 0
        tr_in_backend, tr_in_fb, tr_in_ph = None, 0, 0
        code_strings_n = 0
        requested, applied = canon.model, canon.model
        try:
            requested, applied = apply_egress_overrides(canon, provider)
            guard_on = apply_output_guard(canon, resolve_output_guard(store.get()))
            cfg_model = applied if applied != "auto" else canon.model

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
                extra = dict(provider.params.extra_body or {})
                for k, v in extra.items():
                    payload.setdefault(k, v)
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
            buf_text = ""
            ups_error = None
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
                if not data or not data.startswith("data:"):
                    continue
                data_s = data[5:].strip()
                if data_s == "[DONE]":
                    break
                delta, reasoning, tool_frags = _extract_delta(provider.protocol, data_s)
                if reasoning:
                    full_reasoning.append(reasoning)
                    yield build_openai_sse_reasoning(reasoning, cfg_model, cid, created)
                for frag in tool_frags:
                    _accumulate_tool(tool_acc, frag)
                if delta:
                    full_upstream_texts.append(delta)
                    buf_text += delta
                    # flush complete sentences
                    sentences, rest = _split_sentences(buf_text)
                    for s in sentences:
                        t = await _translate_piece(s, store)
                        yield build_openai_sse_chunk(t, cfg_model, cid, created)
                    buf_text = rest
            for t_ in (translate_task, pump_task):
                if t_ is not None and not t_.done():
                    t_.cancel()
            if ups_error is not None:
                err = {"error": {"message": ups_error, "type": "upstream_error"}}
                yield f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n"
            else:
                if buf_text:
                    t = await _translate_piece(buf_text, store)
                    yield build_openai_sse_chunk(t, cfg_model, cid, created)
                finish = "stop"
                if tool_acc:
                    try:
                        code_strings_n = await translate_stream_tool_acc(tool_acc, store.get())
                    except Exception as e:
                        log.warning("[%s] stream tool-args translate failed: %s", request_id, e)
                    yield _build_openai_tool_chunk(tool_acc, cfg_model, cid, created)
                    finish = "tool_calls"
                yield build_openai_sse_done(cfg_model, cid, created, finish)
        except Exception as e:
            log.exception("[%s] streaming egress failed", request_id)
            try:
                yield f"data: {json.dumps({'error': str(e)[:500]})}\n\ndata: [DONE]\n\n"
            except Exception:
                pass
        finally:
            for t_ in (translate_task, pump_task):
                if t_ is not None and not t_.done():
                    t_.cancel()
            total = int((time.monotonic() - t0) * 1000)
            logger.append({
                **base_log_entry(request_id, canon, provider),
                "model_override": {"requested": requested, "applied": applied},
                "direction": "ja2en+en2ja", "phase": "stream",
                "output_guard": guard_on,
                "translate_backend_used": tr_in_backend,
                "translate_fallbacks": tr_in_fb, "placeholder_fail": tr_in_ph,
                "request_original": req_original,
                "request_translated": _snapshot_user_texts(canon),
                "response_upstream": "".join(full_upstream_texts),
                "reasoning_chars": sum(len(s) for s in full_reasoning),
                "tool_calls": len(tool_acc),
                "code_strings": code_strings_n,
                "latency_ms": {"translate_in": t_in, "upstream": -1,
                               "translate_out": -1, "total": total},
                "stream": True,
            })

    return StreamingResponse(gen(), media_type="text/event-stream")


def _extract_delta(proto: str, data_s: str) -> tuple[str, str, list]:
    """Return (content_delta, reasoning_delta, tool_fragments) from one SSE data payload."""
    from ..protocol_openai import extract_openai_stream_deltas, extract_openai_tool_deltas
    try:
        obj = json.loads(data_s)
    except Exception:
        return "", "", []
    if not isinstance(obj, dict):
        return "", "", []
    if proto == "openai":
        c, r = extract_openai_stream_deltas(obj)
        return c, r, extract_openai_tool_deltas(obj)
    # anthropic egress -> openai ingress
    t = obj.get("type", "")
    idx = obj.get("index", 0)
    if t == "content_block_start" and isinstance(obj.get("content_block"), dict):
        cb = obj["content_block"]
        if cb.get("type") == "tool_use":
            return "", "", [{"index": idx, "id": cb.get("id", "") or "",
                             "name": cb.get("name", "") or "", "arguments": ""}]
    if t == "content_block_delta" and isinstance(obj.get("delta"), dict):
        d = obj["delta"]
        if d.get("type") == "thinking_delta":
            th = d.get("thinking", "")
            return "", th if isinstance(th, str) else "", []
        if d.get("type") == "input_json_delta":
            pj = d.get("partial_json", "")
            return "", "", [{"index": idx, "id": "", "name": "",
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


def _build_openai_tool_chunk(tool_acc: dict, cfg_model: str, cid: str, created: int) -> str:
    tc_list = [{"id": a["id"], "type": "function",
                "function": {"name": a["name"], "arguments": "".join(a["args"])}}
               for _, a in sorted(tool_acc.items())]
    obj = {"id": cid, "object": "chat.completion.chunk", "created": created,
           "model": cfg_model,
           "choices": [{"index": 0, "delta": {"tool_calls": tc_list}, "finish_reason": None}]}
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _split_sentences(buf: str) -> tuple[list[str], str]:
    sentences: list[str] = []
    cur = ""
    for ch in buf:
        cur += ch
        if ch in SENTENCE_END and len(cur.strip()) >= 8:
            sentences.append(cur)
            cur = ""
    return sentences, cur


async def _translate_piece(text: str, store: ConfigStore) -> str:
    cfg = store.get()
    if not cfg.translation.enabled or not text:
        return text
    from ..models_canonical import CanonicalResponse
    cr = CanonicalResponse(text=text)
    try:
        await translate_response(cr, cfg)
    except Exception as e:
        log.warning("stream piece translate failed: %s", e)
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

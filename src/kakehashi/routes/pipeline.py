"""Shared ingress pipeline: auth + translate IN/OUT + egress + log + SSE."""
from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import ConfigStore
from ..egress import UpstreamError, active_provider, apply_egress_overrides, check_upstream, egress_url, send_non_streaming
from ..models_canonical import CanonicalRequest, CanonicalResponse
from ..translate.engine import translate_request, translate_response
from ..translate_log import TranslateLogger

log = logging.getLogger("kakehashi.pipeline")


def check_auth(request: Request, store: ConfigStore) -> bool:
    key = store.get().server.api_key
    if not key:
        return True
    auth = request.headers.get("authorization", "")
    if auth == f"Bearer {key}":
        return True
    if request.headers.get("x-api-key") == key:
        return True
    if request.headers.get("X-API-Key") == key:
        return True
    return False


def unauthorized_response():
    return JSONResponse({"error": {"message": "unauthorized", "type": "auth_error"}}, status_code=401)


def get_logger(store: ConfigStore) -> TranslateLogger:
    cfg = store.get().logging
    logger = TranslateLogger(cfg.translation_log_dir, cfg.translation_log_max_mb,
                             cfg.translation_log_backups, cfg.translation_log_enabled)
    return logger


def base_log_entry(request_id: str, canon: CanonicalRequest, provider) -> dict:
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request_id": request_id,
        "ingress_protocol": canon.raw_ingress,
        "egress_protocol": provider.protocol,
        "egress_provider": provider.id,
        "stream": canon.stream,
    }


SENTENCE_END = ("。", "．", "！", "？", "!", "?", "\n")

# SSE keep-alive interval (seconds). Sent as `: ping` comments (ignored by
# SSE clients) while translation or upstream streaming stalls, to prevent
# harness-side idle timeouts on long requests.
KEEPALIVE_S = 15


async def translate_sentence_buffered(full_text: str, store: ConfigStore):
    """Translate accumulated upstream text sentence-by-sentence for streaming.

    Simplified: split into sentences, translate each via engine, yield translated pieces.
    Falls back to original text on total backend failure.
    """
    from ..models_canonical import CanonicalResponse
    from ..translate.engine import translate_response as _tr
    cfg = store.get()
    if not cfg.translation.enabled or not full_text:
        yield full_text
        return
    # naive sentence split keeping delimiters
    buf = ""
    sentences: list[str] = []
    for ch in full_text:
        buf += ch
        if ch in SENTENCE_END and len(buf.strip()) >= 4:
            sentences.append(buf)
            buf = ""
    if buf:
        sentences.append(buf)
    for s in sentences:
        cr = CanonicalResponse(text=s)
        try:
            await _tr(cr, cfg)
        except Exception as e:
            log.warning("stream sentence translate failed: %s", e)
        yield cr.text

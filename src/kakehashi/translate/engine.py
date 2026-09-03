"""Translation engine: extract -> protect -> translate -> verify -> restore."""
from __future__ import annotations

import logging

from ..config import AppConfig
from ..models_canonical import CanonicalRequest, CanonicalResponse, TextBlock
from .client import translate_text
from .detector import is_japanese
from .protector import protect, restore

log = logging.getLogger("kakehashi.translate.engine")


async def translate_request(canon: CanonicalRequest, cfg: AppConfig):
    """JA->EN for user TextBlocks. Returns (backend_used, fallbacks, placeholder_fail)."""
    if not cfg.translation.enabled:
        return None, 0, 0
    threshold = cfg.translation.cjk_threshold
    patterns = cfg.translation.protect_patterns
    backend_used: str | None = None
    total_fallbacks = 0
    total_ph_fail = 0
    for m in canon.messages:
        if m.role != "user":
            continue
        blocks = m.content if isinstance(m.content, list) else None
        texts: list[TextBlock] = []
        if isinstance(m.content, str):
            if not is_japanese(m.content, threshold):
                continue
            protected, table = protect(m.content, patterns)
            if "__KXH_" not in protected and protected == m.content:
                # still translate (no placeholders but Japanese)
                pass
            out, bid, fb = await translate_text(protected, "ja2en", cfg)
            total_fallbacks += fb
            if bid:
                backend_used = backend_used or bid
            restored, ph_fail = restore(out, table)
            total_ph_fail += ph_fail
            m.content = restored
        elif blocks is not None:
            for b in blocks:
                if isinstance(b, TextBlock) and is_japanese(b.text, threshold):
                    protected, table = protect(b.text, patterns)
                    out, bid, fb = await translate_text(protected, "ja2en", cfg)
                    total_fallbacks += fb
                    if bid:
                        backend_used = backend_used or bid
                    restored, ph_fail = restore(out, table)
                    total_ph_fail += ph_fail
                    b.text = restored
    return backend_used, total_fallbacks, total_ph_fail


async def translate_response(canon: CanonicalResponse, cfg: AppConfig):
    """EN->JA for assistant text. Returns (backend_used, fallbacks, placeholder_fail)."""
    if not cfg.translation.enabled or not canon.text or not canon.text.strip():
        return None, 0, 0
    patterns = cfg.translation.protect_patterns
    protected, table = protect(canon.text, patterns)
    out, bid, fb = await translate_text(protected, "en2ja", cfg)
    restored, ph_fail = restore(out, table)
    # validation: if translation failed fully (bid None) keep original
    canon.text = restored
    return bid, fb, ph_fail


async def translate_tool_args(canon: CanonicalResponse, cfg: AppConfig) -> int:
    """Translate display strings inside tool-call arguments (EN->JA).

    Only string values that look like UI text or parseable Python files are
    touched; identifiers, SQL, paths and code structure are preserved.
    Returns the number of translated strings. Mutates canon.tool_uses.
    """
    if not cfg.translation.enabled or not cfg.translation.code_strings.enabled:
        return 0
    if not canon.tool_uses:
        return 0
    from .code_strings import translate_tool_input
    total = 0
    for tu in canon.tool_uses:
        try:
            new_input, n = await translate_tool_input(tu.input, cfg)
        except Exception as e:
            log.warning("tool-args translate failed for %s: %s", tu.name, e)
            continue
        tu.input = new_input
        total += n
    return total

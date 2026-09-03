"""CJK-ratio based Japanese detection."""
from __future__ import annotations


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF  # hiragana + katakana
        or 0x4E00 <= o <= 0x9FFF  # CJK unified
        or 0x3400 <= o <= 0x4DBF  # extension A
        or 0xAC00 <= o <= 0xD7AF  # hangul
        or 0xFF00 <= o <= 0xFFEF  # fullwidth
    )


def is_japanese(text: str, threshold: float = 0.1) -> bool:
    if not text:
        return False
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return False
    cjk = sum(1 for c in chars if _is_cjk(c))
    return (cjk / len(chars)) >= threshold

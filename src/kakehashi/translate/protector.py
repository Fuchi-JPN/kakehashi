"""Placeholder protection for code/URL/UUID/path."""
from __future__ import annotations

import re

PATTERNS: dict[str, re.Pattern] = {
    "code_block": re.compile(r"```.*?```", re.DOTALL),
    "inline_code": re.compile(r"`[^`\n]+`"),
    "url": re.compile(r"https?://[^\s)>\]]+"),
    "uuid": re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    "path": re.compile(r"(?:\/[\w.\-]+){2,}|(?:[A-Za-z]:\\(?:[^\\\n]+\\)*[^\\\n]*)"),
}

ORDER = ["code_block", "inline_code", "url", "uuid", "path"]


def protect(text: str, enabled: list[str] | None = None) -> tuple[str, dict[str, str]]:
    enabled = enabled or ORDER
    table: dict[str, str] = {}
    idx = 0

    def _repl(m: re.Match) -> str:
        nonlocal idx
        ph = f"__KXH_{idx}__"
        table[ph] = m.group(0)
        idx += 1
        return ph

    out = text
    for name in ORDER:
        if name not in enabled:
            continue
        out = PATTERNS[name].sub(_repl, out)
    return out, table


def restore(text: str, table: dict[str, str]) -> tuple[str, int]:
    fail = 0
    out = text
    for ph, orig in table.items():
        if ph in out:
            out = out.replace(ph, orig)
        else:
            fail += 1
    return out, fail

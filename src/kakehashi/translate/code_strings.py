"""Translate display strings embedded in code (tool-call arguments).

Scope: user-visible English text inside generated code — print messages,
CLI help, error messages, docstrings — translated EN->JA. Code structure,
identifiers, SQL, URLs and format fields are never touched.

v1 supports Python source (via AST, exact source splicing + re-parse
validation). Anything else falls back to whole-string display translation
only when it passes the strict display-text filter.
"""
from __future__ import annotations

import ast
import logging
import re

log = logging.getLogger("kakehashi.translate.code_strings")

_SQL_LEADS = ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "WITH ")
_BRACE_RE = re.compile(r"\{[^{}]*\}")
_ALPHA_RE = re.compile(r"[A-Za-z]")
_WS_RE = re.compile(r"\s")


def _has_code_smell(s: str) -> bool:
    up = s.lstrip().upper()
    if up.startswith(_SQL_LEADS):
        return True
    if "://" in s or "==" in s or "!=" in s:
        return True
    if "\n" in s and any(k in s for k in ("def ", "import ", "class ", "return ", "SELECT ")):
        return True
    return False


def looks_like_display_text(s: str, min_length: int = 8) -> bool:
    """Strict filter for standalone UI text (NOT code files)."""
    if not s or len(s.strip()) < min_length:
        return False
    if not _WS_RE.search(s) or not _ALPHA_RE.search(s):
        return False
    if _has_code_smell(s):
        return False
    return True


def _protect_braces(s: str) -> tuple[str, dict[str, str]]:
    table: dict[str, str] = {}

    def _repl(m: re.Match) -> str:
        ph = f"__KXH_F{len(table)}__"
        table[ph] = m.group(0)
        return ph

    return _BRACE_RE.sub(_repl, s), table


def _restore_braces(s: str, table: dict[str, str]) -> str:
    for ph, orig in table.items():
        s = s.replace(ph, orig)
    return s


def _collect_str_nodes(tree: ast.AST) -> list[tuple[int, int, int, int, str]]:
    """Collect (lineno, col, end_lineno, end_col, value) of plain str constants."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value and "\x00" not in node.value:
                out.append((node.lineno, node.col_offset,
                            node.end_lineno or node.lineno,
                            node.end_col_offset or node.col_offset,
                            node.value))
    return out


async def translate_python_file_strings(src: str, cfg, min_length: int = 8) -> tuple[str, int]:
    """Translate display string literals inside Python source. Returns (new_src, n)."""
    from .client import batch_translate, translate_text
    from .detector import is_japanese

    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return src, 0
    threshold = cfg.translation.cjk_threshold
    nodes = _collect_str_nodes(tree)
    # filter candidates (skip Japanese-origin, code-ish, short)
    cand_idx: list[int] = []
    protected: list[str] = []
    tables: list[dict] = []
    for i, (_, _, _, _, v) in enumerate(nodes):
        if is_japanese(v, threshold):
            continue
        if not looks_like_display_text(v, min_length):
            continue
        p, t = _protect_braces(v)
        cand_idx.append(i)
        protected.append(p)
        tables.append(t)
    if not cand_idx:
        return src, 0
    # batch single-line items; multiline items go individually
    single = [(k, t) for k, t in zip(cand_idx, protected) if "\n" not in t]
    multi = [(k, t) for k, t in zip(cand_idx, protected) if "\n" in t]
    translated: dict[int, str] = {}
    if single:
        keys = [k for k, _ in single]
        outs = await batch_translate([t for _, t in single], "en2ja", cfg)
        for k, o in zip(keys, outs):
            translated[k] = o
    for k, t in multi:
        o, _, _ = await translate_text(t, "en2ja", cfg)
        translated[k] = o
    # splice back (restore braces first), from end to start
    lines = src.splitlines(keepends=True)
    reps = []
    for i, (ln, col, eln, ecol, _) in enumerate(nodes):
        if i not in translated:
            continue
        new_v = _restore_braces(translated[i], tables[cand_idx.index(i)])
        if not new_v.strip() or new_v == nodes[i][4]:
            continue
        reps.append((ln, col, eln, ecol, new_v))
    if not reps:
        return src, 0
    offs = _line_offsets(lines)
    fspans = _fstring_spans(src, offs, tree)
    spliced = _splice_string_literals(src, lines, reps, fspans)
    if spliced is None:
        return src, 0
    new_src, applied = spliced
    if applied == 0:
        return src, 0
    try:
        ast.parse(new_src)
    except (SyntaxError, ValueError):
        log.warning("code_strings: re-parse failed, keeping original")
        return src, 0
    return new_src, applied


def _line_offsets(lines: list[str]) -> list[int]:
    offs = [0]
    for ln in lines:
        offs.append(offs[-1] + len(ln))
    return offs


_QUOTE_RE = re.compile(r"(?i)^([rubf]*)('''|\"\"\"|'|\")")


def _fstring_spans(src: str, offs: list[int], tree: ast.AST) -> list[tuple[int, int, str]]:
    """Locate JoinedStr spans as (start, end, enclosing quote char)."""
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            s = offs[node.lineno - 1] + node.col_offset
            e = offs[(node.end_lineno or node.lineno) - 1] + (node.end_col_offset or node.col_offset)
            m = _QUOTE_RE.match(src[s:s + 12])
            if m:
                spans.append((s, e, m.group(2)[-1]))
    return spans


def _splice_string_literals(src: str, lines: list[str], reps: list,
                            fspans: list[tuple[int, int, str]]) -> tuple[str, int] | None:
    """Replace str-literal source segments, preserving original quoting.

    Handles single-line literals (incl. triple-quoted), f-string literal
    parts (brace-doubled), and multiline triple-quoted blocks. Anything
    ambiguous is skipped per-node. Returns (new_src, applied) or None.
    """
    # offsets of each line start
    offs = _line_offsets(lines)
    out = src
    applied = 0
    try:
        for ln, col, eln, ecol, new_v in sorted(reps, key=lambda r: (r[0], r[1]), reverse=True):
            s = offs[ln - 1] + col
            e = offs[eln - 1] + ecol
            seg = out[s:e]
            if len(seg) < 2:
                continue
            # f-string literal part? (segment is bare text inside JoinedStr)
            fencl = next((q for fs, fe, q in fspans if fs <= s and e <= fe), None)
            if fencl is not None:
                if "\\" in seg or "{{" in seg or "}}" in seg:
                    continue  # escapes present: evaluated value differs from source
                if "\n" in new_v or "\\" in new_v or fencl in new_v:
                    continue
                new_part = new_v.replace("{", "{{").replace("}", "}}")
                out = out[:s] + new_part + out[e:]
                applied += 1
                continue
            # detect quote style: prefixes (r/b/f, combos) + quote char
            m = _QUOTE_RE.match(seg)
            if not m:
                continue
            prefix, quote = m.group(1), m.group(2)
            if "f" in prefix.lower():
                continue  # whole f-string literal (JoinedStr parts handled above)
            if quote in ("'''", '"""'):
                if ln != eln:
                    # multiline block: replace inner content only
                    inner_close = quote
                    if not seg.endswith(inner_close):
                        continue
                    open_len = len(prefix) + 3
                    inner_src = seg[open_len:-3]
                    if "\\" in inner_src or inner_close in new_v:
                        continue
                    out = out[:s] + seg[:open_len] + new_v + inner_close + out[e:]
                    applied += 1
                    continue
                # single-line triple-quoted
                if "\n" in new_v or quote in new_v:
                    continue
                esc = new_v.replace("\\", "\\\\")
                out = out[:s] + f"{prefix}{quote}{esc}{quote}" + out[e:]
                applied += 1
                continue
            if ln != eln or "\n" in new_v:
                continue
            esc = new_v.replace("\\", "\\\\").replace(quote, "\\" + quote)
            out = out[:s] + f"{prefix}{quote}{esc}{quote}" + out[e:]
            applied += 1
    except (IndexError, ValueError):
        return None
    return out, applied


async def translate_arg_string(s: str, cfg) -> tuple[str, int]:
    """Translate one tool-argument string value. Returns (new, n_translated)."""
    from .client import translate_text
    from .detector import is_japanese

    tcfg = cfg.translation.code_strings
    if not cfg.translation.enabled or not tcfg.enabled:
        return s, 0
    if not isinstance(s, str) or len(s.strip()) < tcfg.min_length:
        return s, 0
    if not _WS_RE.search(s) or not _ALPHA_RE.search(s):
        return s, 0
    if is_japanese(s, cfg.translation.cjk_threshold):
        return s, 0
    # code file? -> literal-level translation
    try:
        ast.parse(s)
        is_code = True
    except (SyntaxError, ValueError):
        is_code = False
    if is_code:
        new_s, n = await translate_python_file_strings(s, cfg, tcfg.min_length)
        return new_s, n
    # standalone display text only (strict; SQL/code-ish excluded)
    if not looks_like_display_text(s, tcfg.min_length):
        return s, 0
    p, t = _protect_braces(s)
    out, bid, _ = await translate_text(p, "en2ja", cfg)
    if bid is None:
        return s, 0
    out = _restore_braces(out, t)
    if not out.strip() or out == s:
        return s, 0
    return out, 1


async def translate_tool_input(obj, cfg) -> tuple:
    """Recurse tool-arg JSON, translating display strings. Returns (new_obj, n)."""
    n = 0
    if isinstance(obj, dict):
        new_d = {}
        for k, v in obj.items():
            nv, c = await translate_tool_input(v, cfg)
            new_d[k] = nv
            n += c
        return new_d, n
    if isinstance(obj, list):
        new_l = []
        for v in obj:
            nv, c = await translate_tool_input(v, cfg)
            new_l.append(nv)
            n += c
        return new_l, n
    if isinstance(obj, str):
        return await translate_arg_string(obj, cfg)
    return obj, 0


async def translate_stream_tool_acc(tool_acc: dict, cfg) -> int:
    """Translate JSON args accumulated from streamed tool_calls fragments.

    Replaces each tool's arg fragments with a single re-dumped JSON string.
    Unparseable fragments are left untouched. Returns translated count.
    """
    import json
    n = 0
    for _, a in sorted(tool_acc.items()):
        raw = "".join(a.get("args", []))
        if not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        try:
            new_parsed, c = await translate_tool_input(parsed, cfg)
        except Exception as e:
            log.warning("stream tool-args translate failed: %s", e)
            continue
        if c:
            a["args"] = [json.dumps(new_parsed, ensure_ascii=False)]
            n += c
    return n

import pytest

from kakehashi.config import AppConfig, EgressConfig, EgressProvider
from kakehashi.translate.client import translate_text
from kakehashi.translate.code_strings import (
    looks_like_display_text,
    translate_arg_string,
    translate_python_file_strings,
    translate_tool_input,
)


def _cfg():
    return AppConfig(egress=EgressConfig(
        active_provider="a",
        providers=[EgressProvider(id="a", base_url="http://x/v1", model="m")]))


@pytest.fixture
def fake_tr(monkeypatch):
    async def _fake(text, direction, cfg=None):
        return "JA:" + text, "tb-test", 0
    monkeypatch.setattr("kakehashi.translate.client.translate_text", _fake)
    return _fake


def test_filter():
    assert looks_like_display_text("Add a transaction")
    assert looks_like_display_text("No transactions found.")
    assert not looks_like_display_text("budget.py")
    assert not looks_like_display_text("add")
    assert not looks_like_display_text("SELECT x FROM t WHERE y")
    assert not looks_like_display_text("https://example.com/x")


@pytest.mark.asyncio
async def test_python_literals_translated(fake_tr):
    src = 'print("No transactions found.")\nx = 1\nDB = "budget.db"\n'
    new_src, n = await translate_python_file_strings(src, _cfg())
    assert n == 1
    assert "JA:No transactions found." in new_src
    assert '"budget.db"' in new_src
    import ast
    ast.parse(new_src)


@pytest.mark.asyncio
async def test_identifiers_and_sql_untouched(fake_tr):
    src = 'q = "SELECT x FROM t"\nname = "abc"\n'
    new_src, n = await translate_python_file_strings(src, _cfg())
    assert n == 0
    assert new_src == src


@pytest.mark.asyncio
async def test_triple_single_and_fstring(fake_tr):
    import ast
    src = ('"""Handle the add command."""\n'
           'print(f"Error: bad value.")\n'
           'x = "budget.db"\n')
    new_src, n = await translate_python_file_strings(src, _cfg())
    ast.parse(new_src)
    assert n == 2
    assert "JA:Handle the add command." in new_src
    assert "JA:Error: bad value." in new_src
    assert '"budget.db"' in new_src


@pytest.mark.asyncio
async def test_multiline_docstring(fake_tr):
    import ast
    src = '"""\nFirst line here.\nSecond line here.\n"""\nx = 1\n'
    new_src, n = await translate_python_file_strings(src, _cfg())
    ast.parse(new_src)
    assert n == 1
    assert new_src.startswith('"""')


@pytest.mark.asyncio
async def test_fstring_quote_guard(fake_tr):
    # translation containing the enclosing quote must be skipped safely
    async def _q(text, direction, cfg=None):
        from kakehashi.translate.client import translate_text as _orig  # noqa
        return 'say "hello" now', "tb-test", 0
    import kakehashi.translate.client as _c
    orig = _c.translate_text
    _c.translate_text = _q
    try:
        src = "print(f\"Say hello now\")\n"
        new_src, n = await translate_python_file_strings(src, _cfg())
        import ast
        ast.parse(new_src)
        assert (new_src, n) == (src, 0)
    finally:
        _c.translate_text = orig


@pytest.mark.asyncio
async def test_multiline_result_skipped(monkeypatch):
    async def _nl(text, direction, cfg=None):
        return "line1\nline2", "tb-test", 0
    monkeypatch.setattr("kakehashi.translate.client.translate_text", _nl)
    src = 'print("Hello world")\n'
    new_src, n = await translate_python_file_strings(src, _cfg())
    assert (new_src, n) == (src, 0)


@pytest.mark.asyncio
async def test_tool_input_recursion(fake_tr):
    obj = {"path": "budget.py", "content": 'print("Hello world")\n', "count": 3}
    new_obj, n = await translate_tool_input(obj, _cfg())
    assert new_obj["path"] == "budget.py"
    assert new_obj["count"] == 3
    assert "JA:Hello world" in new_obj["content"]
    assert n == 1


@pytest.mark.asyncio
async def test_batch_markers(monkeypatch):
    seen = {}

    async def _echo(text, direction, cfg=None):
        seen["batch"] = text
        lines = []
        for line in text.splitlines():
            import re
            m = re.match(r"\[KXH-(\d+)\]\s?(.*)$", line)
            lines.append(f"[KXH-{m.group(1)}] T:{m.group(2)}")
        return "\n".join(lines), "tb-test", 0

    monkeypatch.setattr("kakehashi.translate.client.translate_text", _echo)
    from kakehashi.translate.client import batch_translate
    out = await batch_translate(["Add item", "Delete item"], "en2ja", _cfg())
    assert out == ["T:Add item", "T:Delete item"]
    assert "[KXH-0]" in seen["batch"] and "[KXH-1]" in seen["batch"]

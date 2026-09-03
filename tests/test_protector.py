from kakehashi.translate.protector import protect, restore


def test_code_block_protect_restore():
    text = "この関数を直して ```python\nprint(1)\n``` を保つ https://example.com/x 123e4567-e89b-12d3-a456-426614174000"
    protected, table = protect(text)
    assert "__KXH_" in protected
    assert "```" not in protected
    restored, fail = restore(protected, table)
    assert restored == text
    assert fail == 0


def test_placeholder_fail_count():
    protected, table = protect("see https://example.com/a")
    broken = protected.replace(list(table.keys())[0], "MISSING")
    _, fail = restore(broken, table)
    assert fail == 1

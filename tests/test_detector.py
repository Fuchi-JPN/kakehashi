from kakehashi.translate.detector import is_japanese


def test_ja_true():
    assert is_japanese("この関数をリファクタリングして", 0.1)


def test_en_false():
    assert not is_japanese("Refactor this function", 0.1)


def test_empty():
    assert not is_japanese("", 0.1)

from kakehashi.translate_log import TranslateLogger


def test_rotation(tmp_path):
    lg = TranslateLogger(str(tmp_path), max_mb=1, backups=2, enabled=True)
    lg.max_bytes = 50
    for i in range(10):
        lg.append({"i": i, "pad": "x" * 40})
    assert (tmp_path / "translation.jsonl.1").exists()
    assert 1 <= len(lg.tail(3)) <= 3

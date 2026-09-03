from kakehashi.config import ConfigStore


def test_default_gen_and_reload(tmp_path):
    p = tmp_path / "config.yaml"
    s = ConfigStore(p)
    assert s.path.exists()
    assert (p.stat().st_mode & 0o777) == 0o600
    s2 = ConfigStore(p)
    assert s2.get().egress.active_provider == "coderouter"

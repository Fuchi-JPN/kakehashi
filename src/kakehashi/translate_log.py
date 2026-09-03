"""translation.jsonl rolling logger."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger("kakehashi.translate_log")


class TranslateLogger:
    def __init__(self, directory: str, max_mb: int = 10, backups: int = 5, enabled: bool = True):
        self.dir = Path(directory).expanduser()
        self.max_bytes = max_mb * 1024 * 1024
        self.backups = backups
        self.enabled = enabled
        self._lock = threading.Lock()
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self.dir / "translation.jsonl"

    def configure(self, directory: str, max_mb: int, backups: int, enabled: bool):
        self.dir = Path(directory).expanduser()
        self.max_bytes = max_mb * 1024 * 1024
        self.backups = backups
        self.enabled = enabled
        self.dir.mkdir(parents=True, exist_ok=True)

    def _rotate_if_needed(self):
        p = self.path
        if not p.exists() or p.stat().st_size < self.max_bytes:
            return
        for i in range(self.backups, 0, -1):
            src = self.dir / (f"translation.jsonl.{i - 1}" if i > 1 else "translation.jsonl")
            dst = self.dir / f"translation.jsonl.{i}"
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)

    def append(self, entry: dict):
        if not self.enabled:
            return
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self._rotate_if_needed()
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def tail(self, n: int = 50) -> list[dict]:
        p = self.path
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out

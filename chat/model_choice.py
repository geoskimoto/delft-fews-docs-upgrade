"""Admin-selectable chat model, persisted as a short key in a small JSON file.

Read on every call so a change applies to the next request with no restart.
Only keys from config.MODELS are accepted — never a raw model id — so the
admin UI cannot select something the cost table has no rates for.
"""
import json
import os
import threading
from pathlib import Path

from chat import config


class InvalidModel(ValueError):
    pass


def _valid(key) -> bool:
    return isinstance(key, str) and key in config.MODELS


class ModelStore:
    def __init__(self, path: Path, default_key: str):
        self.path = Path(path)
        self.default_key = default_key
        self._lock = threading.Lock()

    def current(self) -> str:
        """Never raises: this runs in the request path."""
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return self.default_key
        key = data.get("model") if isinstance(data, dict) else None
        return key if _valid(key) else self.default_key

    def save(self, key) -> str:
        if not _valid(key):
            raise InvalidModel("model must be one of: " + ", ".join(config.MODELS))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(
                f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            tmp.write_text(json.dumps({"model": key}))
            tmp.replace(self.path)
        return key

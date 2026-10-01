"""Admin-adjustable runtime limits, persisted to a small JSON file.

Read on every call so an edit takes effect on the next request with no restart.
The bounds exist so a typo (2000 for 2.00) cannot become a runaway bill.
"""
import json
import math
import os
import threading
from pathlib import Path

BOUNDS = {
    "daily_budget_usd": (0.0, 50.0),
    "rate_limit_calls": (1, 200),
}


class InvalidLimits(ValueError):
    pass


def _valid_budget(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    lo, hi = BOUNDS["daily_budget_usd"]
    return math.isfinite(value) and lo <= value <= hi


def _valid_calls(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    lo, hi = BOUNDS["rate_limit_calls"]
    return lo <= value <= hi


_VALIDATORS = {
    "daily_budget_usd": _valid_budget,
    "rate_limit_calls": _valid_calls,
}


class LimitsStore:
    def __init__(self, path: Path, defaults: dict):
        self.path = Path(path)
        self.defaults = dict(defaults)
        self._lock = threading.Lock()

    def current(self) -> dict:
        """Never raises: this runs in the request path."""
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            data = {}
        out = {}
        for key, valid in _VALIDATORS.items():
            value = data.get(key)
            out[key] = value if valid(value) else self.defaults[key]
        return out

    def save(self, daily_budget_usd, rate_limit_calls) -> dict:
        values = {
            "daily_budget_usd": daily_budget_usd,
            "rate_limit_calls": rate_limit_calls,
        }
        for key, valid in _VALIDATORS.items():
            if not valid(values[key]):
                lo, hi = BOUNDS[key]
                raise InvalidLimits(f"{key} must be a number between {lo} and {hi}")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(
                f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            tmp.write_text(json.dumps(values))
            tmp.replace(self.path)
        return values

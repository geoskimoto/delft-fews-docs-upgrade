"""CSRF origin check, per-user rate limiting, and the daily spend ceiling."""
import json
import time
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

from chat import config


def origin_allowed(origin: str | None, allowed: str) -> bool:
    """Exact-match Origin check. The chat endpoint is a cookie-authenticated
    state-changing POST, and these pages are static HTML with no server-rendered
    place to seed a CSRF token, so the Origin header is the check that fits.

    Plain `==`, deliberately, not secrets.compare_digest. Origin is attacker-
    supplied and not a secret, so there is no timing channel worth closing —
    and compare_digest raises TypeError on non-ASCII strings, which would turn
    a header anyone can send into an unhandled 500.
    """
    if not origin:
        return False
    return origin == allowed


class RateLimiter:
    """Sliding-window limiter keyed by username. In-process, which is correct
    for a single gunicorn worker. clock is injectable for testing."""

    def __init__(self, max_calls: int, window_seconds: float, clock=time.monotonic):
        self.max_calls = max_calls
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self.clock()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.max_calls:
            return False
        hits.append(now)
        return True


def _today() -> str:
    return date.today().isoformat()


class DailyBudget:
    """Org-wide daily spend ceiling, denominated in dollars.

    A token count cannot express this ceiling: a cached read costs a tenth of
    base input while a 1-hour cache write costs double it, a 20x spread. Summing
    raw tokens would let a few cache writes blow through a cap that looked
    generous, so every token class is priced separately.
    """

    def __init__(self, path: Path, limit_usd: float, clock=_today):
        self.path = Path(path)
        self.limit = limit_usd
        self.clock = clock

    def cost_of(self, usage) -> float:
        return (
            getattr(usage, "input_tokens", 0) * config.RATE_INPUT
            + getattr(usage, "output_tokens", 0) * config.RATE_OUTPUT
            + getattr(usage, "cache_creation_input_tokens", 0) * config.RATE_CACHE_WRITE
            + getattr(usage, "cache_read_input_tokens", 0) * config.RATE_CACHE_READ
        )

    def _load(self) -> float:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return 0.0
        if data.get("date") != self.clock():
            return 0.0
        return float(data.get("spent_usd", 0.0))

    def _save(self, spent: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"date": self.clock(), "spent_usd": spent}))
        tmp.replace(self.path)

    def remaining(self) -> float:
        return self.limit - self._load()

    def exhausted(self) -> bool:
        return self.remaining() <= 0

    def record(self, usage) -> float:
        spent = self._load() + self.cost_of(usage)
        self._save(spent)
        return spent

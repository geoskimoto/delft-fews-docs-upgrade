"""CSRF origin check, per-user rate limiting, and the daily spend ceiling."""
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

from chat import config

log = logging.getLogger(__name__)


def _num(value) -> float:
    """Coerce one SDK usage field to a number.

    The Anthropic SDK types cache_creation_input_tokens and
    cache_read_input_tokens as Optional[int], and they are None — present but
    null — whenever that class of token was not used, which is true of nearly
    every request. `getattr(usage, name, 0)` does NOT help: the attribute
    exists, so the default never fires and None flows into the multiplication.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


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
        # Eight threads share this. The GIL makes the window narrow, but the
        # read-modify-write is still not atomic.
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
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
        # One gunicorn worker, but 8 threads. Without this, two threads
        # interleave load -> compute -> save and most of the spend is lost.
        self._lock = threading.Lock()

    def cost_of(self, usage) -> float:
        return (
            _num(getattr(usage, "input_tokens", 0)) * config.RATE_INPUT
            + _num(getattr(usage, "output_tokens", 0)) * config.RATE_OUTPUT
            + _num(getattr(usage, "cache_creation_input_tokens", 0))
            * config.RATE_CACHE_WRITE
            + _num(getattr(usage, "cache_read_input_tokens", 0))
            * config.RATE_CACHE_READ
        )

    def _load(self) -> float:
        """Never raise. This runs in the request path, and a state file that
        cannot be parsed must degrade to "nothing spent yet", not a 500."""
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return 0.0
        # Valid JSON of the wrong shape ([1,2,3], "hello", null) would make
        # .get raise AttributeError.
        if not isinstance(data, dict) or data.get("date") != self.clock():
            return 0.0
        try:
            spent = float(data.get("spent_usd", 0.0))
        except (TypeError, ValueError):
            return 0.0
        # A negative total would hand out more than the configured ceiling.
        return max(0.0, spent)

    def _save(self, spent: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp name per writer: a shared ".tmp" lets two concurrent
        # renames collide and raise FileNotFoundError.
        tmp = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        tmp.write_text(json.dumps({"date": self.clock(), "spent_usd": spent}))
        tmp.replace(self.path)

    def remaining(self) -> float:
        return self.limit - self._load()

    def exhausted(self) -> bool:
        return self.remaining() <= 0

    def try_reserve(self, amount_usd: float) -> bool:
        """Atomically refuse or HOLD budget before a call is dispatched.

        Calling exhausted() and recording the cost afterwards is check-then-act:
        with eight threads every one reads the same pre-spend balance and they
        all pass. Measured on this branch — six concurrent requests against
        $0.10 remaining all returned 200, and the ledger finished at $4.43
        against a $2.00 ceiling. Reserving under the lock is what makes the
        ceiling a ceiling.
        """
        with self._lock:
            spent = self._load()
            if spent + amount_usd > self.limit:
                return False
            self._write(spent + amount_usd)
            return True

    def settle(self, reserved_usd: float, usage) -> float:
        """Swap a reservation for the real cost once the call has finished."""
        with self._lock:
            spent = max(0.0, self._load() - reserved_usd + self.cost_of(usage))
            self._write(spent)
            return spent

    def _write(self, spent: float) -> None:
        try:
            self._save(spent)
        except OSError:
            # _load already promises never to raise; keep the write side
            # symmetrical. This runs inside a live SSE generator after a 200
            # has been committed, so an escaping OSError would reset the
            # connection mid-answer. Losing one turn's accounting is the
            # better failure — but make it loud.
            log.exception("could not persist daily spend")

    def record(self, usage) -> float:
        with self._lock:
            spent = self._load() + self.cost_of(usage)
            try:
                self._save(spent)
            except OSError:
                # _load already promises never to raise; keep the write side
                # symmetrical. This is called from inside a live SSE generator
                # after a 200 has been committed, so an escaping OSError would
                # reset the connection mid-answer. Losing one turn's accounting
                # is the better failure — but make it loud.
                log.exception("could not persist daily spend")
            return spent

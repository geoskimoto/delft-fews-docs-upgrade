"""DailyBudget and RateLimiter accept a zero-arg callable for their limit,
evaluated on every use, so admin changes apply without a restart."""
import json

import pytest

from chat.security import DailyBudget, RateLimiter


class Knob:
    """A mutable, call-counting limit source."""

    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value


@pytest.fixture
def day():
    return lambda: "2026-01-01"


# -------------------------------------------------------------- DailyBudget

def test_float_limit_still_works(tmp_path, day):
    b = DailyBudget(tmp_path / "b.json", 2.0, clock=day)
    assert b.limit == 2.0
    assert b.remaining() == 2.0
    assert b.try_reserve(1.5) is True
    assert b.try_reserve(1.0) is False


def test_limit_property_reflects_callable(tmp_path, day):
    knob = Knob(3.0)
    b = DailyBudget(tmp_path / "b.json", knob, clock=day)
    assert b.limit == 3.0
    knob.value = 8.5
    assert b.limit == 8.5
    assert isinstance(b.limit, float)


def test_callable_is_not_cached_at_construction(tmp_path, day):
    knob = Knob(1.0)
    b = DailyBudget(tmp_path / "b.json", knob, clock=day)
    knob.value = 4.0
    assert b.remaining() == 4.0


def test_each_use_re_evaluates_the_callable(tmp_path, day):
    knob = Knob(1.0)
    b = DailyBudget(tmp_path / "b.json", knob, clock=day)
    knob.calls = 0
    b.exhausted()
    assert knob.calls >= 1
    n = knob.calls
    b.remaining()
    assert knob.calls > n
    n = knob.calls
    b.try_reserve(0.01)
    assert knob.calls > n


def test_raising_the_limit_unblocks_reserve(tmp_path, day):
    knob = Knob(1.0)
    b = DailyBudget(tmp_path / "b.json", knob, clock=day)
    assert b.try_reserve(0.9) is True
    assert b.try_reserve(0.5) is False
    knob.value = 5.0
    assert b.try_reserve(0.5) is True


def test_lowering_below_spend_exhausts_and_refuses(tmp_path, day):
    knob = Knob(5.0)
    b = DailyBudget(tmp_path / "b.json", knob, clock=day)
    assert b.try_reserve(2.0) is True
    assert b.exhausted() is False
    knob.value = 1.0
    assert b.exhausted() is True
    assert b.remaining() == pytest.approx(-1.0)
    assert b.try_reserve(0.01) is False


def test_lowering_to_zero_exhausts_immediately(tmp_path, day):
    knob = Knob(5.0)
    b = DailyBudget(tmp_path / "b.json", knob, clock=day)
    knob.value = 0.0
    assert b.exhausted() is True
    assert b.try_reserve(0.0001) is False


def test_refused_reserve_does_not_change_the_ledger(tmp_path, day):
    p = tmp_path / "b.json"
    knob = Knob(1.0)
    b = DailyBudget(p, knob, clock=day)
    assert b.try_reserve(0.8) is True
    before = json.loads(p.read_text())
    knob.value = 0.5
    assert b.try_reserve(0.1) is False
    assert json.loads(p.read_text()) == before


def test_settle_and_record_still_work_with_callable(tmp_path, day):
    from types import SimpleNamespace
    knob = Knob(5.0)
    b = DailyBudget(tmp_path / "b.json", knob, clock=day)
    b.try_reserve(1.0)
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=0,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    spent = b.settle(1.0, usage)
    assert spent == pytest.approx(2.0)  # 1M input x $2/MTok
    assert b.remaining() == pytest.approx(3.0)  # $5.00 limit - $2.00


# -------------------------------------------------------------- RateLimiter

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_int_max_calls_still_works():
    r = RateLimiter(2, 60, clock=Clock())
    assert r.max_calls == 2
    assert r.allow("a") and r.allow("a")
    assert r.allow("a") is False


def test_max_calls_property_reflects_callable():
    knob = Knob(3)
    r = RateLimiter(knob, 60, clock=Clock())
    assert r.max_calls == 3
    knob.value = 9
    assert r.max_calls == 9


def test_callable_is_evaluated_per_allow():
    knob = Knob(5)
    r = RateLimiter(knob, 60, clock=Clock())
    knob.calls = 0
    for _ in range(3):
        r.allow("a")
    assert knob.calls >= 3


def test_raising_max_calls_lets_the_next_call_through():
    knob = Knob(2)
    r = RateLimiter(knob, 60, clock=Clock())
    assert r.allow("a") and r.allow("a")
    assert r.allow("a") is False
    knob.value = 3
    assert r.allow("a") is True
    assert r.allow("a") is False


def test_lowering_max_calls_applies_to_the_next_call():
    knob = Knob(5)
    r = RateLimiter(knob, 60, clock=Clock())
    assert r.allow("a") and r.allow("a") and r.allow("a")
    knob.value = 2
    assert r.allow("a") is False
    knob.value = 1
    assert r.allow("a") is False


def test_limit_change_is_per_limiter_not_per_key():
    knob = Knob(1)
    r = RateLimiter(knob, 60, clock=Clock())
    assert r.allow("a") is True
    assert r.allow("b") is True
    assert r.allow("a") is False
    knob.value = 2
    assert r.allow("a") is True
    assert r.allow("b") is True


def test_window_still_slides_with_callable_limit():
    clock = Clock()
    r = RateLimiter(Knob(1), 60, clock=clock)
    assert r.allow("a") is True
    assert r.allow("a") is False
    clock.t = 61
    assert r.allow("a") is True

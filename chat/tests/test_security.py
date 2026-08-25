from types import SimpleNamespace

import pytest

from chat.security import DailyBudget, RateLimiter, origin_allowed

ALLOWED = "https://df-docs.streamflows.org"


def usage(inp=0, out=0, write=0, read=0):
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_creation_input_tokens=write,
        cache_read_input_tokens=read,
    )


def test_matching_origin_is_allowed():
    assert origin_allowed(ALLOWED, ALLOWED) is True


def test_missing_origin_is_rejected():
    assert origin_allowed(None, ALLOWED) is False


def test_foreign_origin_is_rejected():
    assert origin_allowed("https://evil.example", ALLOWED) is False


def test_lookalike_prefix_origin_is_rejected():
    assert origin_allowed(ALLOWED + ".evil.example", ALLOWED) is False


def test_http_variant_of_allowed_origin_is_rejected():
    assert origin_allowed("http://df-docs.streamflows.org", ALLOWED) is False


def test_non_ascii_origin_is_rejected_without_raising():
    """Werkzeug latin-1 decodes header bytes, so a non-ASCII Origin reaches us
    as a str. secrets.compare_digest raises TypeError on those, which would let
    anyone turn a header into an unhandled 500."""
    assert origin_allowed("https://exÃ¤mple.com", ALLOWED) is False


def test_empty_string_origin_is_rejected():
    assert origin_allowed("", ALLOWED) is False


def test_rate_limiter_allows_up_to_the_cap():
    now = [0.0]
    rl = RateLimiter(3, 60, clock=lambda: now[0])
    assert [rl.allow("alice") for _ in range(3)] == [True, True, True]


def test_rate_limiter_blocks_past_the_cap():
    now = [0.0]
    rl = RateLimiter(3, 60, clock=lambda: now[0])
    for _ in range(3):
        rl.allow("alice")
    assert rl.allow("alice") is False


def test_rate_limiter_window_slides():
    now = [0.0]
    rl = RateLimiter(3, 60, clock=lambda: now[0])
    for _ in range(3):
        rl.allow("alice")
    now[0] = 61.0
    assert rl.allow("alice") is True


def test_rate_limiter_is_per_key():
    now = [0.0]
    rl = RateLimiter(1, 60, clock=lambda: now[0])
    assert rl.allow("alice") is True
    assert rl.allow("bob") is True


def test_budget_prices_each_token_class_differently(tmp_path):
    b = DailyBudget(tmp_path / "b.json", 2.00)
    write_cost = b.cost_of(usage(write=100_000))
    read_cost = b.cost_of(usage(read=100_000))
    assert write_cost == pytest.approx(read_cost * 20, rel=1e-6)


def test_budget_cache_write_is_double_base_input(tmp_path):
    b = DailyBudget(tmp_path / "b.json", 2.00)
    assert b.cost_of(usage(write=1_000_000)) == pytest.approx(6.00, rel=1e-6)


def test_budget_output_is_priced_at_output_rate(tmp_path):
    b = DailyBudget(tmp_path / "b.json", 2.00)
    assert b.cost_of(usage(out=1_000_000)) == pytest.approx(15.00, rel=1e-6)


def test_budget_accumulates_across_records(tmp_path):
    b = DailyBudget(tmp_path / "b.json", 2.00)
    b.record(usage(out=100_000))
    b.record(usage(out=100_000))
    assert b.remaining() == pytest.approx(2.00 - 3.00, rel=1e-6)


def test_budget_becomes_exhausted(tmp_path):
    b = DailyBudget(tmp_path / "b.json", 0.50)
    assert b.exhausted() is False
    b.record(usage(write=1_000_000))
    assert b.exhausted() is True


def test_budget_survives_restart(tmp_path):
    path = tmp_path / "b.json"
    DailyBudget(path, 2.00).record(usage(out=100_000))
    assert DailyBudget(path, 2.00).remaining() == pytest.approx(0.50, rel=1e-6)


def test_budget_resets_on_a_new_day(tmp_path):
    day = ["2026-08-24"]
    path = tmp_path / "b.json"
    b = DailyBudget(path, 2.00, clock=lambda: day[0])
    b.record(usage(out=100_000))
    assert b.remaining() == pytest.approx(0.50, rel=1e-6)
    day[0] = "2026-08-25"
    assert DailyBudget(path, 2.00, clock=lambda: day[0]).remaining() == pytest.approx(
        2.00, rel=1e-6
    )


def test_budget_tolerates_a_corrupt_state_file(tmp_path):
    path = tmp_path / "b.json"
    path.write_text("{ not json")
    assert DailyBudget(path, 2.00).remaining() == pytest.approx(2.00, rel=1e-6)

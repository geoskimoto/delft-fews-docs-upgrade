import json
import threading
from datetime import date
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


def test_cost_of_handles_none_valued_cache_fields(tmp_path):
    """The SDK types the cache fields as Optional[int] and sends None — present
    but null — whenever that class of token was unused, which is nearly every
    request. getattr's default does not fire for an existing None attribute, so
    None reaches the multiplication and raises TypeError in the request path."""
    b = DailyBudget(tmp_path / "b.json", 2.00)
    real_shape = SimpleNamespace(
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=80_000,
    )
    cost = b.cost_of(real_shape)
    assert cost == pytest.approx(
        10 * 3e-6 + 5 * 15e-6 + 80_000 * 0.3e-6, rel=1e-6
    )


def test_cost_of_handles_a_usage_object_missing_fields_entirely(tmp_path):
    b = DailyBudget(tmp_path / "b.json", 2.00)
    assert b.cost_of(SimpleNamespace()) == 0.0


@pytest.mark.parametrize("body", ["[1, 2, 3]", '"hello"', "null", "42"])
def test_budget_tolerates_valid_json_of_the_wrong_shape(tmp_path, body):
    """.get on a list or string raises AttributeError; this runs in the
    request path and must degrade to 'nothing spent yet' instead."""
    path = tmp_path / "b.json"
    path.write_text(body)
    assert DailyBudget(path, 2.00).remaining() == pytest.approx(2.00, rel=1e-6)


def test_budget_tolerates_a_non_numeric_spent_value(tmp_path):
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"date": date.today().isoformat(), "spent_usd": "lots"}))
    assert DailyBudget(path, 2.00).remaining() == pytest.approx(2.00, rel=1e-6)


def test_budget_clamps_a_negative_stored_total(tmp_path):
    """A negative total would hand out more than the configured ceiling."""
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"date": date.today().isoformat(), "spent_usd": -50.0}))
    assert DailyBudget(path, 2.00).remaining() == pytest.approx(2.00, rel=1e-6)


def test_record_survives_an_unwritable_state_file(tmp_path):
    """record() is called from inside a live SSE generator, after a 200 has
    already been committed. An escaping OSError would reset the connection
    mid-answer, so losing the accounting is the better failure."""
    b = DailyBudget(tmp_path / "b.json", 2.00)

    def boom(_spent):
        raise OSError("disk full")

    b._save = boom
    assert b.record(SimpleNamespace(output_tokens=1_000)) > 0


def test_concurrent_reservations_cannot_exceed_the_ceiling(tmp_path):
    """The bug this exists to prevent: exhausted() then dispatch then record is
    check-then-act. With eight threads every one read the same pre-spend
    balance and all passed — measured at $4.43 spent against a $2.00 ceiling.
    Reserving under the lock must admit only what actually fits."""
    b = DailyBudget(tmp_path / "b.json", 1.00)
    granted = []

    def worker():
        granted.append(b.try_reserve(0.30))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 0.30 each into a 1.00 ceiling: at most three may be granted.
    assert sum(granted) == 3
    assert b.remaining() == pytest.approx(0.10, rel=1e-6)


def test_settle_replaces_the_reservation_with_the_real_cost(tmp_path):
    b = DailyBudget(tmp_path / "b.json", 2.00)
    assert b.try_reserve(0.50) is True
    b.settle(0.50, SimpleNamespace(output_tokens=1_000))  # actually $0.015
    assert b.remaining() == pytest.approx(2.00 - 0.015, rel=1e-6)


def test_reserve_refuses_once_the_ceiling_is_reached(tmp_path):
    b = DailyBudget(tmp_path / "b.json", 1.00)
    assert b.try_reserve(0.90) is True
    assert b.try_reserve(0.20) is False
    assert b.remaining() == pytest.approx(0.10, rel=1e-6)


def test_rate_limiter_holds_its_cap_under_concurrency(tmp_path):
    """Same class of bug as the budget: an unlocked read-modify-write across
    eight threads."""
    rl = RateLimiter(20, 300)
    allowed = []

    def worker():
        for _ in range(10):
            allowed.append(rl.allow("alice"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(allowed) == 20


def test_concurrent_records_neither_raise_nor_lose_spend(tmp_path):
    """The service runs one gunicorn worker with 8 threads, so two requests can
    interleave load -> compute -> save. Unsynchronised, this loses most of the
    accounting and can raise FileNotFoundError when two temp renames collide."""
    b = DailyBudget(tmp_path / "b.json", 100.0)
    errors = []

    def worker():
        try:
            for _ in range(25):
                b.record(SimpleNamespace(output_tokens=1_000))
        except Exception as exc:  # noqa: BLE001 — any race must surface
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    expected = 8 * 25 * 1_000 * 15e-6
    assert b.remaining() == pytest.approx(100.0 - expected, rel=1e-6)

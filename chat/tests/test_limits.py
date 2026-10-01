"""Spec tests for chat/limits.py: the runtime-adjustable limits store."""
import json
import math
import tempfile
import threading
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from chat.limits import BOUNDS, InvalidLimits, LimitsStore

DEFAULTS = {"daily_budget_usd": 2.0, "rate_limit_calls": 20}


@pytest.fixture
def path(tmp_path):
    return tmp_path / "limits.json"


@pytest.fixture
def store(path):
    return LimitsStore(path, dict(DEFAULTS))


def write_raw(path, text):
    path.write_text(text)


def test_bounds_constant():
    assert BOUNDS == {"daily_budget_usd": (0.0, 50.0), "rate_limit_calls": (1, 200)}


def test_invalid_limits_is_a_value_error():
    assert issubclass(InvalidLimits, ValueError)


# ---------------------------------------------------------------- current()

def test_missing_file_returns_defaults(store):
    assert store.current() == DEFAULTS


def test_current_has_exactly_the_two_keys(store, path):
    write_raw(path, json.dumps({"daily_budget_usd": 3, "rate_limit_calls": 4, "x": 1}))
    assert set(store.current()) == {"daily_budget_usd", "rate_limit_calls"}


@pytest.mark.parametrize("text", [
    "", "not json", "{", "[]", "[1, 2]", "null", '"hello"', "42", "true", "{}",
])
def test_unusable_file_falls_back_to_defaults(store, path, text):
    write_raw(path, text)
    assert store.current() == DEFAULTS


def test_binary_garbage_file_falls_back_to_defaults(store, path):
    path.write_bytes(b"\xff\xfe\x00\x01garbage")
    assert store.current() == DEFAULTS


@pytest.mark.parametrize("bad", [
    '"5"', "null", "true", "false", "NaN", "Infinity", "-Infinity",
    "-0.01", "50.01", "1000", "[]", "{}",
])
def test_bad_budget_falls_back_but_valid_calls_is_honoured(store, path, bad):
    write_raw(path, '{"daily_budget_usd": %s, "rate_limit_calls": 77}' % bad)
    assert store.current() == {"daily_budget_usd": 2.0, "rate_limit_calls": 77}


@pytest.mark.parametrize("bad", [
    '"5"', "null", "true", "false", "NaN", "Infinity", "0", "-3", "201", "99999",
    "[]", "{}",
])
def test_bad_calls_falls_back_but_valid_budget_is_honoured(store, path, bad):
    write_raw(path, '{"daily_budget_usd": 7.5, "rate_limit_calls": %s}' % bad)
    assert store.current() == {"daily_budget_usd": 7.5, "rate_limit_calls": 20}


def test_missing_key_falls_back_per_field(store, path):
    write_raw(path, json.dumps({"daily_budget_usd": 9.0}))
    assert store.current() == {"daily_budget_usd": 9.0, "rate_limit_calls": 20}
    write_raw(path, json.dumps({"rate_limit_calls": 9}))
    assert store.current() == {"daily_budget_usd": 2.0, "rate_limit_calls": 9}


@pytest.mark.parametrize("budget,calls", [(0, 1), (0.0, 1), (50, 200), (50.0, 200)])
def test_bounds_are_inclusive_when_reading(store, path, budget, calls):
    write_raw(path, json.dumps({"daily_budget_usd": budget, "rate_limit_calls": calls}))
    assert store.current() == {"daily_budget_usd": budget, "rate_limit_calls": calls}


def test_current_rereads_the_file_each_call(store, path):
    assert store.current() == DEFAULTS
    write_raw(path, json.dumps({"daily_budget_usd": 4.0, "rate_limit_calls": 8}))
    assert store.current() == {"daily_budget_usd": 4.0, "rate_limit_calls": 8}
    write_raw(path, json.dumps({"daily_budget_usd": 6.0, "rate_limit_calls": 9}))
    assert store.current() == {"daily_budget_usd": 6.0, "rate_limit_calls": 9}
    path.unlink()
    assert store.current() == DEFAULTS


def test_another_store_instance_sees_saves(path):
    a = LimitsStore(path, dict(DEFAULTS))
    b = LimitsStore(path, dict(DEFAULTS))
    a.save(5.5, 12)
    assert b.current() == {"daily_budget_usd": 5.5, "rate_limit_calls": 12}


def test_current_returns_a_fresh_dict(store):
    first = store.current()
    first["daily_budget_usd"] = 999
    assert store.current() == DEFAULTS


def test_defaults_dict_is_not_aliased_to_results(path):
    defaults = dict(DEFAULTS)
    s = LimitsStore(path, defaults)
    s.current()["rate_limit_calls"] = 1
    assert s.current()["rate_limit_calls"] == 20


def test_unreadable_path_never_raises(tmp_path):
    # path is a directory: read raises IsADirectoryError, current() must not.
    d = tmp_path / "limits.json"
    d.mkdir()
    assert LimitsStore(d, dict(DEFAULTS)).current() == DEFAULTS


# ------------------------------------------------------------------- save()

def test_save_returns_new_values_and_persists(store, path):
    out = store.save(7.25, 33)
    assert out == {"daily_budget_usd": 7.25, "rate_limit_calls": 33}
    assert store.current() == out
    on_disk = json.loads(path.read_text())
    assert on_disk["daily_budget_usd"] == 7.25
    assert on_disk["rate_limit_calls"] == 33


def test_save_accepts_int_budget(store):
    out = store.save(5, 10)
    assert out["daily_budget_usd"] == 5
    assert store.current()["daily_budget_usd"] == 5


@pytest.mark.parametrize("budget,calls", [(0, 1), (0.0, 1), (50, 200), (50.0, 200)])
def test_save_accepts_inclusive_bounds(store, budget, calls):
    assert store.save(budget, calls) == {
        "daily_budget_usd": budget, "rate_limit_calls": calls,
    }


def test_save_creates_missing_parent_dir(tmp_path):
    p = tmp_path / "a" / "b" / "limits.json"
    LimitsStore(p, dict(DEFAULTS)).save(3.0, 3)
    assert p.exists()


def test_save_leaves_no_temp_files(store, path):
    store.save(3.0, 3)
    store.save(4.0, 4)
    assert [p.name for p in path.parent.iterdir()] == ["limits.json"]


BAD_BUDGETS = [
    "5", None, True, False, float("nan"), float("inf"), float("-inf"),
    -0.01, -1, 50.01, 51, 1e9, [], {},
]
BAD_CALLS = [
    "5", None, True, False, float("nan"), float("inf"), 0, -1, 201, 10**9,
    5.5, 5.0, 1.0, 200.0, [], {},
]


@pytest.mark.parametrize("bad", BAD_BUDGETS, ids=repr)
def test_save_rejects_bad_budget_and_writes_nothing(store, path, bad):
    with pytest.raises(InvalidLimits):
        store.save(bad, 10)
    assert not path.exists()


@pytest.mark.parametrize("bad", BAD_CALLS, ids=repr)
def test_save_rejects_bad_calls_and_writes_nothing(store, path, bad):
    with pytest.raises(InvalidLimits):
        store.save(5.0, bad)
    assert not path.exists()


@pytest.mark.parametrize("bad", BAD_BUDGETS[:6], ids=repr)
def test_rejected_save_leaves_existing_file_byte_identical(store, path, bad):
    store.save(6.0, 15)
    before = path.read_bytes()
    with pytest.raises(InvalidLimits):
        store.save(bad, 10)
    with pytest.raises(InvalidLimits):
        store.save(5.0, 0)
    assert path.read_bytes() == before
    assert store.current() == {"daily_budget_usd": 6.0, "rate_limit_calls": 15}
    assert [p.name for p in path.parent.iterdir()] == ["limits.json"]


def test_valid_budget_is_not_persisted_when_calls_is_invalid(store, path):
    with pytest.raises(InvalidLimits):
        store.save(9.0, 500)
    assert not path.exists()


def test_validation_lives_in_the_store_not_just_the_route(store):
    # Both fields invalid at once is still a single InvalidLimits.
    with pytest.raises(InvalidLimits):
        store.save("a", "b")


# ---------------------------------------------------------------- threading

def test_concurrent_saves_never_leave_a_corrupt_or_partial_file(path):
    # Defaults are outside every saved value, so a torn read that makes
    # current() fall back would be visible as a default value.
    store = LimitsStore(path, {"daily_budget_usd": 1.5, "rate_limit_calls": 2})
    saved = {(10.0 + i, 30 + i) for i in range(8)}
    store.save(10.0, 30)
    stop = threading.Event()
    seen_bad = []

    def reader():
        while not stop.is_set():
            cur = store.current()
            pair = (cur["daily_budget_usd"], cur["rate_limit_calls"])
            if pair not in saved:
                seen_bad.append(pair)

    def writer(i):
        for _ in range(60):
            store.save(10.0 + i, 30 + i)

    errors = []

    def guarded(fn, *a):
        try:
            fn(*a)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    readers = [threading.Thread(target=guarded, args=(reader,)) for _ in range(2)]
    writers = [threading.Thread(target=guarded, args=(writer, i)) for i in range(8)]
    for t in readers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()

    assert errors == []
    assert seen_bad == []
    data = json.loads(path.read_text())
    assert (data["daily_budget_usd"], data["rate_limit_calls"]) in saved
    assert [p.name for p in path.parent.iterdir()] == ["limits.json"]


# --------------------------------------------------------------- properties

valid_budgets = st.one_of(
    st.integers(0, 50),
    st.floats(0.0, 50.0, allow_nan=False, allow_infinity=False),
)
valid_calls = st.integers(1, 200)


@settings(max_examples=60, deadline=None)
@given(budget=valid_budgets, calls=valid_calls)
def test_property_valid_pairs_roundtrip(budget, calls):
    with tempfile.TemporaryDirectory() as d:
        s = LimitsStore(Path(d) / "limits.json", dict(DEFAULTS))
        out = s.save(budget, calls)
        assert out == {"daily_budget_usd": budget, "rate_limit_calls": calls}
        assert LimitsStore(Path(d) / "limits.json", dict(DEFAULTS)).current() == out


invalid_budgets = st.one_of(
    st.floats(max_value=-1e-9, allow_nan=False, allow_infinity=True),
    st.floats(min_value=50.000001, allow_nan=False, allow_infinity=True),
    st.just(float("nan")),
    st.text(max_size=5),
    st.none(),
    st.booleans(),
)


@settings(max_examples=60, deadline=None)
@given(bad=invalid_budgets)
def test_property_invalid_budget_never_writes(bad):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "limits.json"
        s = LimitsStore(p, dict(DEFAULTS))
        with pytest.raises(InvalidLimits):
            s.save(bad, 10)
        assert not p.exists()


@settings(max_examples=60, deadline=None)
@given(bad=st.one_of(
    st.integers(max_value=0), st.integers(min_value=201),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=5), st.none(), st.booleans(),
))
def test_property_invalid_calls_never_writes(bad):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "limits.json"
        s = LimitsStore(p, dict(DEFAULTS))
        with pytest.raises(InvalidLimits):
            s.save(5.0, bad)
        assert not p.exists()


@settings(max_examples=80, deadline=None)
@given(content=st.binary(max_size=200))
def test_property_current_never_raises_and_stays_in_bounds(content):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "limits.json"
        p.write_bytes(content)
        cur = LimitsStore(p, dict(DEFAULTS)).current()
        lo, hi = BOUNDS["daily_budget_usd"]
        assert lo <= cur["daily_budget_usd"] <= hi
        assert not math.isnan(cur["daily_budget_usd"])
        lo, hi = BOUNDS["rate_limit_calls"]
        assert lo <= cur["rate_limit_calls"] <= hi

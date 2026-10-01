"""Spec tests for the admin-selectable model: config.MODELS, ModelStore, and
per-model spend pricing in DailyBudget."""
import json
import threading
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from chat import config
from chat.model_choice import InvalidModel, ModelStore
from chat.security import DailyBudget


# ============================================================== config.MODELS

def test_models_has_exactly_sonnet_then_haiku():
    assert list(config.MODELS) == ["sonnet", "haiku"]


def test_default_model_is_sonnet():
    assert config.DEFAULT_MODEL == "sonnet"


def test_each_model_has_exact_field_set():
    for entry in config.MODELS.values():
        assert set(entry) == {"id", "label", "rate_input", "rate_output", "supports_effort"}


def test_sonnet_entry_matches_legacy_constants():
    s = config.MODELS["sonnet"]
    assert s["id"] == "claude-sonnet-5" == config.MODEL
    assert s["label"] == "Sonnet"
    assert s["rate_input"] == pytest.approx(2.0 / 1e6) == pytest.approx(config.RATE_INPUT)
    assert s["rate_output"] == pytest.approx(10.0 / 1e6) == pytest.approx(config.RATE_OUTPUT)
    assert s["supports_effort"] is True


def test_haiku_entry():
    h = config.MODELS["haiku"]
    assert h["id"] == "claude-haiku-4-5"
    assert h["label"] == "Haiku"
    assert h["rate_input"] == pytest.approx(1.0 / 1e6)
    assert h["rate_output"] == pytest.approx(5.0 / 1e6)
    assert h["supports_effort"] is False


def test_legacy_constants_remain():
    assert config.EFFORT == "medium"
    assert config.RATE_CACHE_WRITE == pytest.approx(config.RATE_INPUT * 2)
    assert config.RATE_CACHE_READ == pytest.approx(config.RATE_INPUT * 0.1)


# ================================================================ ModelStore

@pytest.fixture
def path(tmp_path):
    return tmp_path / "state" / "model.json"


def test_missing_file_gives_default(path):
    assert ModelStore(path, "sonnet").current() == "sonnet"


def test_default_key_is_honoured_not_hardcoded(path):
    assert ModelStore(path, "haiku").current() == "haiku"


def test_save_returns_key_and_current_reads_it(path):
    store = ModelStore(path, "sonnet")
    assert store.save("haiku") == "haiku"
    assert store.current() == "haiku"


def test_save_creates_parent_dir_and_persists_json_with_model_key(path):
    ModelStore(path, "sonnet").save("haiku")
    assert path.parent.is_dir()
    assert json.loads(path.read_text())["model"] == "haiku"


def test_a_fresh_store_sees_persisted_choice(path):
    ModelStore(path, "sonnet").save("haiku")
    assert ModelStore(path, "sonnet").current() == "haiku"


def test_current_rereads_file_each_call_not_cached(path):
    store = ModelStore(path, "sonnet")
    assert store.current() == "sonnet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": "haiku"}))
    assert store.current() == "haiku"
    path.write_text(json.dumps({"model": "sonnet"}))
    assert store.current() == "sonnet"
    path.unlink()
    assert store.current() == "sonnet"


def test_deleting_file_after_save_reverts_to_default(path):
    store = ModelStore(path, "sonnet")
    store.save("haiku")
    path.unlink()
    assert store.current() == "sonnet"


@pytest.mark.parametrize("content", [
    "", "not json", "{", "null", "[]", "[1,2]", '"haiku"', "42", "true",
    "{}", '{"model": null}', '{"model": 1}', '{"model": true}',
    '{"model": ["haiku"]}', '{"model": {"a": 1}}', '{"model": ""}',
    '{"model": "HAIKU"}', '{"model": "claude-haiku-4-5"}',
    '{"model": "gpt-4"}', '{"other": "haiku"}',
])
def test_unusable_file_content_falls_back_to_default_without_raising(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    assert ModelStore(path, "sonnet").current() == "sonnet"


def test_binary_garbage_falls_back(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe\x00\x80garbage")
    assert ModelStore(path, "sonnet").current() == "sonnet"


def test_path_being_a_directory_does_not_raise(path):
    path.mkdir(parents=True)
    assert ModelStore(path, "sonnet").current() == "sonnet"


BAD_KEYS = [None, 1, 0, True, False, 1.5, "", " ", "SONNET", "Haiku", "HAIKU",
            "sonnet ", " haiku", "claude-haiku-4-5", "claude-sonnet-5",
            "opus", "unknown", ["haiku"], {"model": "haiku"}, b"haiku"]


@pytest.mark.parametrize("bad", BAD_KEYS, ids=repr)
def test_save_rejects_invalid_key_and_writes_nothing(path, bad):
    store = ModelStore(path, "sonnet")
    with pytest.raises(InvalidModel):
        store.save(bad)
    assert not path.exists()
    assert not path.parent.exists() or list(path.parent.iterdir()) == []


@pytest.mark.parametrize("bad", BAD_KEYS, ids=repr)
def test_rejected_save_leaves_existing_file_byte_identical(path, bad):
    store = ModelStore(path, "sonnet")
    store.save("haiku")
    before = path.read_bytes()
    with pytest.raises(InvalidModel):
        store.save(bad)
    assert path.read_bytes() == before
    assert store.current() == "haiku"
    assert [p.name for p in path.parent.iterdir()] == ["model.json"]


def test_invalid_model_is_a_value_error():
    assert issubclass(InvalidModel, ValueError)


def test_save_leaves_no_temp_files(path):
    store = ModelStore(path, "sonnet")
    for key in ("haiku", "sonnet", "haiku"):
        store.save(key)
    assert [p.name for p in path.parent.iterdir()] == ["model.json"]


def test_concurrent_saves_never_leave_a_corrupt_or_partial_file(path):
    # Default is sonnet; only haiku is ever saved, so a torn read that makes
    # current() fall back would show up as "sonnet".
    store = ModelStore(path, "sonnet")
    store.save("haiku")
    stop = threading.Event()
    seen_bad = []
    errors = []

    def reader():
        while not stop.is_set():
            if store.current() != "haiku":
                seen_bad.append(1)

    def writer():
        for _ in range(80):
            store.save("haiku")

    def guarded(fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    readers = [threading.Thread(target=guarded, args=(reader,)) for _ in range(2)]
    writers = [threading.Thread(target=guarded, args=(writer,)) for _ in range(8)]
    for t in readers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()

    assert errors == []
    assert seen_bad == []
    assert json.loads(path.read_text())["model"] == "haiku"
    assert [p.name for p in path.parent.iterdir()] == ["model.json"]


@settings(max_examples=60, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(key=st.one_of(st.text(max_size=20), st.integers(), st.none(), st.booleans()))
def test_property_only_exact_known_keys_are_accepted(tmp_path_factory, key):
    p = tmp_path_factory.mktemp("m") / "model.json"
    store = ModelStore(p, "sonnet")
    if isinstance(key, str) and key in ("sonnet", "haiku"):
        assert store.save(key) == key
        assert store.current() == key
    else:
        with pytest.raises(InvalidModel):
            store.save(key)
        assert not p.exists()
        assert store.current() == "sonnet"


# ============================================================ pricing (cost_of)

@pytest.fixture
def budget(tmp_path):
    return DailyBudget(tmp_path / "budget.json", 100.0)


def U(i=0, o=0, cw=0, cr=0):
    return SimpleNamespace(input_tokens=i, output_tokens=o,
                           cache_creation_input_tokens=cw, cache_read_input_tokens=cr)


def test_cost_of_without_model_key_is_sonnet_rates(budget):
    u = U(1000, 2000, 3000, 4000)
    expected = 1000 * 2e-6 + 2000 * 10e-6 + 3000 * 4e-6 + 4000 * 0.2e-6
    assert budget.cost_of(u) == pytest.approx(expected)
    assert budget.cost_of(u, None) == pytest.approx(expected)
    assert budget.cost_of(u, "sonnet") == pytest.approx(expected)
    assert budget.cost_of(u, model_key=None) == pytest.approx(expected)


@pytest.mark.parametrize("usage,expected", [
    (U(i=1_000_000), 1.0),
    (U(o=1_000_000), 5.0),
    (U(cw=1_000_000), 2.0),
    (U(cr=1_000_000), 0.1),
])
def test_cost_of_haiku_prices_each_token_class(budget, usage, expected):
    assert budget.cost_of(usage, "haiku") == pytest.approx(expected)


@pytest.mark.parametrize("usage,expected", [
    (U(i=1_000_000), 2.0),
    (U(o=1_000_000), 10.0),
    (U(cw=1_000_000), 4.0),
    (U(cr=1_000_000), 0.2),
])
def test_cost_of_sonnet_prices_each_token_class(budget, usage, expected):
    assert budget.cost_of(usage, "sonnet") == pytest.approx(expected)


def test_cost_of_haiku_combined(budget):
    u = U(1000, 2000, 3000, 4000)
    assert budget.cost_of(u, "haiku") == pytest.approx(
        1000 * 1e-6 + 2000 * 5e-6 + 3000 * 2e-6 + 4000 * 0.1e-6)


def test_haiku_is_strictly_cheaper_than_sonnet(budget):
    u = U(1000, 2000, 3000, 4000)
    assert budget.cost_of(u, "haiku") < budget.cost_of(u, "sonnet")


def test_cost_of_haiku_tolerates_none_token_fields(budget):
    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=None,
                        cache_creation_input_tokens=None, cache_read_input_tokens=None)
    assert budget.cost_of(u, "haiku") == pytest.approx(1.0)


def test_unknown_model_key_never_undercharges_vs_sonnet(budget):
    u = U(1000, 2000, 3000, 4000)
    try:
        cost = budget.cost_of(u, "no-such-model")
    except (ValueError, KeyError):
        return
    assert cost >= budget.cost_of(u, "sonnet") - 1e-12


@pytest.mark.parametrize("raw_id", ["claude-haiku-4-5", "HAIKU"])
def test_raw_ids_or_wrong_case_never_undercharge(budget, raw_id):
    u = U(1000, 2000, 3000, 4000)
    try:
        cost = budget.cost_of(u, raw_id)
    except (ValueError, KeyError):
        return
    assert cost >= budget.cost_of(u, "sonnet") - 1e-12


# ==================================================================== settle

def ledger(tmp_path):
    return json.loads((tmp_path / "budget.json").read_text())["spent_usd"]


def test_settle_default_prices_at_sonnet(tmp_path, budget):
    assert budget.try_reserve(1.0)
    spent = budget.settle(1.0, U(1_000_000))
    assert spent == pytest.approx(2.0)
    assert ledger(tmp_path) == pytest.approx(2.0)


def test_settle_with_haiku_prices_at_haiku(tmp_path, budget):
    assert budget.try_reserve(1.0)
    spent = budget.settle(1.0, U(i=1_000_000, o=1_000_000, cw=1_000_000, cr=1_000_000),
                          model_key="haiku")
    assert spent == pytest.approx(1.0 + 5.0 + 2.0 + 0.1)
    assert ledger(tmp_path) == pytest.approx(8.1)


def test_settle_positional_model_key(tmp_path, budget):
    budget.try_reserve(0.5)
    assert budget.settle(0.5, U(i=1_000_000), "haiku") == pytest.approx(1.0)


def test_settle_releases_reservation_and_charges_only_actual_for_haiku(tmp_path, budget):
    budget.try_reserve(4.0)
    assert ledger(tmp_path) == pytest.approx(4.0)
    budget.settle(4.0, U(o=100_000), model_key="haiku")
    assert ledger(tmp_path) == pytest.approx(0.5)

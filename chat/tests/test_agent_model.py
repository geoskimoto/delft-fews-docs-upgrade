"""Spec tests: Agent honours model_key (model id, effort kwarg, estimate)."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat import config
from chat.agent import Agent
from chat.security import DailyBudget


class FakeStream:
    def __init__(self, chunks, final):
        self.text_stream = iter(chunks)
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._final


def message(stop_reason="end_turn", content=None):
    return SimpleNamespace(
        stop_reason=stop_reason, content=content or [],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )


def tool_use_block(block_id="tu_1"):
    return SimpleNamespace(type="tool_use", id=block_id, name="lookup_config_fields",
                           input={"config_file": "locations"})


@pytest.fixture
def schema_dir(tmp_path):
    d = tmp_path / "schema"
    d.mkdir()
    (d / "locations.json").write_text(json.dumps({
        "element": "locations",
        "types": {"LocationComplexType": {
            "doc": "A location.",
            "attributes": [{"name": "id", "type": "string", "use": "required"}],
            "fields": [],
        }},
    }))
    return d


@pytest.fixture
def client():
    c = MagicMock()
    c.messages.stream.side_effect = lambda *a, **k: FakeStream(["ok"], message())
    return c


def make(client, schema_dir, corpus="THE CORPUS", **kw):
    return Agent(corpus=corpus, schema_dir=schema_dir, client=client, **kw)


def drain(agent):
    return "".join(agent.run([{"role": "user", "content": "q"}]))


def calls(client):
    return client.messages.stream.call_args_list


def test_default_is_sonnet_with_effort(client, schema_dir):
    drain(make(client, schema_dir))
    kw = calls(client)[0].kwargs
    assert kw["model"] == "claude-sonnet-5" == config.MODEL
    assert kw["output_config"] == {"effort": config.EFFORT}


def test_explicit_sonnet_matches_default(client, schema_dir):
    drain(make(client, schema_dir, model_key="sonnet"))
    kw = calls(client)[0].kwargs
    assert kw["model"] == "claude-sonnet-5"
    assert kw["output_config"] == {"effort": "medium"}


def test_haiku_uses_haiku_id_and_omits_output_config_entirely(client, schema_dir):
    drain(make(client, schema_dir, model_key="haiku"))
    kw = calls(client)[0].kwargs
    assert kw["model"] == "claude-haiku-4-5"
    assert "output_config" not in kw


def test_haiku_keeps_all_other_kwargs(client, schema_dir):
    ag = make(client, schema_dir, model_key="haiku")
    drain(ag)
    kw = calls(client)[0].kwargs
    assert set(kw) == {"model", "max_tokens", "system", "tools", "messages"}
    assert kw["max_tokens"] == config.MAX_TOKENS
    assert kw["system"] == ag.system_blocks()
    assert kw["tools"] == ag.tools()


def test_sonnet_kwargs_set_is_unchanged(client, schema_dir):
    drain(make(client, schema_dir))
    assert set(calls(client)[0].kwargs) == {
        "model", "max_tokens", "output_config", "system", "tools", "messages"}


@pytest.mark.parametrize("key,model_id,has_effort", [
    ("sonnet", "claude-sonnet-5", True), ("haiku", "claude-haiku-4-5", False)])
def test_model_and_effort_on_every_round_of_tool_loop(client, schema_dir, key, model_id, has_effort):
    rounds = iter([
        message("tool_use", [tool_use_block("a")]),
        message("tool_use", [tool_use_block("b")]),
        message("end_turn"),
    ])
    client.messages.stream.side_effect = lambda *a, **k: FakeStream(["x"], next(rounds))
    drain(make(client, schema_dir, model_key=key))
    cs = calls(client)
    assert len(cs) == 3
    for c in cs:
        assert c.kwargs["model"] == model_id
        assert ("output_config" in c.kwargs) is has_effort
        if has_effort:
            assert c.kwargs["output_config"] == {"effort": config.EFFORT}


def test_supports_effort_flag_drives_behaviour_not_the_key_name(client, schema_dir, monkeypatch):
    models = {k: dict(v) for k, v in config.MODELS.items()}
    models["haiku"]["supports_effort"] = True
    monkeypatch.setattr(config, "MODELS", models)
    drain(make(client, schema_dir, model_key="haiku"))
    assert calls(client)[0].kwargs["output_config"] == {"effort": config.EFFORT}


@pytest.mark.parametrize("bad", ["opus", "", "SONNET", "claude-haiku-4-5", None])
def test_unknown_model_key_raises_at_construction(client, schema_dir, bad):
    with pytest.raises((KeyError, ValueError)):
        make(client, schema_dir, model_key=bad)
    assert calls(client) == []


def test_estimated_cost_haiku_less_than_sonnet(client, schema_dir, tmp_path):
    budget = DailyBudget(tmp_path / "b.json", 100.0)
    corpus = "x" * 200_000
    s = make(client, schema_dir, corpus=corpus, model_key="sonnet").estimated_cost(budget)
    h = make(client, schema_dir, corpus=corpus, model_key="haiku").estimated_cost(budget)
    assert 0 < h < s


def test_estimated_cost_exact_worst_case_per_model(client, schema_dir, tmp_path):
    budget = DailyBudget(tmp_path / "b.json", 100.0)
    corpus = "x" * 200_000
    toks = len(corpus) // 2
    h = make(client, schema_dir, corpus=corpus, model_key="haiku").estimated_cost(budget)
    s = make(client, schema_dir, corpus=corpus).estimated_cost(budget)
    assert h == pytest.approx(config.MAX_TOKENS * 5e-6 + toks * 2e-6)
    assert s == pytest.approx(config.MAX_TOKENS * 15e-6 + toks * 6e-6)

"""Spec tests for the admin model endpoints, audit logging, app wiring, and the
live effect of the selected model on POST /api/chat (model id + spend rate)."""
import json
import logging
import os
import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")

from chat import config
from chat.app import create_app
from chat.identity import storage_key
from chat.model_choice import ModelStore

SECRET = os.environ["JWT_SECRET"]
ORIGIN = "https://df-docs.streamflows.org"
URL = "/api/chat/admin/model"
LIMITS_URL = "/api/chat/admin/limits"
EMAIL = "carol.admin@example.com"
EMAIL_PARTS = ("carol.admin", "example.com")
MODELS_LIST = [{"key": "sonnet", "label": "Sonnet"}, {"key": "haiku", "label": "Haiku"}]


def make_token(groups=("admin",), sub=EMAIL, exp_offset=3600):
    claims = {"exp": int(time.time()) + exp_offset}
    if groups is not None:
        claims["groups"] = list(groups) if isinstance(groups, (list, tuple)) else groups
    if sub is not None:
        claims["sub"] = sub
    return jwt.encode(claims, SECRET, algorithm="HS256")


def login(client, **kw):
    client.set_cookie("streamflows_auth", make_token(**kw))


def put(client, body, origin=ORIGIN, raw=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if origin:
        headers["Origin"] = origin
    data = raw if raw is not None else json.dumps(body)
    return client.put(URL, data=data, headers=headers)


def audit_records(caplog):
    return [r for r in caplog.records if r.name == "chat.audit"]


def record_text(r):
    return r.getMessage() + " " + json.dumps(r.__dict__, default=str)


def all_log_text(caplog):
    return caplog.text + " " + " ".join(record_text(r) for r in caplog.records)


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


BIG_USAGE = dict(input_tokens=100_000, output_tokens=10_000,
                 cache_creation_input_tokens=50_000, cache_read_input_tokens=200_000)


def done_message(**usage):
    u = dict(input_tokens=10, output_tokens=5,
             cache_creation_input_tokens=0, cache_read_input_tokens=0)
    u.update(usage)
    return SimpleNamespace(stop_reason="end_turn", content=[], usage=SimpleNamespace(**u))


@pytest.fixture
def anthropic():
    client = MagicMock()
    client.messages.stream.side_effect = (
        lambda *a, **k: FakeStream(["Hi there"], done_message())
    )
    return client


@pytest.fixture
def app(tmp_path, anthropic):
    return create_app({
        "ANTHROPIC_CLIENT": anthropic,
        "CORPUS": "TEST CORPUS",
        "STATE_DIR": tmp_path,
        "DAILY_BUDGET_USD": 50.00,
    })


@pytest.fixture
def client(app):
    return app.test_client()


def chat(client, groups=("admin", "streamflow")):
    login(client, groups=groups)
    resp = client.post(
        "/api/chat",
        data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}),
        headers={"Content-Type": "application/json", "Origin": ORIGIN},
    )
    if resp.status_code == 200:
        resp.get_data()
    return resp


def spent(tmp_path):
    return json.loads((tmp_path / "budget.json").read_text())["spent_usd"]


# ================================================================ app wiring

def test_app_wires_model_store_at_state_dir_defaulting_to_sonnet(app, tmp_path):
    store = app.config["MODEL_STORE"]
    assert isinstance(store, ModelStore)
    assert store.current() == config.DEFAULT_MODEL == "sonnet"
    store.save("haiku")
    assert json.loads((tmp_path / "model.json").read_text())["model"] == "haiku"


def test_app_honours_existing_model_file_at_startup(tmp_path, anthropic):
    (tmp_path / "model.json").write_text(json.dumps({"model": "haiku"}))
    app = create_app({"ANTHROPIC_CLIENT": anthropic, "CORPUS": "X",
                      "STATE_DIR": tmp_path, "DAILY_BUDGET_USD": 2.0})
    assert app.config["MODEL_STORE"].current() == "haiku"


# ======================================================================= GET

def test_get_requires_auth(client):
    assert client.get(URL).status_code == 401


def test_get_garbage_cookie_is_401(client):
    client.set_cookie("streamflows_auth", "not.a.jwt")
    assert client.get(URL).status_code == 401


def test_get_expired_cookie_is_401(client):
    login(client, exp_offset=-10)
    assert client.get(URL).status_code == 401


def test_get_rejects_streamflow_only(client):
    login(client, groups=["streamflow"])
    resp = client.get(URL)
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "not_authorized"}


@pytest.mark.parametrize("groups", [[], ["administrator"], ["Admin"], "admin", None,
                                    {"admin": True}, [None, 1, ["admin"]]])
def test_get_claim_shape_cannot_grant_admin(client, groups):
    login(client, groups=groups)
    assert client.get(URL).status_code == 403


def test_get_returns_default_and_ordered_choices(client):
    login(client)
    resp = client.get(URL)
    assert resp.status_code == 200
    assert resp.get_json() == {"model": "sonnet", "models": MODELS_LIST}


def test_get_never_exposes_raw_ids_or_rates(client):
    login(client)
    text = client.get(URL).get_data(as_text=True)
    assert "claude-" not in text
    assert "rate" not in text
    assert "supports_effort" not in text


def test_get_reflects_manual_file_edit_without_restart(client, tmp_path):
    login(client)
    (tmp_path / "model.json").write_text(json.dumps({"model": "haiku"}))
    assert client.get(URL).get_json()["model"] == "haiku"


def test_get_with_corrupt_file_reports_default(client, tmp_path):
    login(client)
    (tmp_path / "model.json").write_text("{{{")
    resp = client.get(URL)
    assert resp.status_code == 200
    assert resp.get_json()["model"] == "sonnet"


def test_get_method_cannot_mutate(client, tmp_path):
    login(client)
    client.get(URL + "?model=haiku")
    assert not (tmp_path / "model.json").exists()


# ======================================================================= PUT

def test_put_requires_auth(client, tmp_path):
    assert put(client, {"model": "haiku"}).status_code == 401
    assert not (tmp_path / "model.json").exists()


def test_put_rejects_streamflow_only_and_saves_nothing(client, tmp_path):
    login(client, groups=["streamflow"])
    resp = put(client, {"model": "haiku"})
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "not_authorized"}
    assert not (tmp_path / "model.json").exists()


@pytest.mark.parametrize("origin", [None, "", "https://evil.example",
                                    ORIGIN + ".evil.example", "http://df-docs.streamflows.org"])
def test_put_bad_or_missing_origin_is_403_and_saves_nothing(client, tmp_path, origin):
    login(client)
    resp = put(client, {"model": "haiku"}, origin=origin)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "bad_origin"
    assert not (tmp_path / "model.json").exists()


def test_put_success_returns_get_shape(client, tmp_path):
    login(client)
    resp = put(client, {"model": "haiku"})
    assert resp.status_code == 200
    assert resp.get_json() == {"model": "haiku", "models": MODELS_LIST}
    assert json.loads((tmp_path / "model.json").read_text())["model"] == "haiku"


def test_put_persists_and_get_agrees_and_can_switch_back(client):
    login(client)
    put(client, {"model": "haiku"})
    assert client.get(URL).get_json()["model"] == "haiku"
    assert put(client, {"model": "sonnet"}).get_json()["model"] == "sonnet"
    assert client.get(URL).get_json()["model"] == "sonnet"


def test_put_ignores_extra_keys(client, tmp_path):
    login(client)
    resp = put(client, {"model": "haiku", "id": "claude-opus-9", "rate_input": 0, "admin": True})
    assert resp.status_code == 200
    on_disk = json.loads((tmp_path / "model.json").read_text())
    assert on_disk["model"] == "haiku"
    assert "claude-opus-9" not in json.dumps(on_disk)
    assert "rate_input" not in on_disk


INVALID_BODIES = [
    {}, {"model": None}, {"model": 1}, {"model": True}, {"model": ""},
    {"model": "HAIKU"}, {"model": "Sonnet"}, {"model": "haiku "},
    {"model": "claude-haiku-4-5"}, {"model": "claude-sonnet-5"},
    {"model": "opus"}, {"model": ["haiku"]}, {"model": {"key": "haiku"}},
    {"models": "haiku"}, {"key": "haiku"},
    [], ["haiku"], "haiku", None, 42,
]


@pytest.mark.parametrize("body", INVALID_BODIES, ids=lambda b: json.dumps(b))
def test_put_invalid_body_is_400_and_saves_nothing(client, tmp_path, body):
    login(client)
    resp = put(client, body)
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "invalid_model"
    assert isinstance(data["message"], str) and data["message"]
    assert not (tmp_path / "model.json").exists()


@pytest.mark.parametrize("raw", ["not json at all", "{", ""])
def test_put_garbage_body_is_400(client, tmp_path, raw):
    login(client)
    resp = put(client, None, raw=raw)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_model"
    assert not (tmp_path / "model.json").exists()


def test_put_non_json_content_type_is_400(client, tmp_path):
    login(client)
    resp = put(client, None, raw="model=haiku",
               content_type="application/x-www-form-urlencoded")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_model"
    assert not (tmp_path / "model.json").exists()


def test_rejected_put_leaves_existing_choice_byte_identical(client, tmp_path):
    login(client)
    put(client, {"model": "haiku"})
    before = (tmp_path / "model.json").read_bytes()
    assert put(client, {"model": "claude-haiku-4-5"}).status_code == 400
    assert put(client, {"model": "nope"}).status_code == 400
    assert (tmp_path / "model.json").read_bytes() == before
    assert client.get(URL).get_json()["model"] == "haiku"


def test_limits_endpoint_responses_are_unchanged(client):
    login(client)
    put(client, {"model": "haiku"})
    data = client.get(LIMITS_URL).get_json()
    assert set(data) == {"limits", "bounds", "spent_usd", "remaining_usd"}


# ========================================================== audit logging

def test_successful_put_logs_one_info_with_old_new_actor_no_email(client, caplog):
    client.application.config["MODEL_STORE"].save("haiku")
    login(client)
    with caplog.at_level(logging.DEBUG):
        resp = put(client, {"model": "sonnet"})
    assert resp.status_code == 200
    infos = [r for r in audit_records(caplog) if r.levelno == logging.INFO]
    assert len(infos) == 1
    text = record_text(infos[0])
    for expected in ("haiku", "sonnet", storage_key(EMAIL)):
        assert expected in text
    msg = infos[0].getMessage()
    assert msg.index("haiku") < msg.index("sonnet")  # old before new
    for part in EMAIL_PARTS:
        assert part not in all_log_text(caplog)


def test_each_successful_put_logs_exactly_once(client, caplog):
    login(client)
    with caplog.at_level(logging.DEBUG):
        put(client, {"model": "haiku"})
        put(client, {"model": "sonnet"})
    infos = [r for r in audit_records(caplog) if r.levelno == logging.INFO]
    assert len(infos) == 2


def test_rejected_put_logs_warning_with_actor_no_info_no_email(client, caplog):
    login(client)
    with caplog.at_level(logging.DEBUG):
        resp = put(client, {"model": "claude-haiku-4-5"})
    assert resp.status_code == 400
    warns = [r for r in audit_records(caplog) if r.levelno == logging.WARNING]
    assert warns
    assert storage_key(EMAIL) in " ".join(record_text(r) for r in warns)
    assert not [r for r in audit_records(caplog) if r.levelno == logging.INFO]
    for part in EMAIL_PARTS:
        assert part not in all_log_text(caplog)


def test_non_json_put_logs_warning_without_email(client, caplog):
    login(client)
    with caplog.at_level(logging.DEBUG):
        assert put(client, None, raw="garbage").status_code == 400
    assert [r for r in audit_records(caplog) if r.levelno == logging.WARNING]
    for part in EMAIL_PARTS:
        assert part not in all_log_text(caplog)


def test_bad_origin_put_logs_warning_with_actor_no_email(client, caplog):
    login(client)
    with caplog.at_level(logging.DEBUG):
        assert put(client, {"model": "haiku"}, origin="https://evil.example").status_code == 403
    warns = [r for r in audit_records(caplog) if r.levelno == logging.WARNING]
    assert warns
    assert storage_key(EMAIL) in " ".join(record_text(r) for r in warns)
    assert not [r for r in audit_records(caplog) if r.levelno == logging.INFO]
    for part in EMAIL_PARTS:
        assert part not in all_log_text(caplog)


def test_non_admin_attempt_logs_warning_with_actor_no_email(client, caplog):
    login(client, groups=["streamflow"])
    with caplog.at_level(logging.DEBUG):
        assert put(client, {"model": "haiku"}).status_code == 403
    warns = [r for r in audit_records(caplog) if r.levelno == logging.WARNING]
    assert warns
    assert storage_key(EMAIL) in " ".join(record_text(r) for r in warns)
    for part in EMAIL_PARTS:
        assert part not in all_log_text(caplog)


def test_get_never_logs_email(client, caplog):
    login(client)
    with caplog.at_level(logging.DEBUG):
        client.get(URL)
    for part in EMAIL_PARTS:
        assert part not in all_log_text(caplog)


# =============================================== live effect on /api/chat

def model_ids(anthropic):
    return [c.kwargs["model"] for c in anthropic.messages.stream.call_args_list]


def test_chat_defaults_to_sonnet_with_effort(client, anthropic):
    assert chat(client).status_code == 200
    kw = anthropic.messages.stream.call_args.kwargs
    assert kw["model"] == "claude-sonnet-5"
    assert kw["output_config"] == {"effort": config.EFFORT}


def test_chat_uses_haiku_without_effort_after_admin_switch(client, anthropic):
    login(client)
    assert put(client, {"model": "haiku"}).status_code == 200
    assert chat(client).status_code == 200
    kw = anthropic.messages.stream.call_args.kwargs
    assert kw["model"] == "claude-haiku-4-5"
    assert "output_config" not in kw


def test_switching_between_requests_takes_effect_without_restart(client, anthropic):
    store = client.application.config["MODEL_STORE"]
    chat(client)
    store.save("haiku")
    chat(client)
    store.save("sonnet")
    chat(client)
    assert model_ids(anthropic) == ["claude-sonnet-5", "claude-haiku-4-5", "claude-sonnet-5"]


def test_manual_file_edit_switches_model_on_next_chat(client, anthropic, tmp_path):
    (tmp_path / "model.json").write_text(json.dumps({"model": "haiku"}))
    chat(client)
    assert model_ids(anthropic) == ["claude-haiku-4-5"]


def test_corrupt_model_file_falls_back_to_sonnet_in_chat(client, anthropic, tmp_path):
    (tmp_path / "model.json").write_text("{{{")
    assert chat(client).status_code == 200
    assert model_ids(anthropic) == ["claude-sonnet-5"]


def _expected_cost(i, o, cw, cr, rate_in, rate_out):
    return i * rate_in + o * rate_out + cw * rate_in * 2 + cr * rate_in * 0.1


def test_sonnet_turn_is_charged_at_sonnet_rates(client, anthropic, tmp_path):
    anthropic.messages.stream.side_effect = (
        lambda *a, **k: FakeStream(["x"], done_message(**BIG_USAGE)))
    assert chat(client).status_code == 200
    assert spent(tmp_path) == pytest.approx(
        _expected_cost(100_000, 10_000, 50_000, 200_000, 3e-6, 15e-6))


def test_haiku_turn_is_charged_at_haiku_rates(client, anthropic, tmp_path):
    anthropic.messages.stream.side_effect = (
        lambda *a, **k: FakeStream(["x"], done_message(**BIG_USAGE)))
    client.application.config["MODEL_STORE"].save("haiku")
    assert chat(client).status_code == 200
    assert spent(tmp_path) == pytest.approx(
        _expected_cost(100_000, 10_000, 50_000, 200_000, 1e-6, 5e-6))


def test_identical_usage_costs_less_on_haiku_than_sonnet(client, anthropic, tmp_path):
    anthropic.messages.stream.side_effect = (
        lambda *a, **k: FakeStream(["x"], done_message(**BIG_USAGE)))
    store = client.application.config["MODEL_STORE"]
    store.save("sonnet")
    chat(client)
    sonnet_cost = spent(tmp_path)
    store.save("haiku")
    chat(client)
    haiku_cost = spent(tmp_path) - sonnet_cost
    assert haiku_cost == pytest.approx(sonnet_cost / 3)  # every rate is exactly 1/3


def test_model_read_once_per_request_for_agent_and_settle(client, anthropic, tmp_path):
    """Switch the store while the stream is in flight: the request keeps the
    model it started with, for both the API call and the pricing."""
    store = client.application.config["MODEL_STORE"]
    store.save("haiku")

    def stream(*a, **k):
        store.save("sonnet")  # admin flips mid-request
        return FakeStream(["x"], done_message(**BIG_USAGE))

    anthropic.messages.stream.side_effect = stream
    assert chat(client).status_code == 200
    assert model_ids(anthropic) == ["claude-haiku-4-5"]
    assert spent(tmp_path) == pytest.approx(
        _expected_cost(100_000, 10_000, 50_000, 200_000, 1e-6, 5e-6))


def test_haiku_reservation_is_smaller_so_it_fits_where_sonnet_would_not(client, anthropic, tmp_path):
    """The pre-dispatch reservation is priced per model. With a tiny remaining
    budget, a Haiku worst case fits while a Sonnet worst case is refused."""
    (tmp_path / "budget.json").write_text(json.dumps(
        {"date": date.today().isoformat(), "spent_usd": 0.0}))
    corpus_tokens = len("TEST CORPUS") // 2
    sonnet_est = config.MAX_TOKENS * 15e-6 + corpus_tokens * 6e-6
    haiku_est = config.MAX_TOKENS * 5e-6 + corpus_tokens * 2e-6
    limit = (sonnet_est + haiku_est) / 2
    client.application.config["LIMITS"].save(limit, 20)
    store = client.application.config["MODEL_STORE"]

    store.save("sonnet")
    refused = chat(client)
    assert refused.get_data(as_text=True)  # drain
    assert model_ids(anthropic) == []  # sonnet worst case did not fit

    store.save("haiku")
    assert chat(client).status_code == 200
    assert model_ids(anthropic) == ["claude-haiku-4-5"]

import json
import os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")

from chat.app import create_app

SECRET = os.environ["JWT_SECRET"]
ORIGIN = "https://df-docs.streamflows.org"


def token(groups=("streamflow",), sub="alice"):
    return jwt.encode(
        {"sub": sub, "groups": list(groups), "exp": int(time.time()) + 3600},
        SECRET,
        algorithm="HS256",
    )


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


def done_message(out_tokens=5):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=out_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


@pytest.fixture
def anthropic():
    client = MagicMock()
    client.messages.stream.return_value = FakeStream(["Hi there"], done_message())
    return client


@pytest.fixture
def app(tmp_path, anthropic):
    return create_app({
        "ANTHROPIC_CLIENT": anthropic,
        "CORPUS": "TEST CORPUS",
        "STATE_DIR": tmp_path,
        "DAILY_BUDGET_USD": 2.00,
    })


@pytest.fixture
def client(app):
    return app.test_client()


def post(client, body=None, origin=ORIGIN, authed=True):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    if authed:
        client.set_cookie("streamflows_auth", token())
    return client.post(
        "/api/chat",
        data=json.dumps(body or {"messages": [{"role": "user", "content": "hi"}]}),
        headers=headers,
    )


def test_health_needs_no_auth(client):
    assert client.get("/health").status_code == 200


def test_status_rejects_anonymous(client):
    assert client.get("/api/chat/status").status_code == 401


def test_status_accepts_a_group_member(client):
    client.set_cookie("streamflows_auth", token())
    resp = client.get("/api/chat/status")
    assert resp.status_code == 200
    assert resp.get_json()["authenticated"] is True


def test_chat_rejects_anonymous(client):
    resp = post(client, authed=False)
    assert resp.status_code == 401


def test_chat_rejects_a_non_member(client):
    client.set_cookie("streamflows_auth", token(groups=("someothergroup",)))
    resp = client.post(
        "/api/chat",
        data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}),
        headers={"Content-Type": "application/json", "Origin": ORIGIN},
    )
    assert resp.status_code == 403


def test_chat_rejects_a_foreign_origin(client):
    resp = post(client, origin="https://evil.example")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "bad_origin"


def test_chat_rejects_a_missing_origin(client):
    resp = post(client, origin=None)
    assert resp.status_code == 403


def test_chat_rejects_malformed_history(client):
    resp = post(client, body={"messages": [{"role": "system", "content": "x"}]})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_history"


def test_chat_streams_an_answer(client):
    resp = post(client)
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    body = resp.get_data(as_text=True)
    assert "Hi there" in body
    assert "event: done" in body


def test_chat_disables_proxy_buffering(client):
    resp = post(client)
    assert resp.headers["X-Accel-Buffering"] == "no"
    assert resp.headers["Cache-Control"] == "no-cache"


def test_rate_limit_returns_429(app, client):
    app.config["RATE_LIMITER"].max_calls = 2
    post(client)
    post(client)
    resp = post(client)
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"


def test_exhausted_budget_returns_429_before_dispatch(app, client, anthropic):
    app.config["BUDGET"].limit = 0.0
    resp = post(client)
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "budget_exhausted"
    anthropic.messages.stream.assert_not_called()


def test_spend_is_recorded_after_a_completed_stream(app, client):
    post(client).get_data()
    assert app.config["BUDGET"].remaining() < 2.00


def test_oversized_request_body_is_rejected_before_parsing(client):
    """Flask leaves MAX_CONTENT_LENGTH unset by default, which means an
    unbounded request body. Werkzeug must reject this with a 413."""
    from chat import config

    client.set_cookie("streamflows_auth", token())
    resp = client.post(
        "/api/chat",
        data="x" * (config.MAX_REQUEST_BYTES + 1024),
        headers={"Content-Type": "application/json", "Origin": ORIGIN},
    )
    assert resp.status_code == 413


def test_startup_fails_loudly_when_jwt_secret_is_missing(tmp_path, anthropic, monkeypatch):
    """decode_token indexes os.environ["JWT_SECRET"] directly, so an unset
    value is a KeyError on every request rather than a service that refuses
    to start."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app({
            "ANTHROPIC_CLIENT": anthropic,
            "CORPUS": "TEST CORPUS",
            "STATE_DIR": tmp_path,
        })


def test_startup_fails_loudly_when_schema_data_is_missing(tmp_path, anthropic):
    """src/data/schema/ is gitignored and regenerated by `npm run gen:schema`.
    An empty directory would give the tool an empty enum, and strict:true
    against an empty enum makes the API reject every request. Fail at startup,
    not at the user's first question."""
    empty = tmp_path / "no-schema"
    empty.mkdir()
    with pytest.raises(RuntimeError, match="gen:schema"):
        create_app({
            "ANTHROPIC_CLIENT": anthropic,
            "CORPUS": "TEST CORPUS",
            "STATE_DIR": tmp_path,
            "SCHEMA_DIR": empty,
        })


def test_status_returns_a_storage_key(client):
    client.set_cookie("streamflows_auth", token())
    resp = client.get("/api/chat/status")
    assert resp.status_code == 200
    assert isinstance(resp.get_json()["storage_key"], str)
    assert len(resp.get_json()["storage_key"]) == 16


def test_two_users_get_different_storage_keys(client):
    def key_for(sub):
        client.set_cookie("streamflows_auth", token(sub=sub))
        return client.get("/api/chat/status").get_json()["storage_key"]

    assert key_for("alice") != key_for("bob")


def test_unauthenticated_status_has_no_storage_key(client):
    """A 401 body must not carry a namespace an anonymous caller could adopt."""
    resp = client.get("/api/chat/status")
    assert resp.status_code == 401
    assert "storage_key" not in resp.get_json()

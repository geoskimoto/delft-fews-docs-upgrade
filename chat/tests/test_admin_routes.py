"""Spec tests for admin-adjustable limits: require_admin, the admin routes,
the is_admin status flag, audit logging, and live effect on /api/chat."""
import json
import logging
import os
import time
from datetime import date

import jwt
import pytest
from flask import Flask, jsonify

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")

from chat.app import create_app
from chat.auth import require_admin
from chat.identity import storage_key
from chat.limits import LimitsStore

from types import SimpleNamespace
from unittest.mock import MagicMock

SECRET = os.environ["JWT_SECRET"]
ORIGIN = "https://df-docs.streamflows.org"
URL = "/api/chat/admin/limits"
EMAIL = "carol.admin@example.com"
EMAIL_PARTS = ("carol.admin", "example.com")


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


def good_body(**over):
    body = {"daily_budget_usd": 7.25, "rate_limit_calls": 33}
    body.update(over)
    return body


def audit_records(caplog):
    return [r for r in caplog.records if r.name == "chat.audit"]


def record_text(r):
    return r.getMessage() + " " + json.dumps(r.__dict__, default=str)


def write_spend(tmp_path, spent):
    (tmp_path / "budget.json").write_text(
        json.dumps({"date": date.today().isoformat(), "spent_usd": spent})
    )


def consume_chat(client, sub_groups=("admin", "streamflow")):
    login(client, groups=sub_groups)
    resp = client.post(
        "/api/chat",
        data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}),
        headers={"Content-Type": "application/json", "Origin": ORIGIN},
    )
    if resp.status_code == 200:
        resp.get_data()
    return resp


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


def done_message():
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[],
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


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
        "DAILY_BUDGET_USD": 2.00,
    })


@pytest.fixture
def client(app):
    return app.test_client()


# ============================================================ require_admin

@pytest.fixture
def guard_client():
    app = Flask(__name__)

    @app.route("/guarded", methods=["GET", "PUT"])
    @require_admin
    def guarded():
        from flask import g
        return jsonify({"ok": True, "user": g.current_user})

    return app.test_client()


def guarded(client, **kw):
    client.set_cookie("streamflows_auth", make_token(**kw))
    return client.get("/guarded")


def test_require_admin_no_cookie_is_401_json(guard_client):
    resp = guard_client.get("/guarded")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_authenticated"
    assert resp.content_type.startswith("application/json")


def test_require_admin_garbage_token_is_401(guard_client):
    guard_client.set_cookie("streamflows_auth", "not.a.jwt")
    resp = guard_client.get("/guarded")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_authenticated"


def test_require_admin_expired_token_is_401_session_expired(guard_client):
    resp = guarded(guard_client, exp_offset=-10)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "session_expired"


def test_require_admin_wrong_secret_is_401(guard_client):
    bad = jwt.encode(
        {"sub": "x", "groups": ["admin"], "exp": int(time.time()) + 60},
        "other-secret", algorithm="HS256",
    )
    guard_client.set_cookie("streamflows_auth", bad)
    assert guard_client.get("/guarded").status_code == 401


def test_require_admin_admin_group_passes(guard_client):
    resp = guarded(guard_client, groups=["admin"])
    assert resp.status_code == 200
    assert resp.get_json()["user"] == EMAIL


def test_require_admin_admin_plus_streamflow_passes(guard_client):
    assert guarded(guard_client, groups=["streamflow", "admin"]).status_code == 200


def test_require_admin_streamflow_only_is_403_not_authorized(guard_client):
    resp = guarded(guard_client, groups=["streamflow"])
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "not_authorized"}


@pytest.mark.parametrize("groups", [
    [], ["other"], ["administrator"], ["Admin"], ["admin-readonly"], ["streamflow-admin"],
    "admin", "administrative", "administrator", "streamflow admin",
    {"admin": True}, 42, None, True,
    [None, 1, ["admin"], {"admin": 1}],
])
def test_require_admin_claim_shape_cannot_grant_admin(guard_client, groups):
    resp = guarded(guard_client, groups=groups)
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "not_authorized"}


def test_require_admin_non_string_elements_are_ignored_not_fatal(guard_client):
    assert guarded(guard_client, groups=[1, None, "admin", {"x": 1}]).status_code == 200


@pytest.mark.parametrize("sub", [None, "", "   ", 42, ["a"]])
def test_require_admin_missing_or_blank_sub_is_401(guard_client, sub):
    claims = {"groups": ["admin"], "exp": int(time.time()) + 60}
    if sub is not None:
        claims["sub"] = sub
    guard_client.set_cookie("streamflows_auth", jwt.encode(claims, SECRET, algorithm="HS256"))
    resp = guard_client.get("/guarded")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_authenticated"


# ======================================================================= GET

def test_get_requires_auth(client):
    assert client.get(URL).status_code == 401


def test_get_rejects_streamflow_only_users(client):
    login(client, groups=["streamflow"])
    resp = client.get(URL)
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "not_authorized"}


def test_get_returns_expected_shape_and_defaults(client, tmp_path):
    write_spend(tmp_path, 0.5)
    login(client)
    resp = client.get(URL)
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data) == {"limits", "bounds", "spent_usd", "remaining_usd"}
    assert data["limits"] == {"daily_budget_usd": 2.0, "rate_limit_calls": 20}
    assert data["bounds"] == {"daily_budget_usd": [0.0, 50.0], "rate_limit_calls": [1, 200]}
    assert data["spent_usd"] == pytest.approx(0.5)
    assert data["remaining_usd"] == pytest.approx(1.5)


def test_get_spent_is_zero_with_no_ledger(client):
    login(client)
    data = client.get(URL).get_json()
    assert data["spent_usd"] == 0
    assert data["remaining_usd"] == pytest.approx(2.0)


def test_get_reflects_manual_file_edit_without_restart(client, tmp_path):
    login(client)
    (tmp_path / "limits.json").write_text(
        json.dumps({"daily_budget_usd": 11.5, "rate_limit_calls": 44})
    )
    data = client.get(URL).get_json()
    assert data["limits"] == {"daily_budget_usd": 11.5, "rate_limit_calls": 44}
    assert data["remaining_usd"] == pytest.approx(11.5)


# ======================================================================= app

def test_app_wires_a_limits_store_at_state_dir(app, tmp_path):
    store = app.config["LIMITS"]
    assert isinstance(store, LimitsStore)
    assert store.current() == {"daily_budget_usd": 2.0, "rate_limit_calls": 20}
    store.save(3.0, 4)
    assert json.loads((tmp_path / "limits.json").read_text())["rate_limit_calls"] == 4


def test_app_honours_an_existing_limits_file_at_startup(tmp_path, anthropic):
    (tmp_path / "limits.json").write_text(
        json.dumps({"daily_budget_usd": 9.0, "rate_limit_calls": 5})
    )
    app = create_app({
        "ANTHROPIC_CLIENT": anthropic, "CORPUS": "X",
        "STATE_DIR": tmp_path, "DAILY_BUDGET_USD": 2.0,
    })
    assert app.config["BUDGET"].limit == 9.0
    assert app.config["RATE_LIMITER"].max_calls == 5


def test_app_budget_and_limiter_are_live_views_of_the_store(app):
    store = app.config["LIMITS"]
    store.save(12.5, 77)
    assert app.config["BUDGET"].limit == 12.5
    assert app.config["RATE_LIMITER"].max_calls == 77


def test_app_defaults_use_override_budget_and_config_calls(tmp_path, anthropic):
    from chat import config
    app = create_app({
        "ANTHROPIC_CLIENT": anthropic, "CORPUS": "X",
        "STATE_DIR": tmp_path, "DAILY_BUDGET_USD": 3.0,
    })
    assert app.config["LIMITS"].current() == {
        "daily_budget_usd": 3.0, "rate_limit_calls": config.RATE_LIMIT_CALLS,
    }


# ======================================================================= PUT

def test_put_requires_auth(client, tmp_path):
    resp = put(client, good_body())
    assert resp.status_code == 401
    assert not (tmp_path / "limits.json").exists()


def test_put_rejects_streamflow_only_and_saves_nothing(client, tmp_path):
    login(client, groups=["streamflow"])
    resp = put(client, good_body())
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "not_authorized"}
    assert not (tmp_path / "limits.json").exists()


@pytest.mark.parametrize("origin", [None, "", "https://evil.example",
                                    ORIGIN + ".evil.example", "http://df-docs.streamflows.org"])
def test_put_bad_or_missing_origin_is_403_and_saves_nothing(client, tmp_path, origin):
    login(client)
    resp = put(client, good_body(), origin=origin)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "bad_origin"
    assert not (tmp_path / "limits.json").exists()


def test_put_success_returns_get_shape_with_new_values(client, tmp_path):
    write_spend(tmp_path, 1.0)
    login(client)
    resp = put(client, good_body())
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data) == {"limits", "bounds", "spent_usd", "remaining_usd"}
    assert data["limits"] == {"daily_budget_usd": 7.25, "rate_limit_calls": 33}
    assert data["bounds"] == {"daily_budget_usd": [0.0, 50.0], "rate_limit_calls": [1, 200]}
    assert data["spent_usd"] == pytest.approx(1.0)
    assert data["remaining_usd"] == pytest.approx(6.25)


def test_put_persists_and_get_then_agrees(client, tmp_path):
    login(client)
    put(client, good_body())
    assert client.get(URL).get_json()["limits"] == {
        "daily_budget_usd": 7.25, "rate_limit_calls": 33,
    }
    on_disk = json.loads((tmp_path / "limits.json").read_text())
    assert on_disk["daily_budget_usd"] == 7.25 and on_disk["rate_limit_calls"] == 33


def test_put_ignores_unknown_extra_keys(client, tmp_path):
    login(client)
    resp = put(client, good_body(window_seconds=1, evil="x", admin=True))
    assert resp.status_code == 200
    on_disk = json.loads((tmp_path / "limits.json").read_text())
    assert "window_seconds" not in on_disk
    assert resp.get_json()["limits"] == {"daily_budget_usd": 7.25, "rate_limit_calls": 33}
    assert client.application.config["RATE_LIMITER"].window == 300


@pytest.mark.parametrize("budget,calls", [(0, 1), (0.0, 1), (50, 200), (50.0, 200), (3, 5)])
def test_put_accepts_inclusive_bounds(client, budget, calls):
    login(client)
    resp = put(client, {"daily_budget_usd": budget, "rate_limit_calls": calls})
    assert resp.status_code == 200
    assert resp.get_json()["limits"] == {"daily_budget_usd": budget, "rate_limit_calls": calls}


INVALID_BODIES = [
    {},
    {"daily_budget_usd": 5},
    {"rate_limit_calls": 5},
    {"daily_budget_usd": "5", "rate_limit_calls": 5},
    {"daily_budget_usd": 5, "rate_limit_calls": "5"},
    {"daily_budget_usd": None, "rate_limit_calls": 5},
    {"daily_budget_usd": 5, "rate_limit_calls": None},
    {"daily_budget_usd": True, "rate_limit_calls": 5},
    {"daily_budget_usd": 5, "rate_limit_calls": True},
    {"daily_budget_usd": -0.01, "rate_limit_calls": 5},
    {"daily_budget_usd": 50.01, "rate_limit_calls": 5},
    {"daily_budget_usd": 51, "rate_limit_calls": 5},
    {"daily_budget_usd": 5, "rate_limit_calls": 0},
    {"daily_budget_usd": 5, "rate_limit_calls": 201},
    {"daily_budget_usd": 5, "rate_limit_calls": 5.5},
    {"daily_budget_usd": 5, "rate_limit_calls": 5.0},
    {"daily_budget_usd": [5], "rate_limit_calls": 5},
    {"daily_budget_usd": 5, "rate_limit_calls": {"a": 1}},
    [],
    [1, 2],
    "hello",
    None,
    42,
]


@pytest.mark.parametrize("body", INVALID_BODIES, ids=lambda b: json.dumps(b))
def test_put_invalid_body_is_400_and_saves_nothing(client, tmp_path, body):
    login(client)
    resp = put(client, body)
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "invalid_limits"
    assert isinstance(data["message"], str) and data["message"]
    assert not (tmp_path / "limits.json").exists()


@pytest.mark.parametrize("raw", [
    '{"daily_budget_usd": NaN, "rate_limit_calls": 5}',
    '{"daily_budget_usd": Infinity, "rate_limit_calls": 5}',
    '{"daily_budget_usd": -Infinity, "rate_limit_calls": 5}',
    '{"daily_budget_usd": 5, "rate_limit_calls": NaN}',
    "not json at all", "{", "",
])
def test_put_nan_inf_and_garbage_bodies_are_400(client, tmp_path, raw):
    login(client)
    resp = put(client, None, raw=raw)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_limits"
    assert not (tmp_path / "limits.json").exists()


def test_put_non_json_content_type_is_400(client, tmp_path):
    login(client)
    resp = put(client, None, raw="daily_budget_usd=5&rate_limit_calls=5",
               content_type="application/x-www-form-urlencoded")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_limits"
    assert not (tmp_path / "limits.json").exists()


def test_rejected_put_leaves_existing_limits_untouched(client, tmp_path):
    login(client)
    assert put(client, good_body()).status_code == 200
    before = (tmp_path / "limits.json").read_bytes()
    assert put(client, good_body(rate_limit_calls=0)).status_code == 400
    assert put(client, good_body(daily_budget_usd=99)).status_code == 400
    assert (tmp_path / "limits.json").read_bytes() == before
    assert client.get(URL).get_json()["limits"] == {
        "daily_budget_usd": 7.25, "rate_limit_calls": 33,
    }


def test_put_with_one_valid_and_one_invalid_field_changes_neither(client, tmp_path):
    login(client)
    put(client, good_body())
    resp = put(client, {"daily_budget_usd": 1.0, "rate_limit_calls": 9999})
    assert resp.status_code == 400
    assert client.get(URL).get_json()["limits"] == {
        "daily_budget_usd": 7.25, "rate_limit_calls": 33,
    }


def test_get_method_cannot_mutate(client, tmp_path):
    login(client)
    client.get(URL + "?daily_budget_usd=9&rate_limit_calls=9")
    assert not (tmp_path / "limits.json").exists()


# ============================================================= status flag

def test_status_is_admin_true_for_admin_group(client):
    login(client, groups=["admin"])
    assert client.get("/api/chat/status").get_json()["is_admin"] is True


def test_status_is_admin_true_for_both_groups(client):
    login(client, groups=["streamflow", "admin"])
    assert client.get("/api/chat/status").get_json()["is_admin"] is True


def test_status_is_admin_false_for_streamflow_only(client):
    login(client, groups=["streamflow"])
    data = client.get("/api/chat/status").get_json()
    assert data["is_admin"] is False


@pytest.mark.parametrize("groups", [["administrator", "streamflow"], ["Admin", "streamflow"]])
def test_status_is_admin_false_for_lookalike_claims(client, groups):
    login(client, groups=groups)
    assert client.get("/api/chat/status").get_json()["is_admin"] is False


@pytest.mark.parametrize("scalar", ["admin", "administrative"])
def test_status_scalar_group_claim_is_not_admin(client, scalar):
    login(client, groups=scalar)
    resp = client.get("/api/chat/status")
    # Scalar claim grants nothing at all (existing decorator behaviour).
    assert resp.status_code == 403


def test_status_keeps_existing_keys(client):
    login(client, groups=["admin"])
    data = client.get("/api/chat/status").get_json()
    assert data["authenticated"] is True
    assert data["available"] is True
    assert data["storage_key"] == storage_key(EMAIL)
    assert isinstance(data["is_admin"], bool)


# ========================================================== audit logging

def test_successful_put_logs_one_info_with_old_new_and_actor_no_email(client, caplog):
    client.application.config["LIMITS"].save(3.5, 17)
    login(client)
    with caplog.at_level(logging.DEBUG):
        resp = put(client, good_body())
    assert resp.status_code == 200
    recs = audit_records(caplog)
    infos = [r for r in recs if r.levelno == logging.INFO]
    assert len(infos) == 1
    text = record_text(infos[0])
    for expected in ("3.5", "17", "7.25", "33", storage_key(EMAIL)):
        assert expected in text
    for part in EMAIL_PARTS:
        assert part not in caplog.text
        assert part not in " ".join(record_text(r) for r in caplog.records)


def test_successful_put_logs_no_email_anywhere(client, caplog):
    login(client)
    with caplog.at_level(logging.DEBUG):
        put(client, good_body())
    for part in EMAIL_PARTS:
        assert part not in caplog.text


def test_each_successful_put_logs_exactly_once(client, caplog):
    login(client)
    with caplog.at_level(logging.DEBUG):
        put(client, good_body())
        put(client, good_body(rate_limit_calls=40))
    infos = [r for r in audit_records(caplog) if r.levelno == logging.INFO]
    assert len(infos) == 2


def test_rejected_put_logs_warning_with_actor_and_no_email(client, caplog):
    login(client)
    with caplog.at_level(logging.DEBUG):
        resp = put(client, good_body(daily_budget_usd=999))
    assert resp.status_code == 400
    warns = [r for r in audit_records(caplog) if r.levelno == logging.WARNING]
    assert len(warns) >= 1
    assert storage_key(EMAIL) in " ".join(record_text(r) for r in warns)
    assert not [r for r in audit_records(caplog) if r.levelno == logging.INFO]
    for part in EMAIL_PARTS:
        assert part not in caplog.text


def test_non_json_put_logs_warning_without_email(client, caplog):
    login(client)
    with caplog.at_level(logging.DEBUG):
        resp = put(client, None, raw="garbage")
    assert resp.status_code == 400
    warns = [r for r in audit_records(caplog) if r.levelno == logging.WARNING]
    assert warns
    for part in EMAIL_PARTS:
        assert part not in caplog.text


def test_non_admin_attempt_logs_warning_with_actor_and_no_email(client, caplog):
    login(client, groups=["streamflow"], sub=EMAIL)
    with caplog.at_level(logging.DEBUG):
        resp = put(client, good_body())
    assert resp.status_code == 403
    warns = [r for r in audit_records(caplog) if r.levelno == logging.WARNING]
    assert len(warns) >= 1
    assert storage_key(EMAIL) in " ".join(record_text(r) for r in warns)
    for part in EMAIL_PARTS:
        assert part not in caplog.text
        assert part not in " ".join(record_text(r) for r in caplog.records)


def test_non_admin_get_attempt_also_logs_warning(client, caplog):
    login(client, groups=["streamflow"])
    with caplog.at_level(logging.DEBUG):
        assert client.get(URL).status_code == 403
    warns = [r for r in audit_records(caplog) if r.levelno == logging.WARNING]
    assert warns
    assert storage_key(EMAIL) in " ".join(record_text(r) for r in warns)
    for part in EMAIL_PARTS:
        assert part not in caplog.text


# =============================================== live effect on /api/chat

def test_raising_budget_unblocks_a_budget_exhausted_chat(client, tmp_path):
    write_spend(tmp_path, 2.0)
    blocked = consume_chat(client)
    assert blocked.status_code == 429
    assert blocked.get_json()["error"] == "budget_exhausted"

    login(client)
    assert put(client, {"daily_budget_usd": 10.0, "rate_limit_calls": 20}).status_code == 200

    ok = consume_chat(client)
    assert ok.status_code == 200


def test_lowering_budget_below_spend_blocks_the_next_chat(client, tmp_path):
    write_spend(tmp_path, 1.5)
    assert consume_chat(client).status_code == 200
    write_spend(tmp_path, 1.5)

    login(client)
    assert put(client, {"daily_budget_usd": 1.0, "rate_limit_calls": 20}).status_code == 200

    resp = consume_chat(client)
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "budget_exhausted"


def test_status_available_flips_with_the_budget(client, tmp_path):
    write_spend(tmp_path, 3.0)
    login(client)
    assert client.get("/api/chat/status").get_json()["available"] is False
    put(client, {"daily_budget_usd": 10.0, "rate_limit_calls": 20})
    assert client.get("/api/chat/status").get_json()["available"] is True


def test_lowering_rate_limit_applies_to_the_next_chat(client):
    assert consume_chat(client).status_code == 200
    assert consume_chat(client).status_code == 200

    login(client)
    assert put(client, {"daily_budget_usd": 2.0, "rate_limit_calls": 1}).status_code == 200

    resp = consume_chat(client)
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"


def test_raising_rate_limit_applies_to_the_next_chat(client):
    client.application.config["LIMITS"].save(2.0, 1)
    assert consume_chat(client).status_code == 200
    blocked = consume_chat(client)
    assert blocked.status_code == 429
    assert blocked.get_json()["error"] == "rate_limited"

    login(client)
    assert put(client, {"daily_budget_usd": 2.0, "rate_limit_calls": 5}).status_code == 200

    assert consume_chat(client).status_code == 200


def test_rejected_put_does_not_change_live_behaviour(client):
    client.application.config["LIMITS"].save(2.0, 1)
    assert consume_chat(client).status_code == 200
    login(client)
    assert put(client, {"daily_budget_usd": 2.0, "rate_limit_calls": 500}).status_code == 400
    resp = consume_chat(client)
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"

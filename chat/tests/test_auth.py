import os
import time

import jwt
import pytest
from flask import Flask, jsonify

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")

from chat.auth import require_streamflows_user

SECRET = os.environ["JWT_SECRET"]


def make_token(groups, exp_offset=3600, sub="alice"):
    return jwt.encode(
        {"sub": sub, "groups": groups, "exp": int(time.time()) + exp_offset},
        SECRET,
        algorithm="HS256",
    )


@pytest.fixture
def client():
    app = Flask(__name__)

    @app.route("/api/chat", methods=["POST"])
    @require_streamflows_user
    def protected():
        return jsonify({"ok": True})

    return app.test_client()


def test_no_cookie_is_rejected(client):
    resp = client.post("/api/chat")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_authenticated"


def test_no_cookie_returns_json_not_a_redirect(client):
    """Regression test for the protect_app() /api/ exemption.

    protect_app() skips every path starting with /api/ and redirects rather than
    returning a status code. If a refactor swaps this decorator for protect_app,
    this endpoint silently becomes public. This test is what catches that.
    """
    resp = client.post("/api/chat")
    assert resp.status_code == 401
    assert resp.status_code not in (301, 302, 303, 307, 308)
    assert resp.content_type.startswith("application/json")


def test_garbage_token_is_rejected(client):
    client.set_cookie("streamflows_auth", "not.a.jwt")
    resp = client.post("/api/chat")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_authenticated"


def test_expired_token_is_rejected_with_its_own_code(client):
    client.set_cookie("streamflows_auth", make_token(["streamflow"], exp_offset=-10))
    resp = client.post("/api/chat")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "session_expired"


def test_token_signed_with_wrong_secret_is_rejected(client):
    bad = jwt.encode(
        {"sub": "mallory", "groups": ["streamflow"], "exp": int(time.time()) + 3600},
        "a-different-secret",
        algorithm="HS256",
    )
    client.set_cookie("streamflows_auth", bad)
    resp = client.post("/api/chat")
    assert resp.status_code == 401


def test_valid_token_without_the_group_is_forbidden(client):
    client.set_cookie("streamflows_auth", make_token(["someothergroup"]))
    resp = client.post("/api/chat")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "not_authorized"


def test_streamflow_group_is_allowed(client):
    client.set_cookie("streamflows_auth", make_token(["streamflow"]))
    resp = client.post("/api/chat")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_admin_group_is_allowed(client):
    client.set_cookie("streamflows_auth", make_token(["admin"]))
    resp = client.post("/api/chat")
    assert resp.status_code == 200


def test_missing_groups_claim_is_forbidden(client):
    token = jwt.encode(
        {"sub": "alice", "exp": int(time.time()) + 3600}, SECRET, algorithm="HS256"
    )
    client.set_cookie("streamflows_auth", token)
    resp = client.post("/api/chat")
    assert resp.status_code == 403

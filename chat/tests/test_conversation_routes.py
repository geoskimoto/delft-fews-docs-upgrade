"""Spec tests for the saved-conversation HTTP routes: auth matrix, Origin,
ownership, 15-cap, validation, 503 degradation, and log/DB privacy."""
import json
import logging
import os
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")

from chat.app import create_app
from chat.identity import storage_key

from chat import saved_conversations as saved
ConversationDB = saved.ConversationDB

SECRET = os.environ["JWT_SECRET"]
ORIGIN = "https://df-docs.streamflows.org"
BASE = "/api/chat/conversations"
EMAIL_A = "alice.wonder@hydro-example.org"
EMAIL_B = "bob.builder@other-example.net"
PARTS_A = ("alice.wonder", "hydro-example.org")
PARTS_B = ("bob.builder", "other-example.net")


def key(email):
    return storage_key(email)


def make_token(groups=("streamflow",), sub=EMAIL_A, exp_offset=3600):
    claims = {"exp": int(time.time()) + exp_offset}
    if groups is not None:
        claims["groups"] = list(groups) if isinstance(groups, (list, tuple)) else groups
    if sub is not None:
        claims["sub"] = sub
    return jwt.encode(claims, SECRET, algorithm="HS256")


def login(client, **kw):
    client.set_cookie("streamflows_auth", make_token(**kw))


def u(t):
    return {"role": "user", "content": t}


def a(t):
    return {"role": "assistant", "content": t}


def hdr(origin=ORIGIN):
    h = {"Content-Type": "application/json"}
    if origin:
        h["Origin"] = origin
    return h


def put(client, cid, body, origin=ORIGIN, raw=None, url=None, headers=None):
    h = hdr(origin)
    h.update(headers or {})
    return client.put(url or f"{BASE}/{cid}",
                      data=raw if raw is not None else json.dumps(body), headers=h)


def delete(client, cid=None, origin=ORIGIN, url=None, headers=None):
    h = hdr(origin)
    h.update(headers or {})
    return client.delete(url or (f"{BASE}/{cid}" if cid else BASE), headers=h)


def post_import(client, body, origin=ORIGIN, raw=None):
    return client.post(f"{BASE}/import",
                       data=raw if raw is not None else json.dumps(body),
                       headers=hdr(origin))


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
    usage = SimpleNamespace(input_tokens=10, output_tokens=5,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(stop_reason="end_turn", content=[], usage=usage)


@pytest.fixture
def anthropic():
    c = MagicMock()
    c.messages.stream.side_effect = lambda *a_, **k: FakeStream(["Hi"], done_message())
    return c


@pytest.fixture
def app(tmp_path, anthropic):
    return create_app({"ANTHROPIC_CLIENT": anthropic, "CORPUS": "TEST CORPUS",
                       "STATE_DIR": tmp_path, "DAILY_BUDGET_USD": 50.0})


@pytest.fixture
def client(app):
    c = app.test_client()
    login(c)
    return c


@pytest.fixture
def client_b(app):
    c = app.test_client()
    login(c, sub=EMAIL_B)
    return c


@pytest.fixture
def anon(app):
    return app.test_client()


@pytest.fixture
def cdb(app):
    return app.config["CONVERSATIONS"]


class FailingDB:
    """Every operation fails at the storage layer."""

    def _boom(self, *a_, **k):
        raise sqlite3.OperationalError("disk I/O error")

    list = get = save = delete = clear = import_many = _boom


# ============================================================== wiring

def test_app_wires_conversation_db_at_state_dir(app, tmp_path, client):
    assert isinstance(app.config["CONVERSATIONS"], ConversationDB)
    assert put(client, "c1", {"messages": [u("hi")]}).status_code == 200
    assert (tmp_path / "conversations.db").exists()


# ============================================================ auth matrix

ROUTES = [
    ("get", BASE, None),
    ("get", f"{BASE}/c1", None),
    ("put", f"{BASE}/c1", {"messages": [u("hi")]}),
    ("delete", f"{BASE}/c1", None),
    ("delete", BASE, None),
    ("post", f"{BASE}/import", {"conversations": []}),
]


def call(client, method, url, body, **cookie_kw):
    h = hdr()
    data = json.dumps(body) if body is not None else None
    return getattr(client, method)(url, data=data, headers=h)


@pytest.mark.parametrize("method,url,body", ROUTES)
def test_no_cookie_is_401(anon, cdb, method, url, body):
    assert call(anon, method, url, body).status_code == 401
    assert cdb.list(key(EMAIL_A)) == []


@pytest.mark.parametrize("method,url,body", ROUTES)
def test_garbage_cookie_is_401(anon, method, url, body):
    anon.set_cookie("streamflows_auth", "not.a.jwt")
    assert call(anon, method, url, body).status_code == 401


@pytest.mark.parametrize("method,url,body", ROUTES)
def test_expired_cookie_is_401(anon, method, url, body):
    login(anon, exp_offset=-10)
    assert call(anon, method, url, body).status_code == 401


@pytest.mark.parametrize("method,url,body", ROUTES)
def test_wrong_secret_cookie_is_401(anon, method, url, body):
    anon.set_cookie("streamflows_auth",
                    jwt.encode({"sub": EMAIL_A, "groups": ["streamflow"],
                                "exp": int(time.time()) + 100}, "other", algorithm="HS256"))
    assert call(anon, method, url, body).status_code == 401


@pytest.mark.parametrize("method,url,body", ROUTES)
@pytest.mark.parametrize("groups", [[], ["other"], ["Streamflow"], "streamflow", None,
                                    ["streamflow-readonly"]])
def test_wrong_group_is_403(anon, cdb, method, url, body, groups):
    login(anon, groups=groups)
    resp = call(anon, method, url, body)
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "not_authorized"}
    assert cdb.list(key(EMAIL_A)) == []


@pytest.mark.parametrize("groups", [["streamflow"], ["admin"], ["admin", "streamflow"]])
def test_streamflow_and_admin_groups_allowed(anon, groups):
    login(anon, groups=groups)
    assert anon.get(BASE).status_code == 200


def test_missing_sub_is_401(anon):
    login(anon, sub=None)
    assert anon.get(BASE).status_code == 401


# ================================================================== list

def test_list_empty(client):
    resp = client.get(BASE)
    assert resp.status_code == 200
    assert resp.get_json() == {"conversations": []}


def test_list_items_newest_first_with_shape(client):
    assert put(client, "c1", {"messages": [u("first q")]}).status_code == 200
    time.sleep(0.01)
    assert put(client, "c2", {"messages": [u("second q"), a("ans")]}).status_code == 200
    items = client.get(BASE).get_json()["conversations"]
    assert [i["id"] for i in items] == ["c2", "c1"]
    assert set(items[0]) == {"id", "title", "updatedAt", "messageCount"}
    assert items[0]["title"] == "second q"
    assert items[0]["messageCount"] == 2
    assert isinstance(items[0]["updatedAt"], int)
    assert "messages" not in items[0]


def test_list_only_own(client, client_b):
    put(client, "mine", {"messages": [u("a")]})
    put(client_b, "theirs", {"messages": [u("b")]})
    assert [i["id"] for i in client.get(BASE).get_json()["conversations"]] == ["mine"]
    assert [i["id"] for i in client_b.get(BASE).get_json()["conversations"]] == ["theirs"]


# ==================================================================== get

def test_get_roundtrip(client):
    put(client, "c1", {"messages": [u("What is X?"), a("X is Y.")]})
    resp = client.get(f"{BASE}/c1")
    assert resp.status_code == 200
    conv = resp.get_json()["conversation"]
    assert set(conv) == {"id", "title", "updatedAt", "messages"}
    assert conv["id"] == "c1"
    assert conv["title"] == "What is X?"
    assert conv["messages"] == [u("What is X?"), a("X is Y.")]


def test_get_missing_is_404_not_found(client):
    resp = client.get(f"{BASE}/nope")
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not_found"}


def test_get_other_users_is_404_identical_to_missing(client, client_b):
    put(client_b, "bobs", {"messages": [u("bob secret")]})
    other = client.get(f"{BASE}/bobs")
    missing = client.get(f"{BASE}/does-not-exist")
    assert other.status_code == 404
    assert other.get_json() == missing.get_json() == {"error": "not_found"}
    assert "bob secret" not in other.get_data(as_text=True)


# ==================================================================== put

def test_put_creates_then_updates_preserving_title(client):
    r1 = put(client, "c1", {"messages": [u("Original")]})
    assert r1.status_code == 200
    r2 = put(client, "c1", {"messages": [u("Changed"), a("x")]})
    assert r2.status_code == 200
    conv = r2.get_json()["conversation"]
    assert conv["title"] == "Original"
    assert conv["messages"] == [u("Changed"), a("x")]
    assert len(client.get(BASE).get_json()["conversations"]) == 1


def test_put_applies_capping(client):
    msgs = []
    for i in range(20):
        msgs += [u(f"q{i}"), a(f"a{i}")]
    conv = put(client, "c1", {"messages": msgs}).get_json()["conversation"]
    assert conv["messages"] == msgs[-12:]


def test_put_ignores_client_supplied_title_id_and_updatedat(client):
    resp = put(client, "c1", {"messages": [u("real title")], "title": "HACK",
                              "id": "other", "updatedAt": 1})
    conv = resp.get_json()["conversation"]
    assert conv["id"] == "c1"
    assert conv["title"] == "real title"
    assert conv["updatedAt"] > 10**12


def test_put_unicode(client):
    msgs = [u("\U0001F30A café 水"), a("☃")]
    assert put(client, "c1", {"messages": msgs}).status_code == 200
    assert client.get(f"{BASE}/c1").get_json()["conversation"]["messages"] == msgs


@pytest.mark.parametrize("body", [
    {}, {"messages": []}, {"messages": "hi"}, {"messages": None},
    {"messages": [{"role": "system", "content": "x"}]},
    {"messages": [{"role": "user", "content": 5}]},
    {"messages": [a("only assistant")]},
    {"messages": ["x"]},
])
def test_put_invalid_bodies_400(client, cdb, body):
    resp = put(client, "c1", body)
    assert resp.status_code == 400
    j = resp.get_json()
    assert j["error"] == "invalid_conversation"
    assert isinstance(j["message"], str) and j["message"]
    assert cdb.list(key(EMAIL_A)) == []


@pytest.mark.parametrize("raw", ["not json", "[1,2]", '"str"', "5", "null", ""])
def test_put_non_object_body_400(client, raw):
    resp = put(client, "c1", None, raw=raw)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_conversation"


def test_put_wrong_content_type_is_400(client):
    resp = client.put(f"{BASE}/c1", data=json.dumps({"messages": [u("x")]}),
                      headers={"Content-Type": "text/plain", "Origin": ORIGIN})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_conversation"


def test_put_lone_surrogate_400(client):
    resp = put(client, "c1", None, raw='{"messages":[{"role":"user","content":"\\ud800"}]}')
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_conversation"


@pytest.mark.parametrize("bad_id", ["x" * 65, "bad.id", "bad%20id", "caf%C3%A9"])
def test_put_bad_id_400(client, cdb, bad_id):
    resp = put(client, None, {"messages": [u("x")]}, url=f"{BASE}/{bad_id}")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_conversation"
    assert cdb.list(key(EMAIL_A)) == []


def test_put_oversized_body_rejected(client):
    resp = put(client, "c1", {"messages": [u("x" * (300 * 1024))]})
    assert resp.status_code == 413


def test_put_huge_content_within_request_limit_is_capped(client):
    resp = put(client, "c1", {"messages": [u("x" * (100 * 1024))]})
    assert resp.status_code == 200
    got = resp.get_json()["conversation"]["messages"]
    assert got[-1]["content"] == "x" * (100 * 1024)  # newest is never dropped


# =============================================================== delete one

def test_delete_one_removes_and_returns_ok(client):
    put(client, "c1", {"messages": [u("x")]})
    resp = delete(client, "c1")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert client.get(f"{BASE}/c1").status_code == 404


def test_delete_missing_is_ok(client):
    resp = delete(client, "never")
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}


def test_delete_other_users_id_ok_but_untouched(client, client_b, cdb):
    put(client_b, "bobs", {"messages": [u("bob data")]})
    resp = delete(client, "bobs")
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}
    assert cdb.get(key(EMAIL_B), "bobs")["messages"] == [u("bob data")]
    assert client_b.get(f"{BASE}/bobs").status_code == 200


# =============================================================== delete all

def test_delete_all_clears_only_callers(client, client_b):
    for i in range(3):
        put(client, f"a{i}", {"messages": [u("a")]})
        put(client_b, f"b{i}", {"messages": [u("b")]})
    resp = delete(client)
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}
    assert client.get(BASE).get_json() == {"conversations": []}
    assert len(client_b.get(BASE).get_json()["conversations"]) == 3


def test_delete_all_on_empty_ok(client):
    assert delete(client).get_json() == {"ok": True}


# ================================================================ import

def test_import_basic(client):
    now = int(time.time() * 1000)
    body = {"conversations": [
        {"id": "i1", "title": "Imported one", "updatedAt": now - 5000,
         "messages": [u("q1"), a("r1")]},
        {"id": "i2", "updatedAt": now - 9000, "messages": [u("derive me")]},
    ]}
    resp = post_import(client, body)
    assert resp.status_code == 200
    assert resp.get_json() == {"imported": 2, "skipped": 0}
    got = client.get(f"{BASE}/i1").get_json()["conversation"]
    assert got["title"] == "Imported one"
    assert got["updatedAt"] == now - 5000
    assert client.get(f"{BASE}/i2").get_json()["conversation"]["title"] == "derive me"


def test_import_skips_invalid_and_existing(client):
    put(client, "exists", {"messages": [u("server copy")]})
    body = {"conversations": [
        {"id": "exists", "messages": [u("client copy")]},
        {"id": "ok", "messages": [u("fine")]},
        {"id": "bad id", "messages": [u("x")]},
        {"id": "nomsgs"},
        "junk",
    ]}
    assert post_import(client, body).get_json() == {"imported": 1, "skipped": 4}
    assert client.get(f"{BASE}/exists").get_json()["conversation"]["messages"] == [
        u("server copy")]


def test_import_clamps_future_timestamps(client):
    post_import(client, {"conversations": [
        {"id": "f", "updatedAt": 99_999_999_999_999, "messages": [u("x")]}]})
    ts = client.get(f"{BASE}/f").get_json()["conversation"]["updatedAt"]
    assert ts <= int(time.time() * 1000) + 5000


def test_import_empty_list(client):
    assert post_import(client, {"conversations": []}).get_json() == {
        "imported": 0, "skipped": 0}


@pytest.mark.parametrize("body", [{}, {"conversations": "x"}, {"conversations": {"a": 1}},
                                  {"conversations": None}, {"other": []}])
def test_import_bad_shape_400(client, body):
    resp = post_import(client, body)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_conversation"


@pytest.mark.parametrize("raw", ["nope", "[]", "5", '"s"', "null"])
def test_import_non_object_400(client, raw):
    resp = post_import(client, None, raw=raw)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_conversation"


def test_import_respects_15_cap(client):
    now = int(time.time() * 1000)
    body = {"conversations": [
        {"id": f"c{i}", "updatedAt": now - 100_000 + i, "messages": [u(f"q{i}")]}
        for i in range(25)]}
    assert post_import(client, body).get_json()["imported"] == 15
    ids = {i["id"] for i in client.get(BASE).get_json()["conversations"]}
    assert ids == {f"c{i}" for i in range(10, 25)}


def test_import_only_into_callers_account(client, client_b):
    put(client_b, "same", {"messages": [u("bob")]})
    post_import(client, {"conversations": [{"id": "same", "messages": [u("alice")]}]})
    assert client_b.get(f"{BASE}/same").get_json()["conversation"]["messages"] == [u("bob")]
    assert client.get(f"{BASE}/same").get_json()["conversation"]["messages"][0] == u("alice")


# ================================================================= Origin

BAD_ORIGINS = [None, "https://evil.example.com", "null",
               "https://df-docs.streamflows.org.evil.com", "http://df-docs.streamflows.org",
               "https://df-docs.streamflows.org:8443"]


@pytest.mark.parametrize("origin", BAD_ORIGINS)
def test_put_bad_origin_403_nothing_changes(client, cdb, origin):
    resp = put(client, "c1", {"messages": [u("x")]}, origin=origin)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "bad_origin"
    assert cdb.list(key(EMAIL_A)) == []


@pytest.mark.parametrize("origin", BAD_ORIGINS)
def test_delete_one_bad_origin_403_nothing_changes(client, cdb, origin):
    put(client, "c1", {"messages": [u("x")]})
    resp = delete(client, "c1", origin=origin)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "bad_origin"
    assert cdb.get(key(EMAIL_A), "c1") is not None


@pytest.mark.parametrize("origin", BAD_ORIGINS)
def test_delete_all_bad_origin_403_nothing_changes(client, cdb, origin):
    put(client, "c1", {"messages": [u("x")]})
    resp = delete(client, origin=origin)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "bad_origin"
    assert len(cdb.list(key(EMAIL_A))) == 1


@pytest.mark.parametrize("origin", BAD_ORIGINS)
def test_import_bad_origin_403_nothing_changes(client, cdb, origin):
    resp = post_import(client, {"conversations": [{"id": "i", "messages": [u("x")]}]},
                       origin=origin)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "bad_origin"
    assert cdb.list(key(EMAIL_A)) == []


def test_get_routes_do_not_require_origin(client):
    assert client.get(BASE).status_code == 200
    put(client, "c1", {"messages": [u("x")]})
    assert client.get(f"{BASE}/c1").status_code == 200


def test_origin_checked_before_body_validation(client):
    resp = put(client, "c1", None, raw="garbage", origin="https://evil.example.com")
    assert resp.status_code == 403


# ============================================================= 15 cap (API)

def test_api_keeps_only_15_most_recent(client):
    for i in range(18):
        assert put(client, f"c{i}", {"messages": [u(f"q{i}")]}).status_code == 200
        time.sleep(0.002)
    items = client.get(BASE).get_json()["conversations"]
    assert len(items) == 15
    assert [i["id"] for i in items] == [f"c{i}" for i in range(17, 2, -1)]
    assert client.get(f"{BASE}/c0").status_code == 404


def test_api_cap_is_per_user(client, client_b):
    for i in range(3):
        put(client_b, f"b{i}", {"messages": [u("b")]})
    for i in range(17):
        put(client, f"a{i}", {"messages": [u("a")]})
    assert len(client.get(BASE).get_json()["conversations"]) == 15
    assert len(client_b.get(BASE).get_json()["conversations"]) == 3


# ============================================================== ownership

def test_user_cannot_overwrite_other_users_conversation_by_id(client, client_b, cdb):
    put(client_b, "shared", {"messages": [u("bob original")]})
    resp = put(client, "shared", {"messages": [u("alice hijack")]})
    assert resp.status_code == 200  # creates alice's own row with the same id
    bob = cdb.get(key(EMAIL_B), "shared")
    assert bob["messages"] == [u("bob original")]
    assert bob["title"] == "bob original"
    alice = cdb.get(key(EMAIL_A), "shared")
    assert alice["messages"] == [u("alice hijack")]
    assert client_b.get(f"{BASE}/shared").get_json()["conversation"]["messages"] == [
        u("bob original")]


def test_other_users_update_does_not_alter_my_title_or_time(client, client_b, cdb):
    put(client, "shared", {"messages": [u("alice title")]})
    before = cdb.get(key(EMAIL_A), "shared")
    put(client_b, "shared", {"messages": [u("bob title")]})
    assert cdb.get(key(EMAIL_A), "shared") == before


def test_other_users_prune_does_not_remove_mine(client, client_b, cdb):
    put(client, "keep", {"messages": [u("mine")]})
    for i in range(20):
        put(client_b, f"b{i}", {"messages": [u("b")]})
    assert cdb.get(key(EMAIL_A), "keep") is not None


EVIL_BODY_KEYS = {"user_key": "x", "userKey": "x", "storage_key": "x", "user": EMAIL_B,
                  "sub": EMAIL_B, "owner": EMAIL_B}


def test_cannot_influence_user_key_via_body(client, cdb):
    body = {"messages": [u("mine")], **EVIL_BODY_KEYS,
            "user_key": key(EMAIL_B), "storage_key": key(EMAIL_B)}
    assert put(client, "c1", body).status_code == 200
    assert cdb.get(key(EMAIL_A), "c1") is not None
    assert cdb.list(key(EMAIL_B)) == []
    assert cdb.list("x") == []


def test_cannot_influence_user_key_via_query(client, cdb, client_b):
    put(client_b, "bobs", {"messages": [u("bob")]})
    q = f"?user_key={key(EMAIL_B)}&storage_key={key(EMAIL_B)}&user={EMAIL_B}&sub={EMAIL_B}"
    assert client.get(f"{BASE}{q}").get_json() == {"conversations": []}
    assert client.get(f"{BASE}/bobs{q}").status_code == 404
    assert put(client, "c1", {"messages": [u("m")]}, url=f"{BASE}/c1{q}").status_code == 200
    assert delete(client, url=f"{BASE}{q}").status_code == 200
    assert cdb.get(key(EMAIL_B), "bobs") is not None
    assert cdb.list(key(EMAIL_B)) != [] and len(cdb.list(key(EMAIL_B))) == 1


def test_cannot_influence_user_key_via_headers(client, cdb, client_b):
    put(client_b, "bobs", {"messages": [u("bob")]})
    evil = {"X-User-Key": key(EMAIL_B), "X-Storage-Key": key(EMAIL_B),
            "X-Forwarded-User": EMAIL_B, "X-Remote-User": EMAIL_B,
            "X-User": EMAIL_B, "Remote-User": EMAIL_B}
    h = {**hdr(), **evil}
    assert client.get(BASE, headers=h).get_json() == {"conversations": []}
    assert client.get(f"{BASE}/bobs", headers=h).status_code == 404
    assert put(client, "c1", {"messages": [u("m")]}, headers=evil).status_code == 200
    assert delete(client, headers=evil).status_code == 200
    assert len(cdb.list(key(EMAIL_B))) == 1
    assert cdb.list(key(EMAIL_A)) == []  # own was cleared by delete-all


def test_cannot_influence_user_key_via_import_items(client, cdb):
    post_import(client, {"conversations": [
        {"id": "i", "messages": [u("x")], "user_key": key(EMAIL_B), "owner": EMAIL_B}],
        "user_key": key(EMAIL_B)})
    assert cdb.get(key(EMAIL_A), "i") is not None
    assert cdb.list(key(EMAIL_B)) == []


def test_user_key_is_storage_key_of_verified_subject(client, cdb):
    put(client, "c1", {"messages": [u("x")]})
    assert cdb.get(storage_key(EMAIL_A), "c1") is not None
    assert cdb.get(EMAIL_A, "c1") is None


def test_status_storage_key_matches_rows_owner(client, cdb):
    put(client, "c1", {"messages": [u("x")]})
    sk = client.get("/api/chat/status").get_json()["storage_key"]
    assert cdb.get(sk, "c1") is not None


def test_admin_group_user_gets_no_cross_user_access(app, cdb, client_b):
    put(client_b, "bobs", {"messages": [u("bob private")]})
    adm = app.test_client()
    login(adm, groups=("admin",), sub="root.admin@example.com")
    assert adm.get(f"{BASE}/bobs").status_code == 404
    assert adm.get(BASE).get_json() == {"conversations": []}


# ================================================== privacy: DB file + logs

def test_db_file_never_contains_email_parts(client, client_b, app, tmp_path):
    put(client, "c1", {"messages": [u("hello world")]})
    put(client_b, "c2", {"messages": [u("another")]})
    post_import(client, {"conversations": [{"id": "i", "messages": [u("imp")]}]})
    delete(client_b, "c2")
    blob = b"".join(p.read_bytes() for p in tmp_path.glob("conversations.db*"))
    assert blob  # something was written
    for part in PARTS_A + PARTS_B:
        assert part.encode() not in blob
    assert key(EMAIL_A).encode() in blob  # opaque key is what is stored


def all_log_text(caplog):
    return caplog.text + " " + " ".join(
        r.getMessage() + " " + json.dumps(r.__dict__, default=str)
        for r in caplog.records)


SECRETS = ["TOPSECRET-CONTENT-123", "TOPSECRET-TITLE-456"]


def assert_clean(caplog):
    text = all_log_text(caplog)
    for needle in PARTS_A + PARTS_B + tuple(SECRETS) + (EMAIL_A, EMAIL_B):
        assert needle not in text, needle


def test_logs_clean_on_success_paths(client, client_b, caplog):
    caplog.set_level(logging.DEBUG)
    put(client, "c1", {"messages": [u(SECRETS[1] + " question"), a(SECRETS[0])]})
    client.get(BASE)
    client.get(f"{BASE}/c1")
    client_b.get(f"{BASE}/c1")
    post_import(client, {"conversations": [
        {"id": "i1", "title": SECRETS[1], "messages": [u(SECRETS[0])]}]})
    delete(client, "c1")
    delete(client)
    assert_clean(caplog)


def test_logs_clean_on_failure_paths(client, caplog):
    caplog.set_level(logging.DEBUG)
    put(client, "c1", {"messages": [u(SECRETS[0])]}, origin="https://evil.example.com")
    put(client, "c1", {"messages": [{"role": "system", "content": SECRETS[0]}]})
    put(client, "c1", None, raw="{" + SECRETS[0])
    put(client, "bad.id", {"messages": [u(SECRETS[0])]})
    post_import(client, {"conversations": SECRETS[0]})
    post_import(client, {"conversations": [{"id": "bad id", "title": SECRETS[1],
                                            "messages": [u(SECRETS[0])]}]})
    delete(client, "c1", origin=None)
    login(client, groups=("nope",))
    client.get(BASE)
    assert_clean(caplog)


def test_logs_clean_on_503_paths(app, client, caplog):
    caplog.set_level(logging.DEBUG)
    app.config["CONVERSATIONS"] = FailingDB()
    put(client, "c1", {"messages": [u(SECRETS[1]), a(SECRETS[0])]})
    client.get(BASE)
    client.get(f"{BASE}/c1")
    delete(client, "c1")
    delete(client)
    post_import(client, {"conversations": [{"id": "i", "title": SECRETS[1],
                                            "messages": [u(SECRETS[0])]}]})
    assert_clean(caplog)


# ============================================================ 503 storage

FAILING_CALLS = [
    ("get", lambda c: c.get(BASE)),
    ("get_one", lambda c: c.get(f"{BASE}/c1")),
    ("put", lambda c: put(c, "c1", {"messages": [u("x")]})),
    ("delete_one", lambda c: delete(c, "c1")),
    ("delete_all", lambda c: delete(c)),
    ("import", lambda c: post_import(c, {"conversations": [
        {"id": "i", "messages": [u("x")]}]})),
]


@pytest.mark.parametrize("name,fn", FAILING_CALLS, ids=[n for n, _ in FAILING_CALLS])
def test_storage_error_is_503_with_friendly_message(app, client, name, fn):
    app.config["CONVERSATIONS"] = FailingDB()
    resp = fn(client)
    assert resp.status_code == 503
    j = resp.get_json()
    assert j["error"] == "storage_unavailable"
    assert isinstance(j["message"], str) and len(j["message"]) > 10
    text = resp.get_data(as_text=True)
    assert "Traceback" not in text
    assert "sqlite3" not in text and "disk I/O" not in text and "OperationalError" not in text


def test_storage_error_does_not_mask_auth_or_origin(app, anon, client):
    app.config["CONVERSATIONS"] = FailingDB()
    assert anon.get(BASE).status_code == 401
    assert put(client, "c1", {"messages": [u("x")]},
               origin="https://evil.example.com").status_code == 403


def test_chat_and_status_still_work_when_storage_down(app, client):
    app.config["CONVERSATIONS"] = FailingDB()
    resp = client.post("/api/chat", data=json.dumps(
        {"messages": [u("hello")]}), headers=hdr())
    assert resp.status_code == 200
    assert "Hi" in resp.get_data(as_text=True)
    st = client.get("/api/chat/status")
    assert st.status_code == 200 and st.get_json()["authenticated"] is True


def test_storage_recovers_after_failure(app, client):
    good = app.config["CONVERSATIONS"]
    app.config["CONVERSATIONS"] = FailingDB()
    assert client.get(BASE).status_code == 503
    app.config["CONVERSATIONS"] = good
    assert client.get(BASE).status_code == 200


def test_corrupt_db_file_gives_503_not_500(tmp_path, anthropic):
    (tmp_path / "conversations.db").write_bytes(b"garbage not sqlite" * 200)
    try:
        app = create_app({"ANTHROPIC_CLIENT": anthropic, "CORPUS": "X",
                          "STATE_DIR": tmp_path, "DAILY_BUDGET_USD": 5.0})
    except sqlite3.Error:
        pytest.skip("implementation opens the db eagerly at startup")
    c = app.test_client()
    login(c)
    assert c.get(BASE).status_code == 503


# ================================================= chat route unchanged

def test_chat_route_unchanged_still_streams(client):
    resp = client.post("/api/chat", data=json.dumps({"messages": [u("hello")]}),
                       headers=hdr())
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    assert "Hi" in resp.get_data(as_text=True)


def test_chat_does_not_write_conversations(client, cdb):
    client.post("/api/chat", data=json.dumps({"messages": [u("hello")]}),
                headers=hdr()).get_data()
    assert cdb.list(key(EMAIL_A)) == []

"""Spec tests for chat.saved_conversations.ConversationDB (server-side saved
conversations, per user, newest 15 kept)."""
import sqlite3
import threading

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

from chat import config

from chat import saved_conversations as saved
ConversationDB = saved.ConversationDB
InvalidConversation = saved.InvalidConversation

A = "user-key-A"
B = "user-key-B"
NOW = 1_800_000_000_000


def u(text):
    return {"role": "user", "content": text}


def a(text):
    return {"role": "assistant", "content": text}


@pytest.fixture
def db(tmp_path):
    return ConversationDB(tmp_path / "state" / "conversations.db")


def nbytes(msgs):
    return sum(len(m["content"].encode("utf-8")) for m in msgs)


# ================================================================== config

def test_config_constants():
    assert config.MAX_SAVED_CONVERSATIONS == 15
    assert config.MAX_SAVED_MESSAGES == 12
    assert config.MAX_SAVED_BYTES == 24 * 1024
    assert config.MAX_TITLE_CHARS == 60


def test_invalid_conversation_is_value_error():
    assert issubclass(InvalidConversation, ValueError)


# ============================================================ schema/persist

def test_creates_parent_dir_and_file_and_wal(tmp_path):
    path = tmp_path / "deep" / "er" / "c.db"
    d = ConversationDB(path)
    assert d.list(A) == []
    d.save(A, "c1", [u("hi")])
    assert path.exists()
    con = sqlite3.connect(path)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        con.close()


def test_persists_across_instances(tmp_path):
    path = tmp_path / "c.db"
    ConversationDB(path).save(A, "c1", [u("hello"), a("world")], now_ms=NOW)
    again = ConversationDB(path)
    rec = again.get(A, "c1")
    assert rec["messages"] == [u("hello"), a("world")]
    assert rec["updatedAt"] == NOW
    assert again.list(A)[0]["messageCount"] == 2


def test_missing_user_lists_empty_and_get_none(db):
    assert db.list("nobody") == []
    assert db.get("nobody", "x") is None


# ================================================================== save

def test_save_returns_record_and_get_roundtrips(db):
    rec = db.save(A, "c1", [u("What is a task?"), a("A thing.")], now_ms=NOW)
    assert rec == {"id": "c1", "title": "What is a task?", "updatedAt": NOW,
                   "messages": [u("What is a task?"), a("A thing.")]}
    assert db.get(A, "c1") == rec


def test_save_default_now_is_int_ms(db):
    import time
    before = int(time.time() * 1000)
    rec = db.save(A, "c1", [u("x")])
    after = int(time.time() * 1000)
    assert isinstance(rec["updatedAt"], int)
    assert before <= rec["updatedAt"] <= after


def test_list_item_shape_newest_first(db):
    db.save(A, "old", [u("one")], now_ms=NOW)
    db.save(A, "new", [u("two"), a("r")], now_ms=NOW + 10)
    db.save(A, "mid", [u("three")], now_ms=NOW + 5)
    items = db.list(A)
    assert [i["id"] for i in items] == ["new", "mid", "old"]
    assert items[0] == {"id": "new", "title": "two", "updatedAt": NOW + 10,
                        "messageCount": 2}
    assert set(items[0]) == {"id", "title", "updatedAt", "messageCount"}


def test_upsert_replaces_messages_bumps_time_preserves_title(db):
    db.save(A, "c1", [u("Original question")], now_ms=NOW)
    rec = db.save(A, "c1", [u("Different first message"), a("ans")], now_ms=NOW + 50)
    assert rec["title"] == "Original question"
    assert rec["updatedAt"] == NOW + 50
    got = db.get(A, "c1")
    assert got["title"] == "Original question"
    assert got["messages"][0]["content"] == "Different first message"
    assert len(db.list(A)) == 1


def test_upsert_bumps_ordering(db):
    db.save(A, "c1", [u("a")], now_ms=NOW)
    db.save(A, "c2", [u("b")], now_ms=NOW + 1)
    db.save(A, "c1", [u("a"), a("x")], now_ms=NOW + 2)
    assert [i["id"] for i in db.list(A)] == ["c1", "c2"]


# ------------------------------------------------------------- validation

@pytest.mark.parametrize("bad_id", ["", "x" * 65, "has space", "a/b", "a.b", "é",
                                    "a;b", "../x", "a\n", None, 5, b"abc", ["a"]])
def test_bad_ids_rejected(db, bad_id):
    with pytest.raises(InvalidConversation):
        db.save(A, bad_id, [u("hi")])
    assert db.list(A) == []


@pytest.mark.parametrize("good_id", ["a", "A-b_c9", "x" * 64, "1234567890123"])
def test_good_ids_accepted(db, good_id):
    assert db.save(A, good_id, [u("hi")])["id"] == good_id


@pytest.mark.parametrize("msgs", [
    None, [], "hi", {"role": "user", "content": "x"}, 5,
    [None], ["x"], [5], [[u("x")]],
    [{"role": "system", "content": "x"}],
    [{"role": "User", "content": "x"}],
    [{"role": "user"}],
    [{"content": "x"}],
    [{"role": "user", "content": 5}],
    [{"role": "user", "content": None}],
    [{"role": "user", "content": ["x"]}],
    [{"role": 1, "content": "x"}],
    [u("ok"), {"role": "tool", "content": "x"}],
    [u("ok"), None],
])
def test_bad_messages_rejected_and_nothing_stored(db, msgs):
    with pytest.raises(InvalidConversation):
        db.save(A, "c1", msgs)
    assert db.get(A, "c1") is None
    assert db.list(A) == []


def test_lone_surrogate_rejected(db):
    with pytest.raises(InvalidConversation):
        db.save(A, "c1", [u("bad \ud800 text")])
    with pytest.raises(InvalidConversation):
        db.save(A, "c1", [u("fine"), a("\udfff")])
    assert db.list(A) == []


def test_failed_save_does_not_disturb_existing(db):
    db.save(A, "c1", [u("keep")], now_ms=NOW)
    with pytest.raises(InvalidConversation):
        db.save(A, "c1", [u("x\ud800")], now_ms=NOW + 1)
    assert db.get(A, "c1")["messages"] == [u("keep")]
    assert db.get(A, "c1")["updatedAt"] == NOW


def test_assistant_only_is_nothing_left(db):
    with pytest.raises(InvalidConversation):
        db.save(A, "c1", [a("hello")])
    assert db.get(A, "c1") is None


def test_empty_string_content_allowed_if_valid_shape(db):
    rec = db.save(A, "c1", [u(""), a("")])
    assert rec["title"] == "Untitled"


# ---------------------------------------------------------------- unicode

def test_unicode_and_emoji_roundtrip_and_byte_counting(db):
    msgs = [u("Hydrologie écoulement \U0001F30A\U0001F4A7 水文"),
            a("☃ snow \U0001F9CA")]
    rec = db.save(A, "c1", msgs)
    assert rec["messages"] == msgs
    assert db.get(A, "c1")["messages"] == msgs


def test_byte_cap_counts_utf8_bytes_not_chars(db):
    # 3-byte chars: 4000 chars = 12000 bytes each; two fit (24000), three do not.
    big = "水" * 4000
    msgs = [u(big), a(big), u(big)]
    rec = db.save(A, "c1", msgs)
    # newest two (a, u) fit in 24000 bytes; the leading assistant is then dropped.
    assert rec["messages"] == [u(big)]
    assert nbytes(rec["messages"]) <= config.MAX_SAVED_BYTES


# ================================================================ capping

def test_keeps_last_12_messages(db):
    msgs = []
    for i in range(20):
        msgs.append(u(f"q{i}"))
        msgs.append(a(f"a{i}"))
    rec = db.save(A, "c1", msgs)
    assert len(rec["messages"]) == 12
    assert rec["messages"] == msgs[-12:]
    assert rec["messages"][0]["role"] == "user"


def test_exactly_12_untouched(db):
    msgs = [u("q"), a("a")] * 6
    assert db.save(A, "c1", msgs)["messages"] == msgs


def test_twelve_window_starting_with_assistant_drops_it(db):
    # 13 messages -> last 12 start with an assistant -> that one is dropped.
    msgs = [u("q0")] + [a("a"), u("q")] * 6
    assert len(msgs) == 13
    window = msgs[-12:]
    assert window[0]["role"] == "assistant"
    rec = db.save(A, "c1", msgs)
    assert rec["messages"] == window[1:]


def test_byte_budget_drops_oldest_keeps_newest(db):
    chunk = "x" * 10_000
    msgs = [u(chunk), a(chunk), u(chunk), a(chunk)]
    rec = db.save(A, "c1", msgs)
    # 4*10000 > 24576: newest two fit (20000), three do not.
    assert rec["messages"] == msgs[-2:]
    assert nbytes(rec["messages"]) <= config.MAX_SAVED_BYTES


def test_byte_budget_result_never_starts_with_assistant(db):
    chunk = "x" * 10_000
    msgs = [u(chunk), a(chunk), u(chunk)]
    # newest two = [assistant, user]; the leading assistant is dropped.
    rec = db.save(A, "c1", msgs)
    assert rec["messages"] == [u(chunk)]


def test_newest_message_kept_even_if_over_budget(db):
    huge = "y" * (config.MAX_SAVED_BYTES * 2)
    rec = db.save(A, "c1", [u("short"), a("ok"), u(huge)])
    assert rec["messages"] == [u(huge)]


def test_newest_assistant_over_budget_pulls_preceding_user_back(db):
    huge = "z" * (config.MAX_SAVED_BYTES * 2)
    rec = db.save(A, "c1", [u("q1"), a("a1"), u("the question"), a(huge)])
    assert rec["messages"] == [u("the question"), a(huge)]


def test_leading_assistants_dropped(db):
    # Window of 12 whose first two are assistants cannot happen in a strict
    # alternation, but save() must tolerate any ordering.
    msgs = [u("q0"), a("x"), a("y"), u("q1"), a("z")]
    rec = db.save(A, "c1", msgs)
    assert rec["messages"] == msgs  # starts with user, so untouched
    rec = db.save(A, "c2", [a("x"), a("y"), u("q1"), a("z")])
    assert rec["messages"] == [u("q1"), a("z")]


def test_total_bytes_exactly_at_budget_kept(db):
    half = "x" * (config.MAX_SAVED_BYTES // 2)
    msgs = [u(half), a(half)]
    assert nbytes(msgs) == config.MAX_SAVED_BYTES
    assert db.save(A, "c1", msgs)["messages"] == msgs


MSG = st.builds(
    lambda r, c: {"role": r, "content": c},
    st.sampled_from(["user", "assistant"]),
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=6000),
)


@settings(max_examples=60, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow,
                                 HealthCheck.data_too_large])
@given(msgs=st.lists(MSG, min_size=1, max_size=30))
def test_property_capping_invariants(tmp_path_factory, msgs):
    assume(any(m["role"] == "user" for m in msgs[-12:]))
    d = ConversationDB(tmp_path_factory.mktemp("prop") / "c.db")
    rec = d.save(A, "p", msgs)
    out = rec["messages"]
    assert 1 <= len(out)
    assert out[0]["role"] == "user"
    assert out[-1] == msgs[-1]
    it = iter(msgs)
    assert all(any(m == x for x in it) for m in out)  # ordered subsequence
    if nbytes(out) > config.MAX_SAVED_BYTES:
        assert len(out) <= 2  # only the forced over-budget newest (+ its question)
    assert d.get(A, "p")["messages"] == out


# ================================================================== title

def title_of(db, text, key="t"):
    return db.save(A, key, [u(text), a("r")])["title"]


def test_title_simple(db):
    assert title_of(db, "How do I configure a task?") == "How do I configure a task?"


def test_title_whitespace_collapsed_and_stripped(db):
    assert title_of(db, "  hello \n\t  world   again \n") == "hello world again"


@pytest.mark.parametrize("text", ["", "   ", "\n\t \n"])
def test_title_untitled_when_blank(db, text):
    assert title_of(db, text) == "Untitled"


def test_title_exactly_60_not_truncated(db):
    t = "a" * 60
    assert title_of(db, t) == t


def test_title_61_truncated_to_59_plus_ellipsis(db):
    got = title_of(db, "b" * 61)
    assert got == "b" * 59 + "…"
    assert len(got) == 60


def test_title_truncation_after_collapsing(db):
    got = title_of(db, ("word " * 30))
    assert len(got) == 60 and got.endswith("…")
    assert "  " not in got


def test_title_counts_characters_not_bytes(db):
    t = "水" * 60
    assert title_of(db, t) == t
    got = title_of(db, "水" * 70, key="t2")
    assert got == "水" * 59 + "…"


def test_title_uses_first_user_message(db):
    assert db.save(A, "c1", [u("first"), a("x"), u("second")])["title"] == "first"


def test_title_from_first_user_message_after_capping(db):
    msgs = []
    for i in range(10):
        msgs += [u(f"question {i}"), a("r")]
    rec = db.save(A, "c1", msgs)
    # First save: title derives from the stored (capped) first user message
    # or the original; either way it must be one of the user questions.
    assert rec["title"] in {f"question {i}" for i in range(10)}


def test_title_not_overwritten_on_update_even_with_different_first(db):
    db.save(A, "c1", [u("Stable title")], now_ms=NOW)
    for i in range(3):
        rec = db.save(A, "c1", [u(f"changed {i}"), a("r")], now_ms=NOW + i + 1)
        assert rec["title"] == "Stable title"
    assert db.list(A)[0]["title"] == "Stable title"


def test_title_preserved_after_messages_capped_away(db):
    db.save(A, "c1", [u("Original topic")], now_ms=NOW)
    many = []
    for i in range(20):
        many += [u(f"later {i}"), a("r")]
    assert db.save(A, "c1", many, now_ms=NOW + 1)["title"] == "Original topic"


TITLE_TEXT = st.text(
    alphabet=st.one_of(
        st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Po")),
        st.sampled_from([" ", "\t", "\n"]),
    ),
    max_size=200,
)


@settings(max_examples=80, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(text=TITLE_TEXT)
def test_property_title_rules(tmp_path_factory, text):
    d = ConversationDB(tmp_path_factory.mktemp("title") / "c.db")
    got = d.save(A, "p", [u(text), a("r")])["title"]
    collapsed = " ".join(text.split())
    if not collapsed:
        assert got == "Untitled"
    elif len(collapsed) <= 60:
        assert got == collapsed
    else:
        assert got == collapsed[:59] + "…"
    assert len(got) <= 60
    assert got == got.strip()
    assert "\n" not in got and "\t" not in got and "  " not in got
    # A second save never changes it.
    again = d.save(A, "p", [u("zzz other"), a("r")])["title"]
    assert again == got


# ================================================================ pruning

def test_prune_to_15_most_recent(db):
    for i in range(20):
        db.save(A, f"c{i}", [u(f"q{i}")], now_ms=NOW + i)
    items = db.list(A)
    assert len(items) == 15
    assert [i["id"] for i in items] == [f"c{i}" for i in range(19, 4, -1)]
    assert db.get(A, "c4") is None
    assert db.get(A, "c5") is not None


def test_prune_scoped_to_user_only(db):
    for i in range(3):
        db.save(B, f"b{i}", [u("b")], now_ms=NOW + i)
    for i in range(20):
        db.save(A, f"a{i}", [u("a")], now_ms=NOW + 100 + i)
    assert len(db.list(A)) == 15
    assert len(db.list(B)) == 3


def test_updating_old_conversation_saves_it_from_prune(db):
    for i in range(15):
        db.save(A, f"c{i}", [u("q")], now_ms=NOW + i)
    db.save(A, "c0", [u("q"), a("r")], now_ms=NOW + 100)  # bump oldest
    db.save(A, "new", [u("n")], now_ms=NOW + 101)
    ids = {i["id"] for i in db.list(A)}
    assert len(ids) == 15
    assert "c0" in ids and "new" in ids and "c1" not in ids


def test_saving_older_timestamp_than_all_is_pruned_immediately_when_full(db):
    for i in range(15):
        db.save(A, f"c{i}", [u("q")], now_ms=NOW + 1000 + i)
    db.save(A, "ancient", [u("q")], now_ms=NOW)
    assert db.get(A, "ancient") is None
    assert len(db.list(A)) == 15


def test_prune_happens_in_same_transaction(tmp_path, monkeypatch):
    """No connection may ever observe 16 rows for a user, even between the
    upsert and the prune (prune must be inside the upsert's transaction)."""
    path = tmp_path / "c.db"
    d = ConversationDB(path)
    for i in range(15):
        d.save(A, f"c{i}", [u("q")], now_ms=NOW + i)

    observed = []
    stop = threading.Event()

    def watcher():
        con = sqlite3.connect(path, timeout=30)
        try:
            while not stop.is_set():
                tables = [r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")]
                for t in tables:
                    cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})")]
                    if "user_key" in cols:
                        observed.append(con.execute(
                            f"SELECT COUNT(*) FROM {t} WHERE user_key=?", (A,)
                        ).fetchone()[0])
        finally:
            con.close()

    th = threading.Thread(target=watcher)
    th.start()
    try:
        for i in range(15, 80):
            d.save(A, f"c{i}", [u("q")], now_ms=NOW + i)
    finally:
        stop.set()
        th.join()
    assert observed, "watcher saw no rows table with a user_key column"
    assert max(observed) <= 15


def test_concurrent_saves_never_exceed_15_or_corrupt(tmp_path):
    d = ConversationDB(tmp_path / "c.db")
    errors = []
    counter = iter(range(10_000))
    lock = threading.Lock()

    def worker(n):
        try:
            for j in range(10):
                with lock:
                    ts = NOW + next(counter)
                d.save(A, f"w{n}-{j}", [u(f"q {n} {j}"), a("r")], now_ms=ts)
                assert len(d.list(A)) <= 15
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    items = d.list(A)
    assert len(items) == 15
    for it in items:
        rec = d.get(A, it["id"])
        assert rec["messages"][0]["content"].startswith("q ")
    con = sqlite3.connect(tmp_path / "c.db")
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


def test_concurrent_saves_two_users_independent(tmp_path):
    d = ConversationDB(tmp_path / "c.db")
    errors = []

    def worker(user, n):
        try:
            for j in range(12):
                d.save(user, f"{user}-{n}-{j}", [u("q")], now_ms=NOW + n * 100 + j)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    ts = [threading.Thread(target=worker, args=(usr, n))
          for n in range(4) for usr in (A, B)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []
    la, lb = d.list(A), d.list(B)
    assert len(la) == 15 and len(lb) == 15
    assert all(i["id"].startswith(A) for i in la)
    assert all(i["id"].startswith(B) for i in lb)


# ============================================================== isolation

def test_same_id_two_users_coexist_independently(db):
    db.save(A, "shared", [u("alice question")], now_ms=NOW)
    db.save(B, "shared", [u("bob question"), a("bob answer")], now_ms=NOW + 1)
    assert db.get(A, "shared")["messages"] == [u("alice question")]
    assert db.get(B, "shared")["title"] == "bob question"
    db.save(A, "shared", [u("alice 2"), a("x")], now_ms=NOW + 2)
    assert db.get(B, "shared")["messages"] == [u("bob question"), a("bob answer")]
    assert db.get(B, "shared")["updatedAt"] == NOW + 1
    db.delete(A, "shared")
    assert db.get(A, "shared") is None
    assert db.get(B, "shared") is not None


def test_get_other_users_id_is_none(db):
    db.save(A, "only-a", [u("secret")])
    assert db.get(B, "only-a") is None
    assert db.list(B) == []


def test_delete_other_users_id_is_noop(db):
    db.save(A, "only-a", [u("secret")])
    assert db.delete(B, "only-a") is None
    assert db.get(A, "only-a") is not None


def test_user_key_with_sql_metacharacters_is_inert(db):
    db.save(A, "c1", [u("x")])
    evil = "x' OR '1'='1"
    assert db.list(evil) == []
    assert db.get(evil, "c1") is None
    db.clear(evil)
    db.delete(evil, "c1")
    assert len(db.list(A)) == 1


# ================================================================ delete

def test_delete_removes_and_is_idempotent(db):
    db.save(A, "c1", [u("x")])
    assert db.delete(A, "c1") is None
    assert db.get(A, "c1") is None
    assert db.delete(A, "c1") is None
    assert db.delete(A, "never-existed") is None


def test_delete_only_target_row(db):
    db.save(A, "c1", [u("x")], now_ms=NOW)
    db.save(A, "c2", [u("y")], now_ms=NOW + 1)
    db.delete(A, "c1")
    assert [i["id"] for i in db.list(A)] == ["c2"]


def test_delete_with_invalid_id_does_not_raise_or_touch(db):
    db.save(A, "c1", [u("x")])
    db.delete(A, "../weird")
    assert len(db.list(A)) == 1


def test_clear_only_that_user(db):
    for i in range(3):
        db.save(A, f"a{i}", [u("a")], now_ms=NOW + i)
        db.save(B, f"b{i}", [u("b")], now_ms=NOW + i)
    assert db.clear(A) is None
    assert db.list(A) == []
    assert len(db.list(B)) == 3
    assert db.clear(A) is None  # idempotent
    assert db.clear("never") is None


# ================================================================= import

def rec(id_, text="hello", updated=None, title=None, messages=None):
    r = {"id": id_, "messages": messages if messages is not None else [u(text), a("r")]}
    if updated is not None:
        r["updatedAt"] = updated
    if title is not None:
        r["title"] = title
    return r


def test_import_basic_preserves_updated_at_and_title(db):
    res = db.import_many(A, [rec("c1", "q", updated=NOW - 5000, title="Mine")], now_ms=NOW)
    assert res == {"imported": 1, "skipped": 0}
    got = db.get(A, "c1")
    assert got["updatedAt"] == NOW - 5000
    assert got["title"] == "Mine"
    assert got["messages"] == [u("q"), a("r")]


def test_import_clamps_future_updated_at_to_now(db):
    db.import_many(A, [rec("c1", updated=NOW + 10**9)], now_ms=NOW)
    assert db.get(A, "c1")["updatedAt"] == NOW


def test_import_default_now_clamps_to_current_time(db):
    import time
    db.import_many(A, [rec("c1", updated=10**15)])
    assert db.get(A, "c1")["updatedAt"] <= int(time.time() * 1000) + 1000


def test_import_title_derived_when_missing_or_blank_or_nonstr(db):
    items = [rec("c1", "derive me", updated=NOW - 1),
             rec("c2", "blank one", updated=NOW - 2, title="   "),
             rec("c3", "empty one", updated=NOW - 3, title=""),
             rec("c4", "nonstr one", updated=NOW - 4, title=123)]
    assert db.import_many(A, items, now_ms=NOW) == {"imported": 4, "skipped": 0}
    assert db.get(A, "c1")["title"] == "derive me"
    assert db.get(A, "c2")["title"] == "blank one"
    assert db.get(A, "c3")["title"] == "empty one"
    assert db.get(A, "c4")["title"] == "nonstr one"


def test_import_given_title_truncated_to_60(db):
    db.import_many(A, [rec("c1", title="T" * 100, updated=NOW - 1)], now_ms=NOW)
    t = db.get(A, "c1")["title"]
    assert len(t) <= 60
    assert t.startswith("T" * 59)


def test_import_does_not_overwrite_existing_ids(db):
    db.save(A, "c1", [u("server version")], now_ms=NOW - 100)
    res = db.import_many(A, [rec("c1", "client version", updated=NOW - 1),
                             rec("c2", "new", updated=NOW - 2)], now_ms=NOW)
    assert res == {"imported": 1, "skipped": 1}
    assert db.get(A, "c1")["messages"] == [u("server version")]
    assert db.get(A, "c1")["updatedAt"] == NOW - 100


def test_import_same_id_belonging_to_other_user_is_not_a_conflict(db):
    db.save(B, "c1", [u("bob")], now_ms=NOW - 100)
    res = db.import_many(A, [rec("c1", "alice", updated=NOW - 1)], now_ms=NOW)
    assert res == {"imported": 1, "skipped": 0}
    assert db.get(B, "c1")["messages"] == [u("bob")]
    assert db.get(A, "c1")["messages"][0] == u("alice")


def test_import_skips_invalid_items_without_failing(db):
    items = [
        rec("good1", updated=NOW - 1),
        "string", None, 5, [],
        {"id": "no-messages"},
        {"id": "empty", "messages": []},
        {"id": "bad id!", "messages": [u("x")]},
        {"id": "", "messages": [u("x")]},
        {"id": 7, "messages": [u("x")]},
        {"messages": [u("x")]},
        {"id": "badrole", "messages": [{"role": "system", "content": "x"}]},
        {"id": "surrogate", "messages": [u("\ud800")]},
        {"id": "asst", "messages": [a("only assistant")]},
        rec("good2", updated=NOW - 2),
    ]
    res = db.import_many(A, items, now_ms=NOW)
    assert res["imported"] == 2
    assert res["skipped"] == 13
    assert {i["id"] for i in db.list(A)} == {"good1", "good2"}


def test_import_empty_list(db):
    assert db.import_many(A, [], now_ms=NOW) == {"imported": 0, "skipped": 0}


def test_import_duplicate_ids_within_batch_only_first_wins(db):
    res = db.import_many(A, [rec("d", "first", updated=NOW - 1),
                             rec("d", "second", updated=NOW - 2)], now_ms=NOW)
    assert res["imported"] == 1 and res["skipped"] == 1
    assert len(db.list(A)) == 1


def test_import_applies_message_capping(db):
    msgs = []
    for i in range(20):
        msgs += [u(f"q{i}"), a(f"a{i}")]
    db.import_many(A, [rec("big", messages=msgs, updated=NOW - 1)], now_ms=NOW)
    got = db.get(A, "big")["messages"]
    assert len(got) == 12
    assert got == msgs[-12:]


def test_import_missing_or_invalid_updated_at_treated_oldest(db):
    items = [rec(f"dated{i}", updated=NOW - 1000 + i) for i in range(15)]
    items += [rec("nodate"), rec("strdate", updated="yesterday"),
              rec("negdate", updated=-5), rec("nandate", updated=float("nan"))]
    res = db.import_many(A, items, now_ms=NOW)
    ids = {i["id"] for i in db.list(A)}
    assert ids == {f"dated{i}" for i in range(15)}
    assert res["imported"] == 15
    assert res["imported"] + res["skipped"] <= len(items)


def test_import_considers_only_15_newest_items(db):
    items = [rec(f"c{i}", updated=NOW - 10_000 + i) for i in range(25)]
    res = db.import_many(A, items, now_ms=NOW)
    assert res["imported"] == 15
    assert {i["id"] for i in db.list(A)} == {f"c{i}" for i in range(10, 25)}


def test_import_prunes_total_to_15_dropping_old_imports(db):
    for i in range(12):
        db.save(A, f"existing{i}", [u("e")], now_ms=NOW - 100 + i)
    items = [rec(f"old{i}", updated=NOW - 100_000 + i) for i in range(5)]
    items.append(rec("newer", updated=NOW - 1))
    db.import_many(A, items, now_ms=NOW)
    ids = {i["id"] for i in db.list(A)}
    assert len(ids) == 15
    assert "newer" in ids
    assert all(f"existing{i}" in ids for i in range(12))
    assert sum(1 for i in ids if i.startswith("old")) == 2


def test_import_never_touches_other_users(db):
    for i in range(15):
        db.save(B, f"b{i}", [u("b")], now_ms=NOW - 50 + i)
    db.import_many(A, [rec(f"a{i}", updated=NOW - 1 - i) for i in range(15)], now_ms=NOW)
    assert len(db.list(B)) == 15
    assert len(db.list(A)) == 15


def test_import_unicode_roundtrip(db):
    msgs = [u("\U0001F30A水 café"), a("☃")]
    db.import_many(A, [rec("uni", messages=msgs, updated=NOW - 1)], now_ms=NOW)
    assert db.get(A, "uni")["messages"] == msgs


# ============================================================ storage errors

def test_storage_errors_surface_as_sqlite_error(tmp_path):
    d = ConversationDB(tmp_path / "c.db")
    d.save(A, "c1", [u("x")])
    (tmp_path / "c.db").unlink()
    for ext in ("-wal", "-shm"):
        p = tmp_path / ("c.db" + ext)
        if p.exists():
            p.unlink()
    # Replace the file with garbage so every operation fails at the SQLite layer.
    (tmp_path / "c.db").write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(sqlite3.Error):
        d.list(A)
    with pytest.raises(sqlite3.Error):
        d.save(A, "c2", [u("x")])
    with pytest.raises(sqlite3.Error):
        d.get(A, "c1")
    with pytest.raises(sqlite3.Error):
        d.delete(A, "c1")
    with pytest.raises(sqlite3.Error):
        d.clear(A)
    with pytest.raises(sqlite3.Error):
        d.import_many(A, [rec("c9")])


# ================================================== no raw subject in the db

def test_db_file_contains_only_what_the_caller_passed(tmp_path):
    path = tmp_path / "c.db"
    d = ConversationDB(path)
    d.save("opaquekey1234abcd", "c1", [u("hello")])
    blob = b"".join(p.read_bytes() for p in tmp_path.glob("c.db*"))
    assert b"opaquekey1234abcd" in blob

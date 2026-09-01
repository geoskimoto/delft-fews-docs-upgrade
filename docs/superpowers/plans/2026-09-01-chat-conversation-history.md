# Chat Conversation History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a logged-in user's chat conversations across page navigation, reloads and sessions, with a browsable list of the last 25.

**Architecture:** A `createStore(namespace, storage, now)` factory in `src/scripts/conversation-store.js` with an injected storage handle and clock — pure enough to test under `node --test` with a Map-backed fake, and the seam a server-backed store would slot into later. `GET /api/chat/status` gains an opaque per-user `storage_key` so the browser can namespace its storage without ever holding an email address. `ChatPanel.astro` wires the two together and grows a "Recent" list.

**Tech Stack:** Vanilla ES modules, Astro 5 component `<script>`, Flask, `node --test` and pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-01-chat-conversation-history-design.md`

## Global Constraints

- **No new runtime or dev dependency.** Neither `package.json` nor `chat/requirements.txt` gains a package.
- **No conversation content is stored or logged server-side.** The service stays stateless about content; `chat/routes.py` logs counts only. Nothing in this plan writes a message to disk on the server.
- **No PII in browser storage.** The `sub` claim is an email address and must never reach `localStorage`. Only the opaque `storage_key` does.
- **A missing or blank namespace must degrade to in-memory, never to a shared constant key.** A shared key pools every user of a shared browser into one history — the exact failure the namespacing exists to prevent.
- **No `innerHTML`, `outerHTML` or `insertAdjacentHTML` anywhere** in `ChatPanel.astro`. Every node via `createElement` / `createTextNode`. Conversation titles are model-adjacent user text and must go in through `textContent`.
- **The store module imports nothing and references no browser global** (`document`, `window`, `navigator`, `localStorage`). `TextEncoder` is a standard global available in both Node 22 and browsers and is permitted.
- **A storage failure must never cost the user an answer already on screen.** Every store call from the panel is best-effort.
- Tests are a locked specification. Do not edit a test file to make an implementation pass. If a test looks wrong, stop and report it. Do not alter a given assertion — not its comparison, not its threshold, not its expected value.
- Node is v22.23.1. The JS test command is `npm run test:js`, already configured as `node --test "src/scripts/*.test.js"`.
- The Python suite runs as `chat/venv/bin/python -m pytest chat/tests -q` from the repo root and currently reports 129 passing.
- The JS suite currently reports 51 passing.
- **The store and its 31 tests in this plan were executed before the plan was committed: all 31 pass as written.** A failure means the code was transcribed incorrectly, not that a test is wrong.

---

### Task 1: The conversation store

The whole of the persistence logic, with no DOM and no server. Everything later tasks do is wiring.

**Files:**
- Create: `src/scripts/conversation-store.js`
- Create: `src/scripts/conversation-store.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `createStore(namespace: string, storage: StorageLike|null, now?: () => number) -> Store`
  - `Store = { list(), load(id), save(id, messages), remove(id), clearAll() }`
  - `list() -> [{id, title, updatedAt, messageCount}]`, newest updated first
  - `load(id) -> {id, title, updatedAt, messages} | null`
  - `save(id, messages) -> record | null` (null when the id is blank or nothing usable remains after capping)
  - `newId() -> string`, `capMessages(messages)`, `titleFrom(messages)`
  - Constants `STORAGE_PREFIX`, `MAX_CONVERSATIONS` (25), `MAX_MESSAGES` (12), `MAX_BYTES` (24576), `MAX_TITLE` (60)
  - `StorageLike` is anything with `getItem`/`setItem`/`removeItem`; `window.localStorage` satisfies it.

- [ ] **Step 1: Write the failing tests**

Create `src/scripts/conversation-store.test.js`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createStore, newId, capMessages, titleFrom,
  STORAGE_PREFIX, MAX_CONVERSATIONS, MAX_MESSAGES, MAX_BYTES,
} from './conversation-store.js';

/* A Map-backed stand-in for localStorage. `fail` makes setItem throw, which is
   how the quota path is exercised without filling a real browser. */
function fakeStorage(opts = {}) {
  const map = new Map();
  return {
    map,
    fail: opts.fail || null,
    getItem(k) { return map.has(k) ? map.get(k) : null; },
    setItem(k, v) {
      if (this.fail && this.fail(String(v))) {
        const e = new Error('QuotaExceededError');
        e.name = 'QuotaExceededError';
        throw e;
      }
      map.set(k, String(v));
    },
    removeItem(k) { map.delete(k); },
  };
}

function clock(start = 1000) {
  let t = start;
  return () => (t += 1000);
}

const u = (s) => ({ role: 'user', content: s });
const a = (s) => ({ role: 'assistant', content: s });

test('a saved conversation can be listed and loaded back', () => {
  const s = createStore('ns', fakeStorage(), clock());
  s.save('c1', [u('how do I map external IDs?'), a('Use an IdMap file.')]);
  assert.deepEqual(s.list().map((c) => c.id), ['c1']);
  assert.equal(s.list()[0].messageCount, 2);
  assert.equal(s.load('c1').messages[0].content, 'how do I map external IDs?');
});

test('load returns null for an unknown id', () => {
  const s = createStore('ns', fakeStorage(), clock());
  assert.equal(s.load('nope'), null);
});

test('list is newest-updated first, not creation order', () => {
  const s = createStore('ns', fakeStorage(), clock());
  s.save('a', [u('first')]);
  s.save('b', [u('second')]);
  s.save('a', [u('first'), a('x'), u('again')]);
  assert.deepEqual(s.list().map((c) => c.id), ['a', 'b']);
});

test('the title comes from the first user message and never changes', () => {
  const s = createStore('ns', fakeStorage(), clock());
  s.save('c1', [u('how do I map external IDs?')]);
  s.save('c1', [u('how do I map external IDs?'), a('Use IdMap.'), u('and ensembles?')]);
  assert.equal(s.list()[0].title, 'how do I map external IDs?');
});

test('a long title is truncated with an ellipsis', () => {
  const long = 'x'.repeat(200);
  assert.equal(titleFrom([u(long)]).length, 60);
  assert.ok(titleFrom([u(long)]).endsWith('…'));
});

test('a whitespace-only first message titles as Untitled', () => {
  assert.equal(titleFrom([u('   \n  ')]), 'Untitled');
  assert.equal(titleFrom([]), 'Untitled');
});

test('newlines in a title are collapsed to single spaces', () => {
  assert.equal(titleFrom([u('one\n\ntwo   three')]), 'one two three');
});

test('a conversation is capped to 12 messages, dropping from the front', () => {
  const many = [];
  for (let i = 0; i < 20; i++) many.push(i % 2 ? a('a' + i) : u('q' + i));
  const capped = capMessages(many);
  assert.equal(capped.length, MAX_MESSAGES);
  assert.equal(capped[0].content, 'q8');
});

test('the byte cap drops older messages', () => {
  const big = 'x'.repeat(10 * 1024);
  const capped = capMessages([u(big), a(big), u(big), a(big)]);
  const total = capped.reduce((n, m) => n + m.content.length, 0);
  assert.ok(total <= MAX_BYTES, `kept ${total} bytes`);
  assert.ok(capped.length >= 1);
});

test('a single oversized message is still kept', () => {
  const capped = capMessages([u('x'.repeat(MAX_BYTES * 2))]);
  assert.equal(capped.length, 1);
});

test('a capped conversation never begins with an assistant message', () => {
  const many = [];
  for (let i = 0; i < 20; i++) many.push(i % 2 ? u('q' + i) : a('a' + i));
  assert.equal(capMessages(many)[0].role, 'user');
});

test('malformed messages are dropped rather than stored', () => {
  const s = createStore('ns', fakeStorage(), clock());
  s.save('c1', [u('real'), { role: 'system', content: 'x' }, { role: 'user' }, null]);
  assert.deepEqual(s.load('c1').messages, [{ role: 'user', content: 'real' }]);
});

test('saving nothing usable does not create a conversation', () => {
  const s = createStore('ns', fakeStorage(), clock());
  assert.equal(s.save('c1', []), null);
  assert.equal(s.save('c1', [{ role: 'system', content: 'x' }]), null);
  assert.equal(s.list().length, 0);
});

test('a blank id is refused', () => {
  const s = createStore('ns', fakeStorage(), clock());
  assert.equal(s.save('', [u('hi')]), null);
  assert.equal(s.list().length, 0);
});

test('a 26th conversation evicts the least recently updated', () => {
  const s = createStore('ns', fakeStorage(), clock());
  for (let i = 0; i < MAX_CONVERSATIONS; i++) s.save('c' + i, [u('q' + i)]);
  // Touch the oldest-created so it is no longer the least recently updated.
  s.save('c0', [u('q0'), a('x'), u('again')]);
  s.save('new', [u('newest')]);
  const ids = s.list().map((c) => c.id);
  assert.equal(ids.length, MAX_CONVERSATIONS);
  assert.ok(ids.includes('c0'), 'recently touched c0 should survive');
  assert.ok(!ids.includes('c1'), 'c1 was least recently updated and should go');
});

test('remove deletes one conversation and leaves the rest', () => {
  const s = createStore('ns', fakeStorage(), clock());
  s.save('a', [u('one')]);
  s.save('b', [u('two')]);
  s.remove('a');
  assert.deepEqual(s.list().map((c) => c.id), ['b']);
});

test('clearAll empties the list', () => {
  const s = createStore('ns', fakeStorage(), clock());
  s.save('a', [u('one')]);
  s.clearAll();
  assert.deepEqual(s.list(), []);
});

test('save re-reads, so a concurrent tab write is not clobbered', () => {
  const shared = fakeStorage();
  const tabA = createStore('ns', shared, clock(1000));
  const tabB = createStore('ns', shared, clock(50000));
  tabA.save('a', [u('from A')]);
  tabB.save('b', [u('from B')]);   // B read A's write first
  tabA.save('a', [u('from A'), a('x'), u('more')]);  // A must not drop B
  const ids = tabA.list().map((c) => c.id).sort();
  assert.deepEqual(ids, ['a', 'b']);
});

test('a conversation deleted in one tab is not resurrected by the other', () => {
  const shared = fakeStorage();
  const tabA = createStore('ns', shared, clock(1000));
  const tabB = createStore('ns', shared, clock(50000));
  tabA.save('a', [u('one')]);
  tabA.save('b', [u('two')]);
  tabB.remove('a');
  tabA.save('b', [u('two'), a('x'), u('three')]);
  assert.deepEqual(tabA.list().map((c) => c.id), ['b']);
});

test('two namespaces never see each other', () => {
  const shared = fakeStorage();
  const alice = createStore('alice-key', shared, clock());
  const bob = createStore('bob-key', shared, clock());
  alice.save('a', [u('alice question')]);
  assert.deepEqual(bob.list(), []);
  bob.save('b', [u('bob question')]);
  assert.deepEqual(alice.list().map((c) => c.id), ['a']);
});

test('a missing namespace degrades to memory, never a shared key', () => {
  const shared = fakeStorage();
  const one = createStore('', shared, clock());
  const two = createStore(null, shared, clock());
  one.save('a', [u('secret')]);
  assert.deepEqual(two.list(), [], 'separate memory, not a shared constant key');
  for (const k of shared.map.keys()) {
    assert.ok(!k.includes('memory'), `wrote to backing storage under ${k}`);
  }
});

test('a throwing storage handle degrades to memory instead of propagating', () => {
  const hostile = {
    getItem() { throw new Error('denied'); },
    setItem() { throw new Error('denied'); },
    removeItem() { throw new Error('denied'); },
  };
  const s = createStore('ns', hostile, clock());
  assert.doesNotThrow(() => s.save('a', [u('hi')]));
  assert.deepEqual(s.list().map((c) => c.id), ['a']);
});

test('a null storage handle degrades to memory', () => {
  const s = createStore('ns', null, clock());
  s.save('a', [u('hi')]);
  assert.deepEqual(s.list().map((c) => c.id), ['a']);
});

test('quota failure evicts the oldest and retries once', () => {
  const storage = fakeStorage();
  const s = createStore('ns', storage, clock());
  s.save('a', [u('one')]);
  s.save('b', [u('two')]);
  s.save('c', [u('three')]);
  // Reject a write holding four conversations; the three-item retry succeeds.
  storage.fail = (v) => (v.match(/"id":/g) || []).length >= 4;
  s.save('d', [u('four')]);
  const ids = s.list().map((c) => c.id);
  assert.equal(ids.length, 3, 'the retry wrote a shorter list');
  assert.ok(ids.includes('d'), 'the new conversation survived');
  assert.ok(!ids.includes('a'), 'the least recently updated was evicted');
});

test('a hopeless quota failure gives up silently', () => {
  const storage = fakeStorage({ fail: () => true });
  const s = createStore('ns', storage, clock());
  assert.doesNotThrow(() => s.save('a', [u('one')]));
});

test('when the retry also fails, nothing is written and nothing throws', () => {
  const storage = fakeStorage();
  const s = createStore('ns', storage, clock());
  s.save('a', [u('one')]);
  s.save('b', [u('two')]);
  storage.fail = () => true;
  assert.doesNotThrow(() => s.save('c', [u('three')]));
  assert.deepEqual(s.list().map((x) => x.id).sort(), ['a', 'b']);
});

test('corrupt JSON is discarded rather than thrown', () => {
  const storage = fakeStorage();
  storage.map.set(STORAGE_PREFIX + 'ns', '{not json');
  const s = createStore('ns', storage, clock());
  assert.deepEqual(s.list(), []);
  assert.doesNotThrow(() => s.save('a', [u('hi')]));
  assert.deepEqual(s.list().map((c) => c.id), ['a']);
});

test('a non-array conversations field is discarded', () => {
  const storage = fakeStorage();
  storage.map.set(STORAGE_PREFIX + 'ns', JSON.stringify({ conversations: 'nope' }));
  assert.deepEqual(createStore('ns', storage, clock()).list(), []);
});

test('malformed entries are dropped and well-formed ones survive', () => {
  const storage = fakeStorage();
  storage.map.set(STORAGE_PREFIX + 'ns', JSON.stringify({
    conversations: [
      { id: 'good', title: 't', updatedAt: 5, messages: [{ role: 'user', content: 'q' }] },
      { id: '', title: 't', updatedAt: 5, messages: [] },
      { id: 'nodate', title: 't', updatedAt: 'soon', messages: [] },
      { id: 'nomsgs', title: 't', updatedAt: 5 },
      null,
      'string',
    ],
  }));
  assert.deepEqual(createStore('ns', storage, clock()).list().map((c) => c.id), ['good']);
});

test('load returns a copy, so mutating it cannot corrupt the store', () => {
  const s = createStore('ns', fakeStorage(), clock());
  s.save('a', [u('one')]);
  s.load('a').messages.push(u('injected'));
  assert.equal(s.load('a').messages.length, 1);
});

test('newId produces distinct ids', () => {
  const ids = new Set();
  for (let i = 0; i < 500; i++) ids.add(newId());
  assert.equal(ids.size, 500);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm run test:js`
Expected: FAIL — `Cannot find module .../conversation-store.js`. The 51 existing tests still pass.

- [ ] **Step 3: Implement the store**

Create `src/scripts/conversation-store.js`:

```js
/**
 * Per-user conversation history for the chat panel.
 *
 * Pure apart from the storage handle and clock, both injected: no DOM, no
 * imports, no browser globals. That is what lets this be tested with
 * `node --test` against a fake storage, with no jsdom and no dependency, and
 * it is the seam a server-backed store would slot into later.
 */

export const STORAGE_PREFIX = 'fewsChat:v1:';
export const MAX_CONVERSATIONS = 25;
export const MAX_MESSAGES = 12;
export const MAX_BYTES = 24 * 1024;
export const MAX_TITLE = 60;

const ROLES = new Set(['user', 'assistant']);
const encoder = new TextEncoder();

export function newId() {
  return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
}

function memoryStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
  };
}

function usable(storage) {
  try {
    if (!storage) return false;
    const probe = STORAGE_PREFIX + 'probe';
    storage.setItem(probe, '1');
    storage.removeItem(probe);
    return true;
  } catch (e) {
    return false;
  }
}

function validMessage(m) {
  return Boolean(m) && typeof m === 'object'
    && ROLES.has(m.role) && typeof m.content === 'string';
}

function validRecord(c) {
  return Boolean(c) && typeof c === 'object'
    && typeof c.id === 'string' && c.id !== ''
    && typeof c.title === 'string'
    && typeof c.updatedAt === 'number' && Number.isFinite(c.updatedAt)
    && Array.isArray(c.messages) && c.messages.every(validMessage);
}

function cleanMessages(messages) {
  return (Array.isArray(messages) ? messages : [])
    .filter(validMessage)
    .map((m) => ({ role: m.role, content: m.content }));
}

export function capMessages(messages) {
  const recent = cleanMessages(messages).slice(-MAX_MESSAGES);
  const out = [];
  let total = 0;
  for (let i = recent.length - 1; i >= 0; i--) {
    const size = encoder.encode(recent[i].content).length;
    if (out.length && total + size > MAX_BYTES) break;
    total += size;
    out.unshift(recent[i]);
  }
  // Mirror chat/conversation.py: never begin with an assistant message, or a
  // restored conversation opens mid-answer with no question above it.
  while (out.length > 1 && out[0].role !== 'user') out.shift();
  return out;
}

export function titleFrom(messages) {
  const first = cleanMessages(messages).find((m) => m.role === 'user');
  const text = first ? first.content.replace(/\s+/g, ' ').trim() : '';
  if (!text) return 'Untitled';
  return text.length > MAX_TITLE ? text.slice(0, MAX_TITLE - 1) + '…' : text;
}

export function createStore(namespace, storage, now = () => Date.now()) {
  // A missing namespace must NEVER fall back to a shared constant key: on a
  // shared browser that pools every user's history into one list. Degrade to
  // memory instead — session-only, but never cross-user.
  const named = typeof namespace === 'string' && namespace !== '';
  const backing = named && usable(storage) ? storage : memoryStorage();
  const key = STORAGE_PREFIX + (named ? namespace : 'memory');

  function read() {
    let raw;
    try { raw = backing.getItem(key); } catch (e) { return []; }
    if (!raw) return [];
    let parsed;
    try { parsed = JSON.parse(raw); } catch (e) { return []; }
    const list = parsed && Array.isArray(parsed.conversations) ? parsed.conversations : [];
    return list.filter(validRecord).map((c) => ({
      id: c.id,
      title: c.title,
      updatedAt: c.updatedAt,
      messages: cleanMessages(c.messages),
    }));
  }

  function write(list) {
    const body = () => JSON.stringify({ conversations: list });
    try {
      backing.setItem(key, body());
      return true;
    } catch (e) {
      // Quota. Drop the least recently updated (last, since sorted newest
      // first) and try once more, then give up silently — a failed save must
      // never cost the answer already on screen.
      if (list.length > 1) {
        list.pop();
        try { backing.setItem(key, body()); return true; } catch (e2) { /* give up */ }
      }
      return false;
    }
  }

  function sorted(list) {
    return list.slice().sort((a, b) => b.updatedAt - a.updatedAt);
  }

  return {
    list() {
      return sorted(read()).map((c) => ({
        id: c.id, title: c.title, updatedAt: c.updatedAt,
        messageCount: c.messages.length,
      }));
    },

    load(id) {
      const found = read().find((c) => c.id === id);
      return found ? { ...found, messages: found.messages.slice() } : null;
    },

    save(id, messages) {
      if (typeof id !== 'string' || id === '') return null;
      const capped = capMessages(messages);
      if (!capped.length) return null;
      // Re-read before writing. Writing back an in-memory copy lets a second
      // open tab resurrect a conversation deleted in the first, or drop one
      // the first just created.
      const list = read();
      const existing = list.find((c) => c.id === id);
      const record = {
        id,
        title: existing && existing.title ? existing.title : titleFrom(capped),
        updatedAt: now(),
        messages: capped,
      };
      const next = sorted([record, ...list.filter((c) => c.id !== id)])
        .slice(0, MAX_CONVERSATIONS);
      write(next);
      return record;
    },

    remove(id) {
      write(sorted(read().filter((c) => c.id !== id)));
    },

    clearAll() {
      try { backing.removeItem(key); } catch (e) { /* nothing to do */ }
    },
  };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm run test:js`
Expected: PASS, 82 tests (51 existing + 31 new).

- [ ] **Step 5: Commit**

```bash
git add src/scripts/conversation-store.js src/scripts/conversation-store.test.js
git commit -m "feat: add per-user conversation store with injected storage

Keeps the last 25 conversations, each capped to the same 12 messages and
24KB the server enforces. Re-reads before writing so two open tabs cannot
clobber each other, and degrades to in-memory rather than a shared key when
no namespace is available."
```

---

### Task 2: The opaque per-user storage key

**Files:**
- Create: `chat/identity.py`
- Create: `chat/tests/test_identity.py`
- Modify: `chat/routes.py` (the `status` view)

**Interfaces:**
- Consumes: `g.current_user` (the JWT `sub`), set by `require_streamflows_user`.
- Produces:
  - `chat.identity.storage_key(subject: str) -> str` — 16 lowercase hex characters
  - `GET /api/chat/status` response gains `"storage_key"` alongside `authenticated` and `available`.

- [ ] **Step 1: Write the failing tests**

Create `chat/tests/test_identity.py`:

```python
import os

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")

from chat.identity import storage_key


def test_storage_key_is_stable_for_one_subject():
    assert storage_key("alice@example.com") == storage_key("alice@example.com")


def test_storage_key_differs_between_subjects():
    assert storage_key("alice@example.com") != storage_key("bob@example.com")


def test_storage_key_does_not_leak_the_subject():
    """The subject is an email address. The browser stores this value, so it
    must not be recoverable from it by inspection."""
    key = storage_key("alice@example.com")
    assert "alice" not in key
    assert "example" not in key
    assert key != "alice@example.com"


def test_storage_key_is_short_lowercase_hex():
    key = storage_key("alice@example.com")
    assert len(key) == 16
    assert all(c in "0123456789abcdef" for c in key)
```

Append to `chat/tests/test_auth.py`:

```python
def test_status_returns_a_storage_key(client):
    resp = client.get("/api/chat/status", headers={"Cookie": f"streamflows_auth={token()}"})
    assert resp.status_code == 200
    assert isinstance(resp.get_json()["storage_key"], str)
    assert len(resp.get_json()["storage_key"]) == 16


def test_two_users_get_different_storage_keys(client):
    def key_for(sub):
        resp = client.get(
            "/api/chat/status",
            headers={"Cookie": f"streamflows_auth={token(sub=sub)}"},
        )
        return resp.get_json()["storage_key"]

    assert key_for("alice") != key_for("bob")


def test_unauthenticated_status_has_no_storage_key():
    """A 401 body must not carry a namespace an anonymous caller could adopt."""
    from chat.app import create_app

    resp = create_app().test_client().get("/api/chat/status")
    assert resp.status_code == 401
    assert "storage_key" not in resp.get_json()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `chat/venv/bin/python -m pytest chat/tests/test_identity.py chat/tests/test_auth.py -q`
Expected: FAIL — `No module named 'chat.identity'`.

`test_auth.py` already defines the `client` fixture at line 24 and the `token(groups=..., sub=...)` helper at line 18, so the appended tests use both directly — do not add a second fixture.

- [ ] **Step 3: Implement the key**

Create `chat/identity.py`:

```python
"""Derive an opaque, stable per-user key for the browser's conversation store.

The browser needs to namespace stored conversations per user, but it cannot
read the httponly session cookie, so the server has to supply an identifier.
The `sub` claim is an email address and must not end up in localStorage, hence
a digest rather than the subject itself.
"""
import hashlib
import os


def storage_key(subject: str) -> str:
    """First 16 hex characters of sha256(JWT_SECRET + subject).

    Salted with the service secret so the value cannot be precomputed from a
    guessed address, and truncated because 64 bits is far more than enough to
    separate a handful of users inside one browser.
    """
    secret = os.environ["JWT_SECRET"]
    digest = hashlib.sha256((secret + subject).encode("utf-8")).hexdigest()
    return digest[:16]
```

In `chat/routes.py`, add the import alongside the existing ones:

```python
from chat.identity import storage_key
```

and replace the body of `status`:

```python
@chat_bp.route("/api/chat/status")
@require_streamflows_user
def status():
    budget = current_app.config["BUDGET"]
    return jsonify({
        "authenticated": True,
        "available": not budget.exhausted(),
        # Opaque per-user namespace for the browser's conversation store. Never
        # the subject itself — that is an email address and would land in
        # localStorage.
        "storage_key": storage_key(g.current_user),
    })
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `chat/venv/bin/python -m pytest chat/tests -q`
Expected: PASS, 136 tests (129 existing + 7 new).

- [ ] **Step 5: Commit**

```bash
git add chat/identity.py chat/tests/test_identity.py chat/tests/test_auth.py chat/routes.py
git commit -m "feat: return an opaque per-user storage key from the status endpoint

The browser cannot read the httponly session cookie, so the server supplies
a namespace for the conversation store. A salted digest rather than the sub
claim, which is an email address and must not reach localStorage."
```

---

### Task 3: Persist and restore the current conversation

Wire the store in. No list UI yet — this task alone makes navigation stop destroying conversations, which is the core of the feature.

**Files:**
- Modify: `src/components/ChatPanel.astro` (the `<script>`)

**Interfaces:**
- Consumes: `createStore`, `newId` from `../scripts/conversation-store.js`; `storage_key` from `/api/chat/status`.
- Produces: module-scope `store`, `currentId`, and functions `startNewConversation()`, `openConversation(id)`, `persist()` used by Task 4's list UI.

- [ ] **Step 1: Import and add the state**

At the head of the `<script>`, alongside the existing `parseAnswer` import:

```js
  import { createStore, newId } from '../scripts/conversation-store.js';
```

Below the existing `let inFlight = false;` add:

```js
  /* Replaced once /api/chat/status supplies the per-user namespace. Until then
     this is an in-memory store: the panel behaves exactly as it did before,
     and nothing is ever written under a shared key. */
  let store = createStore('', null);
  let currentId = null;
```

- [ ] **Step 2: Add the render, persist and open helpers**

Add these functions after `addTurn`:

```js
  /* Rebuild the log from a stored conversation. Titles and message text are
     model-adjacent user input, so they go in through the same textContent and
     block-builder paths as a live answer — never as markup. */
  function renderConversation(messages) {
    log.textContent = '';
    for (const m of messages) {
      if (m.role === 'user') {
        addTurn('user', m.content);
      } else {
        const el = addTurn('assistant', '');
        paintAnswer(el, m.content);
      }
    }
    log.scrollTop = log.scrollHeight;
  }

  /* Best effort, always. A storage failure must never cost the user the answer
     already on screen, so this swallows everything. */
  function persist() {
    if (!currentId || !messages.length) return;
    try { store.save(currentId, messages); } catch (e) {}
    renderHistory();
  }

  function startNewConversation() {
    currentId = newId();
    messages.length = 0;
    log.textContent = '';
    renderHistory();
    if (signedIn) input.focus();
  }

  function openConversation(id) {
    if (inFlight) return;
    const record = store.load(id);
    if (!record) { renderHistory(); return; }
    currentId = record.id;
    messages.length = 0;
    for (const m of record.messages) messages.push({ role: m.role, content: m.content });
    renderConversation(record.messages);
    renderHistory();
  }

  /* Restore the most recent conversation, or start a fresh one. Called once the
     namespace is known. */
  function restore() {
    const recent = store.list();
    if (recent.length) openConversation(recent[0].id);
    else startNewConversation();
  }
```

`renderHistory` is defined in Task 4. Add this stub now so Task 3 runs standalone, and replace it in Task 4:

```js
  function renderHistory() {}
```

- [ ] **Step 3: Adopt the namespace when status arrives**

In `checkStatus`, after `signedIn = true;` and `form.hidden = false;`, and before the `if (!data.available)` branch, insert:

```js
      /* A blank or missing key must leave the in-memory store in place. Falling
         back to a constant would pool every user of a shared browser into one
         history. */
      if (typeof data.storage_key === 'string' && data.storage_key) {
        let backing = null;
        try { backing = window.localStorage; } catch (e) {}
        store = createStore(data.storage_key, backing);
      }
      restore();
```

Because `restore()` repaints the log, move the two `addTurn('notice', ...)` calls in that branch so they run *after* it — otherwise the restored conversation wipes the notice. Concretely, the tail of the success path becomes:

```js
      if (typeof data.storage_key === 'string' && data.storage_key) {
        let backing = null;
        try { backing = window.localStorage; } catch (e) {}
        store = createStore(data.storage_key, backing);
      }
      restore();
      if (!data.available) {
        addTurn('notice',
          'The assistant has reached its daily limit and is resting until tomorrow.');
      } else if (!messages.length) {
        addTurn('notice', 'Ask anything about configuring Delft-FEWS.');
      }
```

The `!messages.length` guard keeps the "Ask anything" prompt from appearing above a restored conversation.

- [ ] **Step 4: Save after each completed turn**

In `send()`, the tail currently reads:

```js
    if (answer) flushAnswer(answerEl);
    if (answer && !errored) messages.push({ role: 'assistant', content: answer });
    else if (errored) dropTurn();
```

Make it:

```js
    if (answer) flushAnswer(answerEl);
    if (answer && !errored) {
      messages.push({ role: 'assistant', content: answer });
      persist();
    } else if (errored) {
      dropTurn();
      persist();
    }
```

Persisting on the error path too keeps storage consistent with `messages` after a failed turn is rolled back.

Also give the failed-fetch and non-ok branches a `persist()` after their `dropTurn()`, for the same reason:

```js
      dropTurn();
      persist();
```

- [ ] **Step 5: Ensure a conversation id exists before the first send**

In the submit handler, immediately after `if (!q || !signedIn || inFlight) return;` add:

```js
    if (!currentId) currentId = newId();
```

- [ ] **Step 6: Verify the HTML-sink invariant still holds**

```bash
grep -nE 'innerHTML|outerHTML|insertAdjacentHTML' src/components/ChatPanel.astro src/scripts/conversation-store.js src/scripts/answer-markdown.js
```

Expected: **no output.** Any hit is a stop-and-report.

- [ ] **Step 7: Build and run both suites**

```bash
npm run build
npm run test:js
chat/venv/bin/python -m pytest chat/tests -q
```

Expected: 54 pages; 82 JS tests; 136 Python tests.

- [ ] **Step 8: Commit**

```bash
git add src/components/ChatPanel.astro
git commit -m "feat: persist and restore the current chat conversation

The panel remounts on every page navigation, so the transcript died on any
click. It is now saved after each completed turn under the per-user
namespace from /api/chat/status, and restored when the panel loads."
```

---

### Task 4: The Recent conversations list

**Files:**
- Modify: `src/components/ChatPanel.astro` (template, `<style is:global>`, `<script>`)

**Interfaces:**
- Consumes: `store`, `currentId`, `startNewConversation()`, `openConversation(id)` from Task 3.
- Produces: `renderHistory()` replacing Task 3's stub.

- [ ] **Step 1: Add the markup**

In the template, between the `<header>` and `<div id="fews-chat-log">`, insert:

```astro
  <div id="fews-chat-history" hidden>
    <div class="history-bar">
      <button id="fews-chat-recent" type="button" aria-expanded="false"
        aria-controls="fews-chat-list">Recent</button>
      <button id="fews-chat-new" type="button">New</button>
    </div>
    <ul id="fews-chat-list" hidden></ul>
  </div>
```

- [ ] **Step 2: Add the styles**

Append to the `<style is:global>` block:

```css
  #fews-chat-history { border-bottom: 1px solid var(--sl-color-gray-5); }
  #fews-chat-history .history-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0.4rem 1rem;
  }
  #fews-chat-history .history-bar button {
    background: none; border: 0; cursor: pointer; padding: 0.2rem 0.4rem;
    color: var(--sl-color-text-accent); font-size: var(--sl-text-xs);
  }
  #fews-chat-list {
    list-style: none; margin: 0; padding: 0 0 0.4rem;
    max-height: 40vh; overflow-y: auto;
  }
  #fews-chat-list li {
    display: flex; align-items: center; gap: 0.4rem; padding: 0 0.6rem 0 1rem;
  }
  #fews-chat-list .open {
    flex: 1; min-width: 0; text-align: left; cursor: pointer;
    background: none; border: 0; padding: 0.3rem 0;
    color: inherit; font-size: var(--sl-text-xs);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #fews-chat-list li.current .open { font-weight: 700; }
  #fews-chat-list .when { color: var(--sl-color-gray-3); font-size: var(--sl-text-xs); }
  #fews-chat-list .delete {
    background: none; border: 0; cursor: pointer; padding: 0.2rem;
    color: var(--sl-color-gray-3); font-size: var(--sl-text-xs);
  }
  #fews-chat-clear {
    display: block; width: calc(100% - 2rem); margin: 0 1rem 0.5rem;
    background: none; border: 0; cursor: pointer; padding: 0.3rem 0;
    text-align: left; color: var(--sl-color-gray-3); font-size: var(--sl-text-xs);
  }
```

- [ ] **Step 3: Replace the `renderHistory` stub**

Delete `function renderHistory() {}` and add, near the other builders:

```js
  const historyPanel = document.getElementById('fews-chat-history');
  const recentBtn = document.getElementById('fews-chat-recent');
  const newBtn = document.getElementById('fews-chat-new');
  const listEl = document.getElementById('fews-chat-list');

  function relativeTime(ms) {
    const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (seconds < 60) return 'just now';
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + 'm';
    const hours = Math.round(minutes / 60);
    if (hours < 24) return hours + 'h';
    return Math.round(hours / 24) + 'd';
  }

  /* Titles are the user's own words echoed back, so they go in through
     textContent like everything else — never as markup. */
  function renderHistory() {
    const items = store.list();
    historyPanel.hidden = !signedIn;
    listEl.textContent = '';

    for (const item of items) {
      const li = document.createElement('li');
      if (item.id === currentId) li.className = 'current';

      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'open';
      open.textContent = item.title;
      open.title = item.title;
      open.addEventListener('click', () => openConversation(item.id));

      const when = document.createElement('span');
      when.className = 'when';
      when.textContent = relativeTime(item.updatedAt);

      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'delete';
      del.textContent = '×';
      del.setAttribute('aria-label', 'Delete this conversation');
      del.addEventListener('click', () => {
        store.remove(item.id);
        /* Deleting the open conversation must not leave its turns on screen
           with nowhere to save them. */
        if (item.id === currentId) startNewConversation();
        else renderHistory();
      });

      li.appendChild(open);
      li.appendChild(when);
      li.appendChild(del);
      listEl.appendChild(li);
    }

    if (items.length) {
      const clear = document.createElement('button');
      clear.type = 'button';
      clear.id = 'fews-chat-clear';
      clear.textContent = 'Clear all conversations';
      clear.addEventListener('click', () => {
        store.clearAll();
        startNewConversation();
      });
      listEl.appendChild(clear);
    }

    recentBtn.textContent = items.length ? `Recent (${items.length})` : 'Recent';
    recentBtn.disabled = !items.length;
    if (!items.length) {
      listEl.hidden = true;
      recentBtn.setAttribute('aria-expanded', 'false');
    }
  }
```

- [ ] **Step 4: Wire the two header buttons**

Add alongside the existing `toggle` and `closeBtn` listeners:

```js
  recentBtn.addEventListener('click', () => {
    const nowOpen = listEl.hidden;
    listEl.hidden = !nowOpen;
    recentBtn.setAttribute('aria-expanded', String(nowOpen));
  });
  newBtn.addEventListener('click', () => {
    if (inFlight) return;
    startNewConversation();
  });
```

- [ ] **Step 5: Verify the HTML-sink invariant**

```bash
grep -nE 'innerHTML|outerHTML|insertAdjacentHTML' src/components/ChatPanel.astro src/scripts/conversation-store.js src/scripts/answer-markdown.js
```

Expected: **no output.**

- [ ] **Step 6: Confirm the new rules are unscoped**

```bash
npm run build
grep -o '#fews-chat-list[^{]*{[^}]*}' dist/_astro/*.css | head -3
```

Expected: rules present with **no** `:where(.astro-` on them. A scoped rule is dead for these script-populated elements — that is the bug this panel already had once.

- [ ] **Step 7: Run both suites**

```bash
npm run test:js
chat/venv/bin/python -m pytest chat/tests -q
```

Expected: 82 JS, 136 Python.

- [ ] **Step 8: Commit**

```bash
git add src/components/ChatPanel.astro
git commit -m "feat: add a Recent conversations list to the chat panel

Lists the stored conversations newest first with per-item delete, a New
button and a clear-all control. Titles go in through textContent like every
other piece of model-adjacent text."
```

---

### Task 5: Deploy and verify

**Files:** none. Deployment only.

**Interfaces:** Consumes Tasks 1-4, merged to `main`.

- [ ] **Step 1: Run everything**

```bash
npm run test:js
chat/venv/bin/python -m pytest chat/tests -q
npm run build
```

Expected: 82, 136, 54 pages. Do not deploy on a failure — report it.

- [ ] **Step 2: Merge and push**

```bash
git checkout main && git merge --no-ff <branch> && git push origin main
```

- [ ] **Step 3: Pull and rebuild on the deploy clone**

```bash
sudo -u fewsdocs git -C /home/fewsdocs/repo pull
sudo -u fewsdocs bash -c 'cd /home/fewsdocs/repo && npm ci && npm run build'
```

Expected: 54 pages.

- [ ] **Step 4: Restart the service**

`/api/chat/status` changed, so the running service must be replaced.

```bash
sudo systemctl restart fewsdocs-chat
sudo systemctl is-active fewsdocs-chat
```

Expected: `active`. `PERSONA` is untouched, so the prompt cache survives and the next question does not pay a cold write.

- [ ] **Step 5: Confirm the gate still holds**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://df-docs.streamflows.org/api/chat \
  -H 'Content-Type: application/json' -d '{"messages":[]}'
curl -s https://df-docs.streamflows.org/api/chat/status -w ' [%{http_code}]\n'
```

Expected: `401` from both, and the 401 body must **not** contain `storage_key`. Anything else is a stop-and-report — the auth gate matters more than the feature.

- [ ] **Step 6: Verify the behaviour live**

Signed in, confirm all six:

1. Ask a question, then click a documentation link in the answer. The conversation is still there after the page loads.
2. Reload the page. Still there.
3. Press **New**. The log clears and "Recent" shows the previous conversation.
4. Reopen the previous conversation from the list. Its turns come back, with code blocks and tables rendered, not as raw Markdown.
5. Delete a conversation from the list; it goes, and the rest remain.
6. In DevTools, Application → Local Storage: the key is `fewsChat:v1:<16 hex>` and **no email address appears anywhere in it**.

---

## Self-Review

**Spec coverage.** Store interface, injected storage and clock → Task 1. Storage format, titles, ids, the 12-message/24 KB caps, the leading-assistant trim, 25-conversation eviction, concurrency re-read, and every failure-handling clause → Task 1's tests and implementation. `storage_key` derivation and the status endpoint → Task 2. Namespace adoption, the never-a-shared-constant rule, restore-on-load and save-after-turn → Task 3. Recent list, New, per-item delete, clear-all → Task 4. Deployment and the restart requirement → Task 5. Testing section → Tasks 1 and 2. No uncovered requirement.

**Placeholder scan.** Task 3 introduces `function renderHistory() {}` deliberately and Task 4 replaces it; both tasks say so explicitly, so the stub is a sequencing device, not a placeholder. Nothing else defers.

**Type consistency.** `createStore(namespace, storage, now)` and the five method names are identical in Tasks 1, 3 and 4. `list()` returns `messageCount`, used only in Task 1's tests. `newId()` is used in Tasks 3 and 5. `storage_key` is the same name as the Python function, the JSON field, and the `data.storage_key` read in Task 3. `paintAnswer` and `addTurn` in Task 3 are existing functions in `ChatPanel.astro`, not new ones. `renderHistory()` takes no arguments in both its stub and its real definition.

**Test counts.** 51 existing JS + 31 new = 82. 129 existing Python + 4 in `test_identity.py` + 3 appended to `test_auth.py` = 136.

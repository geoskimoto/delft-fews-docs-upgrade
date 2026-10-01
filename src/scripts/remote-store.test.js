import test from 'node:test';
import assert from 'node:assert/strict';
import { createRemoteStore } from './remote-store.js';
import { createStore, capMessages, MAX_MESSAGES } from './conversation-store.js';

/* ---------- helpers ---------- */

function fakeStorage() {
  const map = new Map();
  return {
    map,
    getItem(k) { return map.has(k) ? map.get(k) : null; },
    setItem(k, v) { map.set(k, String(v)); },
    removeItem(k) { map.delete(k); },
  };
}

function clock(start = 1000) {
  let t = start;
  return () => (t += 1000);
}

const u = (s) => ({ role: 'user', content: s });
const a = (s) => ({ role: 'assistant', content: s });

const BASE = '/api/chat/conversations';
const NAMESPACE = 'secret-namespace-7f3a';

/**
 * In-test server as an injected fetchFn. Records every call and implements a
 * small in-memory version of the contract. `behave` overrides one route kind
 * ('list' | 'load' | 'save' | 'remove' | 'clearAll' | 'import'):
 *   'network'            fetchFn rejects
 *   'throw'              fetchFn throws synchronously
 *   'badjson'            json() rejects
 *   { status, body }     canned response
 *   function(call)       returns any of the above
 */
function makeServer() {
  const convs = new Map();
  const calls = [];
  const behave = {};
  let tick = 5000;

  function classify(method, url) {
    if (url === BASE) return method === 'GET' ? 'list' : method === 'DELETE' ? 'clearAll' : 'other';
    if (url === BASE + '/import') return method === 'POST' ? 'import' : 'other';
    if (url.startsWith(BASE + '/')) {
      return { GET: 'load', PUT: 'save', DELETE: 'remove' }[method] || 'other';
    }
    return 'other';
  }

  function respond(status, body) {
    return { ok: status >= 200 && status < 300, status, json: async () => body };
  }

  function defaults(kind, call) {
    const seg = call.url.slice(BASE.length + 1);
    const id = kind === 'import' ? null : decodeURIComponent(seg);
    switch (kind) {
      case 'list':
        return respond(200, {
          conversations: [...convs.values()].map((c) => ({
            id: c.id, title: c.title, updatedAt: c.updatedAt, messageCount: c.messages.length,
          })),
        });
      case 'load':
        return convs.has(id) ? respond(200, { conversation: convs.get(id) }) : respond(404, { error: 'not found' });
      case 'save': {
        const rec = {
          id, title: (call.body.messages[0] || {}).content || 'Untitled',
          updatedAt: (tick += 1000), messages: call.body.messages,
        };
        convs.set(id, rec);
        return respond(200, { conversation: rec });
      }
      case 'remove': convs.delete(id); return respond(200, { ok: true });
      case 'clearAll': convs.clear(); return respond(200, { ok: true });
      case 'import': {
        const n = call.body.conversations.length;
        for (const c of call.body.conversations) convs.set(c.id, c);
        return respond(200, { imported: n, skipped: 0 });
      }
      default: return respond(404, {});
    }
  }

  async function fetchFn(url, init = {}) {
    const method = String(init.method || 'GET').toUpperCase();
    const kind = classify(method, url);
    let body = null;
    if (init.body !== undefined) body = JSON.parse(init.body);
    const call = {
      url, method, kind, body, rawBody: init.body,
      headers: init.headers || {}, credentials: init.credentials,
    };
    calls.push(call);
    let b = behave[kind];
    if (typeof b === 'function') b = b(call);
    if (b === 'throw') throw new Error('sync boom');
    if (b === 'network') throw new Error('network down');
    if (b === 'badjson') {
      return { ok: true, status: 200, json: async () => { throw new SyntaxError('bad json'); } };
    }
    if (b && typeof b === 'object') return respond(b.status, b.body);
    return defaults(kind, call);
  }

  // A fetchFn that throws synchronously (not an async rejection) when armed.
  function syncThrowingFetch(url, init) {
    if (behave.syncThrow) throw new Error('sync boom');
    return fetchFn(url, init);
  }

  return { convs, calls, behave, fetchFn, syncThrowingFetch, respond,
    of: (kind) => calls.filter((c) => c.kind === kind) };
}

function setup(opts = {}) {
  const server = makeServer();
  const storage = fakeStorage();
  const local = createStore(NAMESPACE, storage, clock());
  const store = createRemoteStore(opts.sync ? server.syncThrowingFetch : server.fetchFn, local);
  return { server, storage, local, store };
}

function storageSnapshot(storage) {
  return JSON.stringify([...storage.map.entries()]);
}

/* ---------- happy paths ---------- */

test('degraded starts false', () => {
  const { store } = setup();
  assert.equal(store.degraded, false);
});

test('methods are async (return promises)', () => {
  const { store } = setup();
  for (const p of [store.list(), store.load('x'), store.save('x', [u('q')]),
    store.remove('x'), store.clearAll(), store.importLocal()]) {
    assert.ok(p && typeof p.then === 'function');
  }
});

test('list GETs the collection and returns the conversations array as given', async () => {
  const { store, server } = setup();
  server.behave.list = { status: 200, body: { conversations: [
    { id: 'c1', title: 'T', updatedAt: 9, messageCount: 2 },
  ] } };
  const out = await store.list();
  assert.deepEqual(out, [{ id: 'c1', title: 'T', updatedAt: 9, messageCount: 2 }]);
  const [c] = server.calls;
  assert.equal(c.url, BASE);
  assert.equal(c.method, 'GET');
  assert.equal(c.credentials, 'same-origin');
  assert.equal(store.degraded, false);
});

test('save PUTs capped messages as JSON and returns the server record', async () => {
  const { store, server } = setup();
  const rec = await store.save('c1', [u('hello'), a('hi')]);
  const [c] = server.calls;
  assert.equal(c.method, 'PUT');
  assert.equal(c.url, BASE + '/c1');
  assert.equal(c.credentials, 'same-origin');
  assert.equal(c.headers['Content-Type'], 'application/json');
  assert.deepEqual(c.body, { messages: [u('hello'), a('hi')] });
  assert.equal(rec.id, 'c1');
  assert.equal(rec.updatedAt, server.convs.get('c1').updatedAt);
  assert.deepEqual(rec.messages, [u('hello'), a('hi')]);
  assert.equal(store.degraded, false);
});

test('save returns exactly what the server returned', async () => {
  const { store, server } = setup();
  const canned = { id: 'c1', title: 'Server title', updatedAt: 424242, messages: [u('q')] };
  server.behave.save = { status: 200, body: { conversation: canned } };
  assert.deepEqual(await store.save('c1', [u('q')]), canned);
});

test('save caps messages client-side before sending', async () => {
  const { store, server } = setup();
  const msgs = [];
  for (let i = 0; i < 20; i++) { msgs.push(u('q' + i)); msgs.push(a('a' + i)); }
  assert.equal(msgs.length, 40);
  await store.save('c1', msgs);
  const sent = server.of('save')[0].body.messages;
  assert.ok(sent.length <= MAX_MESSAGES);
  assert.equal(sent[0].role, 'user');
  assert.deepEqual(sent, capMessages(msgs));
  assert.deepEqual(sent.at(-1), a('a19'));
});

test('save strips extra message fields and invalid messages like capMessages does', async () => {
  const { store, server } = setup();
  await store.save('c1', [{ role: 'user', content: 'q', extra: 1 }, { role: 'system', content: 'x' }, a('r')]);
  assert.deepEqual(server.of('save')[0].body.messages, [u('q'), a('r')]);
});

test('save with nothing valid after capping returns null with no network call', async () => {
  const { store, server } = setup();
  assert.equal(await store.save('c1', []), null);
  assert.equal(await store.save('c1', [a('orphan answer')]), null);
  assert.equal(await store.save('c1', [{ role: 'x', content: 1 }]), null);
  assert.equal(await store.save('c1', null), null);
  assert.equal(server.calls.length, 0);
  assert.equal(store.degraded, false);
});

test('save with an empty or non-string id returns null with no network call', async () => {
  const { store, server } = setup();
  assert.equal(await store.save('', [u('q')]), null);
  assert.equal(await store.save(undefined, [u('q')]), null);
  assert.equal(await store.save(42, [u('q')]), null);
  assert.equal(await store.save(null, [u('q')]), null);
  assert.equal(server.calls.length, 0);
});

test('invalid save does not touch the local store either', async () => {
  const { store, local } = setup();
  await store.save('', [u('q')]);
  await store.save('c1', []);
  assert.deepEqual(local.list(), []);
});

test('load GETs the encoded id and returns the conversation', async () => {
  const { store, server } = setup();
  await store.save('c1', [u('q'), a('r')]);
  server.calls.length = 0;
  const conv = await store.load('c1');
  assert.equal(conv.id, 'c1');
  assert.deepEqual(conv.messages, [u('q'), a('r')]);
  const [c] = server.calls;
  assert.equal(c.method, 'GET');
  assert.equal(c.url, BASE + '/c1');
  assert.equal(c.credentials, 'same-origin');
});

test('remove DELETEs the encoded id', async () => {
  const { store, server } = setup();
  await store.save('c1', [u('q')]);
  server.calls.length = 0;
  await store.remove('c1');
  const [c] = server.calls;
  assert.equal(c.method, 'DELETE');
  assert.equal(c.url, BASE + '/c1');
  assert.equal(c.credentials, 'same-origin');
  assert.equal(server.convs.has('c1'), false);
  assert.equal(store.degraded, false);
});

test('clearAll DELETEs the collection', async () => {
  const { store, server } = setup();
  await store.save('c1', [u('q')]);
  await store.save('c2', [u('q')]);
  server.calls.length = 0;
  await store.clearAll();
  const [c] = server.calls;
  assert.equal(c.method, 'DELETE');
  assert.equal(c.url, BASE);
  assert.equal(c.credentials, 'same-origin');
  assert.equal(server.convs.size, 0);
  assert.equal(store.degraded, false);
});

test('a round trip through the server works end to end', async () => {
  const { store } = setup();
  await store.save('c1', [u('one'), a('r1')]);
  await store.save('c2', [u('two')]);
  const list = await store.list();
  assert.deepEqual(list.map((c) => c.id).sort(), ['c1', 'c2']);
  await store.remove('c1');
  assert.deepEqual((await store.list()).map((c) => c.id), ['c2']);
});

test('remote success never writes to the local store', async () => {
  const { store, storage } = setup();
  await store.save('c1', [u('q'), a('r')]);
  await store.list();
  await store.load('c1');
  await store.remove('c1');
  assert.equal(storage.map.size, 0);
});

/* ---------- URL encoding ---------- */

test('ids with special characters are percent-encoded in the path', async () => {
  const { store, server } = setup();
  const id = 'a/b?c#d';
  await store.save(id, [u('q')]);
  await store.load(id);
  await store.remove(id);
  const urls = server.calls.map((c) => c.url);
  assert.deepEqual(urls, [
    BASE + '/' + encodeURIComponent(id),
    BASE + '/' + encodeURIComponent(id),
    BASE + '/' + encodeURIComponent(id),
  ]);
  for (const url of urls) {
    const rest = url.slice(BASE.length + 1);
    assert.ok(!/[/?#]/.test(rest), 'raw delimiter leaked: ' + url);
  }
});

test('unicode and space ids are encoded and round trip', async () => {
  const { store, server } = setup();
  for (const id of ['café ☕', '日本語', 'with space', '100%', 'a+b&c=d']) {
    await store.save(id, [u('q')]);
    const url = server.of('save').at(-1).url;
    assert.equal(url, BASE + '/' + encodeURIComponent(id));
    const conv = await store.load(id);
    assert.equal(conv.id, id);
  }
});

test('an id that looks like "import" is still addressed as an item, encoded', async () => {
  const { store, server } = setup();
  await store.load('import');
  assert.equal(server.calls[0].method, 'GET');
  assert.equal(server.calls[0].url, BASE + '/import');
});

/* ---------- load 404 vs failure ---------- */

test('load 404 returns null, is not degraded, and does not consult local', async () => {
  const { store, server, local } = setup();
  local.save('c1', [u('only local'), a('yes')]);
  const out = await store.load('c1');
  assert.equal(out, null);
  assert.equal(store.degraded, false);
  assert.equal(server.of('load').length, 1);
});

test('load 404 after being degraded clears degraded (404 is a healthy answer)', async () => {
  const { store, server } = setup();
  server.behave.list = 'network';
  await store.list();
  assert.equal(store.degraded, true);
  server.behave.list = undefined;
  assert.equal(await store.load('missing'), null);
  assert.equal(store.degraded, false);
});

test('load 500 falls back to local and degrades', async () => {
  const { store, server, local } = setup();
  local.save('c1', [u('local q'), a('local a')]);
  server.behave.load = { status: 500, body: {} };
  const out = await store.load('c1');
  assert.deepEqual(out, local.load('c1'));
  assert.equal(store.degraded, true);
});

test('load 500 for an id absent locally returns null (local answer) and degrades', async () => {
  const { store, server } = setup();
  server.behave.load = { status: 503, body: {} };
  assert.equal(await store.load('ghost'), null);
  assert.equal(store.degraded, true);
});

test('load with a malformed 200 body falls back', async () => {
  for (const body of [{}, { conversation: null }, { conversation: 'x' }, null, [], { conversation: { id: 'c1' } }]) {
    const { store, server, local } = setup();
    local.save('c1', [u('q')]);
    server.behave.load = { status: 200, body };
    const out = await store.load('c1');
    assert.deepEqual(out, local.load('c1'), JSON.stringify(body));
    assert.equal(store.degraded, true);
  }
});

/* ---------- fallback behaviour ---------- */

for (const failure of [
  ['network error', 'network'],
  ['500', { status: 500, body: {} }],
  ['502', { status: 502, body: 'bad gateway' }],
  ['401', { status: 401, body: { error: 'auth' } }],
  ['403', { status: 403, body: { error: 'forbidden' } }],
  ['400', { status: 400, body: { error: 'bad' } }],
  ['429', { status: 429, body: {} }],
  ['json() rejects', 'badjson'],
]) {
  const [label, mode] = failure;

  test(`list falls back to the local list on ${label}`, async () => {
    const { store, server, local } = setup();
    local.save('l1', [u('first')]);
    local.save('l2', [u('second')]);
    server.behave.list = mode;
    const out = await store.list();
    assert.deepEqual(out, local.list());
    assert.equal(out.length, 2);
    assert.equal(store.degraded, true);
  });

  test(`save falls back to the local store on ${label} and returns the local record`, async () => {
    const { store, server, local } = setup();
    server.behave.save = mode;
    const rec = await store.save('c1', [u('q'), a('answer on screen')]);
    assert.ok(rec, 'the answer on screen must not be lost');
    assert.equal(rec.id, 'c1');
    assert.deepEqual(rec, local.load('c1'));
    assert.equal(store.degraded, true);
  });

  test(`remove falls back to local on ${label}`, async () => {
    const { store, server, local } = setup();
    local.save('c1', [u('q')]);
    server.behave.remove = mode;
    await store.remove('c1');
    assert.equal(local.load('c1'), null);
    assert.equal(store.degraded, true);
  });

  test(`clearAll falls back to local on ${label}`, async () => {
    const { store, server, local } = setup();
    local.save('c1', [u('q')]);
    local.save('c2', [u('q')]);
    server.behave.clearAll = mode;
    await store.clearAll();
    assert.deepEqual(local.list(), []);
    assert.equal(store.degraded, true);
  });

  test(`methods never reject on ${label}`, async () => {
    const { store, server } = setup();
    for (const k of ['list', 'load', 'save', 'remove', 'clearAll']) server.behave[k] = mode;
    await store.list();
    await store.load('c1');
    await store.save('c1', [u('q')]);
    await store.remove('c1');
    await store.clearAll();
    await store.importLocal();
  });
}

test('a synchronously throwing fetchFn is treated as failure for every method', async () => {
  const { store, server, local } = setup({ sync: true });
  server.behave.syncThrow = true;
  local.save('c1', [u('q'), a('r')]);
  assert.deepEqual(await store.list(), local.list());
  assert.deepEqual(await store.load('c1'), local.load('c1'));
  const rec = await store.save('c2', [u('new')]);
  assert.deepEqual(rec, local.load('c2'));
  await store.remove('c1');
  assert.equal(local.load('c1'), null);
  await store.clearAll();
  assert.deepEqual(local.list(), []);
  assert.equal(store.degraded, true);
});

test('list with a non-array conversations body is a failure', async () => {
  for (const body of [{ conversations: 'nope' }, { conversations: {} }, {}, null, 'text', []]) {
    const { store, server, local } = setup();
    local.save('l1', [u('x')]);
    server.behave.list = { status: 200, body };
    const out = await store.list();
    assert.deepEqual(out, local.list(), JSON.stringify(body));
    assert.equal(store.degraded, true);
  }
});

test('save with a malformed 200 body falls back to local', async () => {
  for (const body of [{}, null, { conversation: null }, { conversation: 'x' }]) {
    const { store, server, local } = setup();
    server.behave.save = { status: 200, body };
    const rec = await store.save('c1', [u('q')]);
    assert.deepEqual(rec, local.load('c1'), JSON.stringify(body));
    assert.equal(store.degraded, true);
  }
});

test('fallback save applies the local cap too (never begins with an assistant)', async () => {
  const { store, server, local } = setup();
  server.behave.save = 'network';
  const msgs = [];
  for (let i = 0; i < 20; i++) { msgs.push(u('q' + i)); msgs.push(a('a' + i)); }
  const rec = await store.save('c1', msgs);
  assert.ok(rec.messages.length <= MAX_MESSAGES);
  assert.equal(rec.messages[0].role, 'user');
  assert.deepEqual(local.load('c1').messages, rec.messages);
});

test('fallback does not pollute degraded-free reads: load 404 is not served from local', async () => {
  const { store, server, local } = setup();
  local.save('c1', [u('local only')]);
  // Server healthy, says 404.
  assert.equal(await store.load('c1'), null);
  assert.equal(server.of('load').length, 1);
});

/* ---------- degraded transitions ---------- */

test('degraded becomes true on failure and false again after a later full success', async () => {
  const { store, server } = setup();
  assert.equal(store.degraded, false);
  server.behave.list = 'network';
  await store.list();
  assert.equal(store.degraded, true);
  await store.list();
  assert.equal(store.degraded, true);
  server.behave.list = undefined;
  await store.list();
  assert.equal(store.degraded, false);
});

test('degraded recovers via any successful operation kind', async () => {
  for (const op of [
    (s) => s.list(), (s) => s.save('x', [u('q')]), (s) => s.remove('x'),
    (s) => s.clearAll(), (s) => s.load('x'),
  ]) {
    const { store, server } = setup();
    server.behave.list = 'network';
    await store.list();
    assert.equal(store.degraded, true);
    server.behave.list = undefined;
    await op(store);
    assert.equal(store.degraded, false);
  }
});

test('a failing call after recovery re-degrades', async () => {
  const { store, server } = setup();
  server.behave.save = 'network';
  await store.save('c1', [u('q')]);
  assert.equal(store.degraded, true);
  server.behave.save = undefined;
  await store.save('c1', [u('q')]);
  assert.equal(store.degraded, false);
  server.behave.save = { status: 500, body: {} };
  await store.save('c1', [u('q')]);
  assert.equal(store.degraded, true);
});

test('an invalid save (no network call) does not change degraded', async () => {
  const { store, server } = setup();
  server.behave.list = 'network';
  await store.list();
  assert.equal(store.degraded, true);
  await store.save('', [u('q')]);
  assert.equal(store.degraded, true);
});

test('local fallback writes are not mirrored to the server on later success', async () => {
  const { store, server, local } = setup();
  server.behave.save = 'network';
  await store.save('offline', [u('typed while down'), a('answer')]);
  assert.ok(local.load('offline'));
  server.behave.save = undefined;
  server.calls.length = 0;
  await store.list();
  await store.save('online', [u('later')]);
  await store.load('online');
  assert.equal(server.convs.has('offline'), false);
  const puts = server.of('save');
  assert.deepEqual(puts.map((c) => c.url), [BASE + '/online']);
  assert.equal(server.of('import').length, 0);
  // and the local copy is left alone
  assert.ok(local.load('offline'));
});

test('remote success after degradation does not push or clear the local store', async () => {
  const { store, server, local } = setup();
  local.save('l1', [u('x')]);
  server.behave.list = 'network';
  await store.list();
  server.behave.list = undefined;
  await store.list();
  assert.ok(local.load('l1'));
  assert.equal(server.of('import').length, 0);
});

/* ---------- no globals ---------- */

test('uses only the injected fetchFn, never a global fetch or localStorage', async () => {
  const realFetch = globalThis.fetch;
  const hadLocal = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  let globalUsed = 0;
  globalThis.fetch = () => { globalUsed++; throw new Error('global fetch used'); };
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    get() { globalUsed++; throw new Error('global localStorage used'); },
  });
  try {
    const { store, server, local } = setup();
    local.save('l1', [u('x')]);
    await store.list();
    await store.save('c1', [u('q')]);
    await store.load('c1');
    server.behave.list = 'network';
    await store.list();
    await store.importLocal();
    await store.remove('c1');
    await store.clearAll();
    assert.equal(globalUsed, 0);
    assert.ok(server.calls.length > 0);
  } finally {
    globalThis.fetch = realFetch;
    if (hadLocal) Object.defineProperty(globalThis, 'localStorage', hadLocal);
    else delete globalThis.localStorage;
  }
});

/* ---------- importLocal ---------- */

function seedLocal(local, n) {
  for (let i = 0; i < n; i++) {
    local.save('c' + String(i).padStart(2, '0'), [u('question ' + i), a('answer ' + i)]);
  }
}

test('importLocal with an empty local store resolves null with no network call', async () => {
  const { store, server } = setup();
  assert.equal(await store.importLocal(), null);
  assert.equal(server.calls.length, 0);
  assert.equal(store.degraded, false);
});

test('importLocal POSTs full records and clears local on success', async () => {
  const { store, server, local, storage } = setup();
  seedLocal(local, 3);
  const expected = local.list().map((c) => local.load(c.id));
  const result = await store.importLocal();
  assert.deepEqual(result, { imported: 3, skipped: 0 });
  const imports = server.of('import');
  assert.equal(imports.length, 1);
  const c = imports[0];
  assert.equal(c.method, 'POST');
  assert.equal(c.url, BASE + '/import');
  assert.equal(c.credentials, 'same-origin');
  assert.equal(c.headers['Content-Type'], 'application/json');
  assert.deepEqual(Object.keys(c.body), ['conversations']);
  assert.equal(c.body.conversations.length, 3);
  for (const rec of c.body.conversations) {
    assert.deepEqual(Object.keys(rec).sort(), ['id', 'messages', 'title', 'updatedAt']);
  }
  assert.deepEqual(
    c.body.conversations.slice().sort((x, y) => x.id.localeCompare(y.id)),
    expected.slice().sort((x, y) => x.id.localeCompare(y.id)),
  );
  assert.deepEqual(local.list(), []);
  assert.equal(storage.map.size, 0);
  assert.equal(store.degraded, false);
});

test('importLocal returns the server-reported imported/skipped counts', async () => {
  const { store, server, local } = setup();
  seedLocal(local, 2);
  server.behave.import = { status: 200, body: { imported: 1, skipped: 1 } };
  assert.deepEqual(await store.importLocal(), { imported: 1, skipped: 1 });
});

test('importLocal sends only the 15 newest conversations by updatedAt', async () => {
  const { store, server, local } = setup();
  seedLocal(local, 22); // c00 oldest ... c21 newest
  const newest15 = local.list().slice(0, 15).map((c) => c.id);
  assert.equal(newest15.length, 15);
  await store.importLocal();
  const sent = server.of('import')[0].body.conversations.map((c) => c.id);
  assert.equal(sent.length, 15);
  assert.deepEqual(sent.slice().sort(), newest15.slice().sort());
  assert.ok(sent.includes('c21'));
  assert.ok(!sent.includes('c00'));
  assert.ok(!sent.includes('c06'));
});

test('importLocal picks newest by updatedAt, not by insertion order', async () => {
  const { store, server, local } = setup();
  seedLocal(local, 16);
  // re-save the oldest so it becomes the newest
  local.save('c00', [u('question 0'), a('answer 0'), u('again')]);
  await store.importLocal();
  const sent = server.of('import')[0].body.conversations.map((c) => c.id);
  assert.equal(sent.length, 15);
  assert.ok(sent.includes('c00'));
  assert.ok(!sent.includes('c01'));
});

test('importLocal does not clear local before the request has succeeded', async () => {
  const { store, server, local } = setup();
  seedLocal(local, 2);
  let localAtRequestTime = null;
  server.behave.import = () => {
    localAtRequestTime = local.list().length;
    return { status: 200, body: { imported: 2, skipped: 0 } };
  };
  await store.importLocal();
  assert.equal(localAtRequestTime, 2);
  assert.equal(local.list().length, 0);
});

for (const [label, mode] of [
  ['network error', 'network'],
  ['500', { status: 500, body: {} }],
  ['401', { status: 401, body: {} }],
  ['403', { status: 403, body: {} }],
  ['400', { status: 400, body: {} }],
  ['413', { status: 413, body: {} }],
  ['json() rejecting', 'badjson'],
  ['a 200 with a non-object body', { status: 200, body: null }],
  ['a 200 with wrong-shaped body', { status: 200, body: { imported: 'many' } }],
]) {
  test(`importLocal failure (${label}) leaves local untouched, resolves null, not degraded`, async () => {
    const { store, server, local, storage } = setup();
    seedLocal(local, 3);
    const before = storageSnapshot(storage);
    server.behave.import = mode;
    const out = await store.importLocal();
    assert.equal(out, null);
    assert.equal(storageSnapshot(storage), before);
    assert.equal(local.list().length, 3);
    assert.equal(store.degraded, false);
  });
}

test('importLocal failure via synchronously throwing fetchFn resolves null', async () => {
  const { store, server, local } = setup({ sync: true });
  seedLocal(local, 2);
  server.behave.syncThrow = true;
  assert.equal(await store.importLocal(), null);
  assert.equal(local.list().length, 2);
  assert.equal(store.degraded, false);
});

test('importLocal can be retried after a failure and then succeeds', async () => {
  const { store, server, local } = setup();
  seedLocal(local, 2);
  server.behave.import = 'network';
  assert.equal(await store.importLocal(), null);
  server.behave.import = undefined;
  assert.deepEqual(await store.importLocal(), { imported: 2, skipped: 0 });
  assert.equal(server.of('import').length, 2);
  assert.deepEqual(local.list(), []);
});

test('importLocal after success makes no second request', async () => {
  const { store, server, local } = setup();
  seedLocal(local, 2);
  await store.importLocal();
  assert.equal(server.of('import').length, 1);
  assert.equal(await store.importLocal(), null);
  assert.equal(server.of('import').length, 1);
});

test('concurrent importLocal calls issue at most one request', async () => {
  const { store, server, local } = setup();
  seedLocal(local, 3);
  const [r1, r2, r3] = await Promise.all([
    store.importLocal(), store.importLocal(), store.importLocal(),
  ]);
  assert.equal(server.of('import').length, 1);
  assert.deepEqual(r1, { imported: 3, skipped: 0 });
  assert.deepEqual(r2, r1);
  assert.deepEqual(r3, r1);
});

test('concurrent importLocal calls share a slow in-flight request', async () => {
  const server = makeServer();
  const local = createStore(NAMESPACE, fakeStorage(), clock());
  seedLocal(local, 2);
  let release;
  const gate = new Promise((r) => { release = r; });
  const slowFetch = async (url, init) => { await gate; return server.fetchFn(url, init); };
  const store = createRemoteStore(slowFetch, local);
  const p1 = store.importLocal();
  const p2 = store.importLocal();
  release();
  const [r1, r2] = await Promise.all([p1, p2]);
  assert.equal(server.of('import').length, 1);
  assert.deepEqual(r1, r2);
  assert.deepEqual(r1, { imported: 2, skipped: 0 });
});

test('concurrent failing importLocal calls share one request and all resolve null', async () => {
  const { store, server, local } = setup();
  seedLocal(local, 2);
  server.behave.import = 'network';
  const out = await Promise.all([store.importLocal(), store.importLocal()]);
  assert.deepEqual(out, [null, null]);
  assert.equal(server.of('import').length, 1);
  assert.equal(local.list().length, 2);
});

/* ---------- identity ---------- */

test('no request ever carries a namespace, user key, or email-like field', async () => {
  const { store, server, local } = setup();
  seedLocal(local, 2);
  await store.list();
  await store.save('c1', [u('q'), a('r')]);
  await store.load('c1');
  await store.importLocal();
  await store.remove('c1');
  await store.clearAll();
  server.behave.list = 'network';
  await store.list();
  assert.ok(server.calls.length >= 6);

  const allowedHeaders = new Set(['content-type', 'accept']);
  const allowedBodyKeys = new Set(['messages', 'conversations', 'id', 'title', 'updatedAt']);
  for (const c of server.calls) {
    assert.ok(!c.url.includes(NAMESPACE), 'namespace in url: ' + c.url);
    assert.ok(!/@/.test(decodeURIComponent(c.url)), 'email-like in url');
    assert.ok(!/[?&](user|uid|email|key|ns|namespace|token)=/i.test(c.url));
    if (c.rawBody !== undefined) {
      assert.ok(!c.rawBody.includes(NAMESPACE), 'namespace in body');
      assert.ok(!/@/.test(c.rawBody), 'email-like in body');
      const keysOf = (o) => Object.keys(o);
      const topKeys = keysOf(c.body);
      for (const k of topKeys) assert.ok(allowedBodyKeys.has(k), 'unexpected body key ' + k);
      for (const rec of c.body.conversations || []) {
        for (const k of keysOf(rec)) assert.ok(allowedBodyKeys.has(k), 'unexpected record key ' + k);
      }
    }
    for (const h of Object.keys(c.headers)) {
      assert.ok(allowedHeaders.has(h.toLowerCase()), 'unexpected header ' + h);
    }
    assert.equal(c.credentials, 'same-origin');
  }
});

test('every request, including GET and DELETE, sends credentials same-origin', async () => {
  const { store, server, local } = setup();
  seedLocal(local, 1);
  await store.list();
  await store.save('c1', [u('q')]);
  await store.load('c1');
  await store.load('missing');
  await store.importLocal();
  await store.remove('c1');
  await store.clearAll();
  assert.equal(server.calls.length, 7);
  for (const c of server.calls) assert.equal(c.credentials, 'same-origin', c.method + ' ' + c.url);
});

test('every URL is same-origin relative (starts with /api/chat/conversations)', async () => {
  const { store, server, local } = setup();
  seedLocal(local, 1);
  await store.list();
  await store.save('x/y', [u('q')]);
  await store.importLocal();
  await store.clearAll();
  for (const c of server.calls) {
    assert.ok(c.url.startsWith('/api/chat/conversations'), c.url);
    assert.ok(!/^[a-z]+:\/\//i.test(c.url));
  }
});

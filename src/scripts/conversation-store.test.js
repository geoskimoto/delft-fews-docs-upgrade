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

test('a namespace equal to the probe sentinel is not wiped on reopen', () => {
  const shared = fakeStorage();
  createStore('probe', shared, clock()).save('c1', [u('real conversation data')]);
  // A second createStore is what every page load does.
  const reopened = createStore('probe', shared, clock(9000));
  assert.deepEqual(reopened.list().map((c) => c.id), ['c1']);
});

test('an answer larger than the byte budget keeps its question above it', () => {
  const capped = capMessages([u('short q'), a('y'.repeat(MAX_BYTES * 2))]);
  assert.equal(capped.length, 2);
  assert.equal(capped[0].role, 'user');
  assert.equal(capped[0].content, 'short q');
});

test('a lone assistant message with no question before it is dropped', () => {
  assert.deepEqual(capMessages([a('y'.repeat(MAX_BYTES * 2))]), []);
});

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
    // Read-only on purpose. A destructive probe writing a sentinel key
    // collides with the real key of a user whose namespace equals that
    // sentinel, wiping their history on every page load. write() already
    // catches a storage that refuses writes, so detecting that lazily costs
    // nothing.
    storage.getItem(STORAGE_PREFIX + 'probe');
    return typeof storage.setItem === 'function'
      && typeof storage.removeItem === 'function';
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
  // Never begin mid-answer. Drop leading assistant turns; if that would empty
  // the list — one answer larger than the whole byte budget — pull its question
  // back in instead, over budget, rather than storing a reply with no question
  // above it or losing the exchange altogether.
  while (out.length > 1 && out[0].role !== 'user') out.shift();
  if (out.length === 1 && out[0].role !== 'user') {
    const idx = recent.lastIndexOf(out[0]);
    const question = idx > 0 ? recent[idx - 1] : null;
    if (question && question.role === 'user') out.unshift(question);
    else out.shift();
  }
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

/**
 * Server-backed conversation history for the chat panel.
 *
 * Mirrors the local store's methods but async, talking to /api/chat/conversations.
 * Whoever the user is comes only from the session cookie — nothing here sends a
 * user key. When the server is unusable every operation falls back to the
 * injected local (browser) store, so a failed save never costs the answer on
 * screen. Pure apart from the injected fetch: no DOM, no globals.
 */
import { capMessages } from './conversation-store.js';

const BASE = '/api/chat/conversations';
const IMPORT_LIMIT = 15;

const isObject = (v) => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
const isRecord = (v) => isObject(v) && typeof v.id === 'string' && Array.isArray(v.messages);

export function createRemoteStore(fetchFn, localStore) {
  let degraded = false;
  let importing = null;

  /* Resolves {status, body} for a response whose JSON parsed, or null when the
     request failed in any way. Never rejects. */
  async function call(path, method, payload) {
    const init = { method, credentials: 'same-origin' };
    if (payload !== undefined) {
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(payload);
    }
    try {
      const resp = await fetchFn(BASE + path, init);
      if (!resp) return null;
      if (resp.status === 404 && !resp.ok) return { status: 404, body: null };
      if (!resp.ok) return null;
      return { status: resp.status, body: await resp.json() };
    } catch (e) {
      return null;
    }
  }

  const id = (value) => (typeof value === 'string' && value !== ''
    ? '/' + encodeURIComponent(value) : null);

  /* Run the remote attempt; fall back to local when it yields no usable result. */
  async function attempt(remote, fallback) {
    const result = await remote();
    if (result !== undefined) {
      degraded = false;
      return result;
    }
    degraded = true;
    return fallback();
  }

  return {
    get degraded() { return degraded; },

    list() {
      return attempt(async () => {
        const r = await call('', 'GET');
        return r && isObject(r.body) && Array.isArray(r.body.conversations)
          ? r.body.conversations : undefined;
      }, () => localStore.list());
    },

    load(convId) {
      const path = id(convId);
      if (!path) return Promise.resolve(null);
      return attempt(async () => {
        const r = await call(path, 'GET');
        if (r && r.status === 404) return null;
        return r && isObject(r.body) && isRecord(r.body.conversation)
          ? r.body.conversation : undefined;
      }, () => localStore.load(convId));
    },

    async save(convId, messages) {
      const path = id(convId);
      const capped = capMessages(messages);
      if (!path || !capped.length) return null;
      return attempt(async () => {
        const r = await call(path, 'PUT', { messages: capped });
        return r && isObject(r.body) && isRecord(r.body.conversation)
          ? r.body.conversation : undefined;
      }, () => localStore.save(convId, messages));
    },

    remove(convId) {
      const path = id(convId);
      if (!path) return Promise.resolve();
      return attempt(async () => {
        const r = await call(path, 'DELETE');
        return r ? null : undefined;
      }, () => localStore.remove(convId));
    },

    clearAll() {
      return attempt(async () => {
        const r = await call('', 'DELETE');
        return r ? null : undefined;
      }, () => localStore.clearAll());
    },

    /* One-time move of what this browser already holds. The emptied local store
       is the marker, so it is cleared only after the server confirms. Failure
       leaves it untouched for a later retry and does not mark the store
       degraded. */
    importLocal() {
      if (importing) return importing;
      importing = (async () => {
        const items = localStore.list().slice(0, IMPORT_LIMIT);
        if (!items.length) return null;
        const conversations = items
          .map((item) => localStore.load(item.id))
          .filter(Boolean)
          .map((c) => ({
            id: c.id, title: c.title, updatedAt: c.updatedAt, messages: c.messages,
          }));
        const r = await call('/import', 'POST', { conversations });
        if (!r || !isObject(r.body) || typeof r.body.imported !== 'number') return null;
        localStore.clearAll();
        return { imported: r.body.imported, skipped: r.body.skipped };
      })().finally(() => { importing = null; });
      return importing;
    },
  };
}

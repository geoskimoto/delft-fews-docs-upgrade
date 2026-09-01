# Chat conversation history

The chat panel loses its conversation on every page navigation. This spec covers
why, and what replaces it.

## The problem

`ChatPanel.astro` mounts through the Starlight `Footer` override, so it is
re-created on every page load. The transcript lives in a `messages` array in the
page's script scope, and only `fewsChatOpen` is persisted. Following a link the
assistant just cited therefore destroys the conversation that produced it.

Nothing survives a reload either, so a question asked yesterday cannot be
referred back to.

## Goals

- A conversation survives navigation, reload, closing the tab, and rebooting.
- Earlier conversations can be listed, reopened and deleted.
- No conversation content is stored or logged server-side.
- No personally identifying information is written to the browser's storage.
- No new runtime dependency; no JavaScript build pipeline.

## Non-goals

- Cross-device sync. Storage is per-browser. A server-backed store is a possible
  Phase 2 and carries privacy decisions deliberately deferred here.
- Search over past conversations. Twenty-five items scan fine in the panel.
- Renaming conversations, pinning, or export.

## Architecture

### `src/scripts/conversation-store.js`

One exported factory, `createStore(namespace, storage) -> Store`, with five
methods:

```js
Store = {
  list(),             // [{id, title, updatedAt, messageCount}], newest first
  load(id),           // {id, title, updatedAt, messages} | null
  save(id, messages), // upsert, evict, returns the stored record
  remove(id),
  clearAll(),
}
```

The `storage` handle is injected rather than reaching for `window.localStorage`.
That is what lets the whole module be tested with the Node 22 built-in runner
against a Map-backed fake — no jsdom, no test framework, no dependency. It is
also the Phase 2 seam: a `createRemoteStore` exposing the same five methods
substitutes without the panel changing.

The module imports nothing and references no browser global.

### `ChatPanel.astro`

Owns the UI and the wiring. It holds the current conversation id, calls
`save()` after a turn completes, and calls `load()` when the panel mounts or a
conversation is chosen from the list.

## Identifying the user

The `streamflows_auth` cookie is httponly, so the browser cannot read who is
signed in. `GET /api/chat/status` supplies it.

Returning the raw `sub` claim would put the user's email address into
localStorage. Instead the endpoint returns an opaque `storage_key`: the first 16
hex characters of `sha256(JWT_SECRET + sub)`. Stable per user, not reversible,
and cheap.

This is the only server-side change. The chat service continues to store no
conversation content and to log counts only.

The key namespaces the browser's storage, so a second person signing in on the
same browser gets their own empty list rather than the first person's history.
Namespacing hides the earlier data; it does not remove it, which is why the
panel offers an explicit "Clear all conversations".

## Storage format

One localStorage key per user, `fewsChat:v1:<storage_key>`, holding every
conversation for that user:

```json
{
  "conversations": [
    {
      "id": "l4k2j9-3f",
      "title": "mapping external IDs",
      "updatedAt": 1756700000000,
      "messages": [{ "role": "user", "content": "..." }]
    }
  ]
}
```

A single key keeps a save to one read and one write, and makes eviction a list
operation rather than a scan.

**Titles** come from the first user message: newlines collapsed, trimmed to 60
characters, with an ellipsis if truncated. A title is set once and never
regenerated. If the first message is somehow empty, the title is "Untitled".

**Ids** are generated in the browser and only have to be unique within one
user's list. `Date.now().toString(36)` plus six random base-36 characters is
sufficient; ids are never shown to the user and carry no meaning.

## Limits

**Per conversation: 12 messages and 24 KB**, mirroring `MAX_HISTORY_TURNS` and
`MAX_HISTORY_BYTES` in `chat/config.py`. Note that `MAX_HISTORY_TURNS` is
applied to a flat message list in `chat/conversation.py`, so it is twelve
messages — six exchanges — not twelve exchanges.

Applied on `save()`, dropping from the front, then dropping any leading
assistant message so the stored list begins with a user message. That last step
mirrors the server's own trim and keeps a restored conversation from opening
mid-answer.

This keeps what the reader sees identical to what the model could still see, so
a resumed conversation never appears to contain context the assistant has
actually lost.

**Overall: 25 conversations**, evicting the least recently updated when a 26th
is saved. Worst case is roughly 1 MB — the 24 KB per-conversation budget plus
the allowance for one over-budget exchange when a single answer exceeds it,
around 40 KB per conversation — against a per-origin budget of about 5 MB, so
this can never crowd out anything else the site stores.

## Concurrency

`save()` re-reads the stored blob and merges the target conversation into it by
id before writing, rather than writing a copy held in memory.

Without this, two open tabs clobber one another: tab B's save would write back
its own stale view of the list, resurrecting a conversation deleted in tab A or
dropping one tab A created. Re-reading makes concurrent tabs safe for distinct
conversations. Two tabs editing the *same* conversation still resolve
last-writer-wins, which is acceptable — the panel is single-flight, so this
requires deliberate effort to provoke.

## Failure handling

Every failure degrades to today's behaviour rather than breaking the chat.

- **Storage unavailable** (private browsing, disabled, or throwing on access):
  `createStore` falls back to an in-memory Map. The panel works exactly as it
  does now — session-only, no error shown, nothing lost that was not already
  being lost.
- **`QuotaExceededError` on write:** evict the oldest conversation and retry
  once. If it still fails, give up silently. A failed save must never remove the
  answer already on screen or interrupt a stream.
- **Corrupt or hand-edited JSON:** discard the unreadable entry and continue.
  `list()` and `load()` validate the shape of everything they read — an entry
  without a string `id`, a numeric `updatedAt`, or a well-formed `messages`
  array is dropped, not repaired.
- **`storage_key` absent** (an older service build, or a status response without
  it): fall back to in-memory. Never fall back to a shared constant namespace,
  which would pool every user of a shared browser into one history.

The store never trusts its own storage.

## UI

A collapsible "Recent" section at the top of the panel, above the log:

- A **New** button starting an empty conversation.
- The list, newest first, showing each title and a relative timestamp, with a
  delete control per row.
- A **Clear all conversations** control.

Deleting the conversation currently open clears the panel and starts a new one.
Selecting a conversation while a reply is streaming is refused by the existing
single-flight guard, which already disables input during a stream.

## Testing

Per the repository's agent-testing constraints, tests are written before
implementation and by a separate agent invocation from the one that implements
the code. Once written they are a locked specification.

`conversation-store.js` runs under `npm run test:js` against an injected fake
storage. Coverage targets the failure modes, not the happy path:

- Eviction at 26 conversations removes the least recently updated, not the
  oldest created.
- The 12-message and 24 KB caps both apply, dropping from the front, and a
  single oversized message does not defeat them.
- After truncation the stored list never begins with an assistant message.
- A title is set from the first user message and never changes afterwards.
- `save()` merges against a concurrently modified blob: a conversation added by
  "another tab" between read and write survives.
- `QuotaExceededError` triggers one eviction and retry, then fails silently.
- Corrupt JSON, a non-array `conversations`, and malformed entries are discarded
  without throwing.
- A throwing storage handle degrades to in-memory rather than propagating.
- Two different namespaces never see each other's conversations.

`storage_key` gets pytest coverage in the existing suite: stable across calls
for one subject, different for different subjects, never equal to the raw
subject, and present in the `/api/chat/status` payload only for an authorised
caller.

The panel wiring is verified by hand, as with the Markdown renderer.

## Deployment

Front-end changes ship through `npm run build`. The `/api/chat/status` change
requires `systemctl restart fewsdocs-chat`. `PERSONA` is untouched, so the
prompt cache is not invalidated and the next question does not pay a cold write.

# Chat Answer Formatting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render assistant answers with real paragraphs, headings, lists, code blocks and tables instead of one collapsed line.

**Architecture:** A pure `parseAnswer(text) -> Block[]` parser in `src/scripts/answer-markdown.js` that touches no DOM and has no imports, plus a thin DOM builder inside `src/components/ChatPanel.astro` that walks the block array with `createElement`. The split exists so the parser — where all the logic and all the risk live — is testable with the Node 22 built-in test runner and zero dependencies.

**Tech Stack:** Vanilla ES modules, Astro 5 component `<script>`, `node --test` (built in to Node 22). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-31-chat-answer-formatting-design.md`

## Global Constraints

- **No new runtime or dev dependency.** `package.json` gains a script, never a package.
- **No `innerHTML`, `outerHTML`, or `insertAdjacentHTML` anywhere.** Every node is built with `createElement` / `createTextNode`. This is the property that makes model output structurally unable to become HTML; it is not negotiable.
- **Anchor hrefs are allowlisted to `https://df-docs.streamflows.org`.** An allowlist, never a `javascript:` denylist.
- The parser module imports nothing and references no browser global (`document`, `window`, `navigator`). If it does, it stops being testable without a DOM.
- **Never silently drop model output.** An unrecognised construct renders as literal text; a malformed table row renders its cells rather than being discarded.
- Tests in this plan are a locked specification. Do not edit a test file to make an implementation pass. If a test looks wrong, stop and report it.
- The chat panel is 420px wide. Wide content scrolls inside its own box; the panel itself must never scroll horizontally.
- Node is v22.23.1. Use the glob form `node --test "src/scripts/*.test.js"`, **not** the directory form `node --test src/scripts/` — the directory form treats `answer-markdown.js` itself as a test file and fails. Do not add a test framework.
- The parser and its 45 tests in this plan were executed before the plan was committed: all 45 pass as written. A failure means the code was transcribed incorrectly, not that the test is wrong.

---

### Task 1: Revive the dead turn styles

The `.turn` rules in `ChatPanel.astro` are compiled by Astro to `.turn:where(.astro-sy3qanz7)`. Every `.turn` element is created at runtime by `addTurn()`, so it never carries that class and none of the rules apply — which is why answers collapse to one line. Moving the block to `<style is:global>` is what makes every later task visible at all.

**Files:**
- Modify: `src/components/ChatPanel.astro:27-64` (the `<style>` block)

**Interfaces:**
- Consumes: nothing.
- Produces: a global `#fews-chat` style block that later tasks extend with code-block and table rules.

- [ ] **Step 1: Confirm the defect in the built CSS**

```bash
npm run build >/dev/null 2>&1
grep -o '#fews-chat-log[^{]*\.turn[^{]*{[^}]*}' dist/_astro/*.css
```

Expected: output containing `.turn:where(.astro-` — the scoped selector that never matches. Record it; Step 4 asserts it is gone.

- [ ] **Step 2: Split the style block in two**

`ChatPanel.astro` keeps its existing scoped `<style>` for elements that appear in the template (`#fews-chat-toggle`, `#fews-chat`, `header`, `h2`, `#fews-chat-close`, `#fews-chat-log`, `#fews-chat-form` and its children, the media query). Remove only these four rules from it:

```css
  #fews-chat-log .turn { margin-bottom: 1rem; white-space: pre-wrap; }
  #fews-chat-log .turn.user { color: var(--sl-color-white); font-weight: 600; }
  #fews-chat-log .turn.notice { color: var(--sl-color-orange-high); }
  #fews-chat-log .turn a { color: var(--sl-color-text-accent); }
```

Add a second, global style block immediately after the existing one:

```astro
<!-- Turn elements are created by the script below, so they never carry Astro's
     scoping class and scoped rules cannot reach them. Every selector here is
     rooted at #fews-chat, so going global leaks nothing to the rest of the
     site. -->
<style is:global>
  #fews-chat-log .turn { margin-bottom: 1rem; }
  /* pre-wrap only where the text is inserted verbatim. Assistant answers are
     built from block elements, which carry their own spacing. */
  #fews-chat-log .turn.user,
  #fews-chat-log .turn.notice { white-space: pre-wrap; }
  #fews-chat-log .turn.user { color: var(--sl-color-white); font-weight: 600; }
  #fews-chat-log .turn.notice { color: var(--sl-color-orange-high); }
  #fews-chat-log .turn a { color: var(--sl-color-text-accent); }
</style>
```

- [ ] **Step 3: Rebuild**

Run: `npm run build`
Expected: build succeeds, 54 pages.

- [ ] **Step 4: Verify the rules are now unscoped**

```bash
grep -o '#fews-chat-log \.turn[^{]*{[^}]*}' dist/_astro/*.css
```

Expected: rules appear with **no** `:where(.astro-` on `.turn`. If `:where(.astro-` is still attached to `.turn`, `is:global` was not applied — fix before continuing.

- [ ] **Step 5: Commit**

```bash
git add src/components/ChatPanel.astro
git commit -m "fix: apply chat turn styles to script-created elements

Astro scoped .turn to .astro-<hash>, which turn divs built by addTurn()
never carry, so white-space: pre-wrap and the turn colours were dead and
answers collapsed onto one line."
```

---

### Task 2: Test harness and the inline span parser

`parseInline` converts one run of text into spans. Blocks are built on top of it in Task 3, so it comes first.

**Files:**
- Create: `src/scripts/answer-markdown.js`
- Create: `src/scripts/answer-markdown.test.js`
- Modify: `package.json` (scripts)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `export function parseInline(text: string): Span[]`
  - `Span = {type:'text', text} | {type:'code', text} | {type:'strong', text} | {type:'link', text, href}`
  - `export const DOC_ORIGIN = 'https://df-docs.streamflows.org'`
  - Adjacent `text` spans are always merged; the parser never emits two in a row.

- [ ] **Step 1: Add the test script**

In `package.json`, add to `"scripts"`:

```json
    "test:js": "node --test \"src/scripts/*.test.js\"",
```

The glob is deliberate. `node --test src/scripts/` picks up `answer-markdown.js` as well and reports it as a failing test with no tests in it.

- [ ] **Step 2: Write the failing tests**

Create `src/scripts/answer-markdown.test.js`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseInline, DOC_ORIGIN as DOC } from './answer-markdown.js';

test('plain text is a single text span', () => {
  assert.deepEqual(parseInline('hello world'), [
    { type: 'text', text: 'hello world' },
  ]);
});

test('empty string yields no spans', () => {
  assert.deepEqual(parseInline(''), []);
});

test('inline code becomes a code span', () => {
  assert.deepEqual(parseInline('use `<location>` here'), [
    { type: 'text', text: 'use ' },
    { type: 'code', text: '<location>' },
    { type: 'text', text: ' here' },
  ]);
});

test('bold becomes a strong span', () => {
  assert.deepEqual(parseInline('**required** field'), [
    { type: 'strong', text: 'required' },
    { type: 'text', text: ' field' },
  ]);
});

test('a markdown link to the docs origin becomes a link span', () => {
  assert.deepEqual(parseInline(`see [Locations](${DOC}/reference/locations/)`), [
    { type: 'text', text: 'see ' },
    { type: 'link', text: 'Locations', href: `${DOC}/reference/locations/` },
  ]);
});

test('a javascript: link renders as literal text', () => {
  assert.deepEqual(parseInline('[click](javascript:alert)'), [
    { type: 'text', text: '[click](javascript:alert)' },
  ]);
});

test('a markdown link to another host renders as literal text', () => {
  assert.deepEqual(parseInline('[x](https://evil.example/p)'), [
    { type: 'text', text: '[x](https://evil.example/p)' },
  ]);
});

test('a protocol-relative link renders as literal text', () => {
  assert.deepEqual(parseInline('[x](//evil.example/p)'), [
    { type: 'text', text: '[x](//evil.example/p)' },
  ]);
});

test('a host that merely starts with the doc origin is rejected', () => {
  const u = `${DOC}.evil.example/x`;
  assert.deepEqual(parseInline(u), [{ type: 'text', text: u }]);
});

test('a userinfo-style lookalike is rejected', () => {
  const u = `${DOC}@evil.example/x`;
  assert.deepEqual(parseInline(u), [{ type: 'text', text: u }]);
});

test('a bare doc URL links, and its trailing period stays text', () => {
  assert.deepEqual(parseInline(`See ${DOC}/tasks/locations/.`), [
    { type: 'text', text: 'See ' },
    { type: 'link', text: `${DOC}/tasks/locations/`, href: `${DOC}/tasks/locations/` },
    { type: 'text', text: '.' },
  ]);
});

test('a bare URL on another host stays text', () => {
  assert.deepEqual(parseInline('see https://evil.example/x'), [
    { type: 'text', text: 'see https://evil.example/x' },
  ]);
});

test('a doc URL inside inline code is not linked', () => {
  assert.deepEqual(parseInline(`\`${DOC}/x\``), [
    { type: 'code', text: `${DOC}/x` },
  ]);
});

test('unmatched ** and backtick render literally', () => {
  assert.deepEqual(parseInline('**not bold and `not code'), [
    { type: 'text', text: '**not bold and `not code' },
  ]);
});

test('adjacent text runs are merged into one span', () => {
  // The rejected link and the text after it must not become two text spans.
  assert.deepEqual(parseInline('[x](ftp://a) tail'), [
    { type: 'text', text: '[x](ftp://a) tail' },
  ]);
});

test('code spans are opaque to bold and links', () => {
  assert.deepEqual(parseInline('`**a** [b](c)`'), [
    { type: 'code', text: '**a** [b](c)' },
  ]);
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `npm run test:js`
Expected: FAIL — `Cannot find module .../answer-markdown.js`.

- [ ] **Step 4: Implement `parseInline`**

Create `src/scripts/answer-markdown.js`:

```js
/**
 * Markdown parsing for assistant answers. Pure: no DOM, no imports, no
 * globals. The DOM builder in ChatPanel.astro consumes what this returns.
 *
 * Kept pure so it can be tested with `node --test` and deep-equal assertions,
 * with no jsdom and no test framework.
 */

export const DOC_ORIGIN = 'https://df-docs.streamflows.org';

/**
 * An href is emitted only for this site's own pages. An allowlist, not a
 * `javascript:` denylist — anything unrecognised stays inert text.
 *
 * The character after the origin must be a path, query or fragment delimiter.
 * Without that check `https://df-docs.streamflows.org.evil.example/` and
 * `https://df-docs.streamflows.org@evil.example/` both pass a plain
 * startsWith.
 */
function safeHref(raw) {
  if (typeof raw !== 'string' || !raw.startsWith(DOC_ORIGIN)) return null;
  const rest = raw.slice(DOC_ORIGIN.length);
  if (rest === '' || rest[0] === '/' || rest[0] === '?' || rest[0] === '#') {
    return raw;
  }
  return null;
}

// Alternation order is precedence order. Code first, so a code span is opaque
// to everything inside it. Links before bare URLs, so `[t](url)` is not eaten
// by the bare-URL branch.
//   1 inline code   2 link text   3 link href   4 bold   5 bare URL
//
// The {0,2000} bounds are load-bearing, not decoration. Unbounded, the link
// label class consumes to end of input on a run of unmatched `[` and then
// backtracks one character at a time looking for a `]` that never arrives —
// measured at 15 seconds for 100,000 brackets, and parseInline runs on every
// streaming delta. A label or href past the bound renders as literal text
// instead of a link, which is the right trade: no real answer has one.
const INLINE = new RegExp(
  '`([^`\\n]+)`' +
    '|\\[([^\\]\\n]{0,2000})\\]\\(([^)\\s]{0,2000})\\)' +
    '|\\*\\*([^\\n]{1,2000}?)\\*\\*' +
    '|(https?://[^\\s<>()\\[\\]]+)',
  'g',
);

export function parseInline(text) {
  const spans = [];
  // Merge on push. Two adjacent text spans carry no more information than one
  // and would make every assertion depend on where a rejected match happened
  // to split the run.
  const push = (t) => {
    if (!t) return;
    const last = spans[spans.length - 1];
    if (last && last.type === 'text') last.text += t;
    else spans.push({ type: 'text', text: t });
  };

  const src = typeof text === 'string' ? text : '';
  let cursor = 0;

  for (const m of src.matchAll(INLINE)) {
    push(src.slice(cursor, m.index));
    if (m[1] !== undefined) {
      spans.push({ type: 'code', text: m[1] });
    } else if (m[2] !== undefined) {
      const href = safeHref(m[3]);
      if (href) spans.push({ type: 'link', text: m[2], href });
      else push(m[0]);
    } else if (m[4] !== undefined) {
      spans.push({ type: 'strong', text: m[4] });
    } else {
      // Prose ends sentences with the URL, so trailing punctuation the URL
      // almost certainly does not own goes back as text — otherwise every
      // cited link that closes a sentence points at a 404.
      const url = m[5].replace(/[.,;:!?]+$/, '');
      const href = safeHref(url);
      if (href) {
        spans.push({ type: 'link', text: url, href });
        push(m[5].slice(url.length));
      } else {
        push(m[5]);
      }
    }
    cursor = m.index + m[0].length;
  }
  push(src.slice(cursor));
  return spans;
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm run test:js`
Expected: PASS, 16 tests. (A 17th, added during review, guards against quadratic backtracking.)

- [ ] **Step 6: Commit**

```bash
git add package.json src/scripts/answer-markdown.js src/scripts/answer-markdown.test.js
git commit -m "feat: add pure inline Markdown span parser for chat answers

Handles inline code, bold, markdown links and bare URLs, with hrefs
allowlisted to the docs origin so a javascript: or off-site link renders as
inert text. Tested with the built-in node runner, no new dependency."
```

---

### Task 3: Block parser — headings, paragraphs, fenced code, lists

**Files:**
- Modify: `src/scripts/answer-markdown.js`
- Modify: `src/scripts/answer-markdown.test.js`

**Interfaces:**
- Consumes: `parseInline` from Task 2.
- Produces:
  - `export function parseAnswer(text: string): Block[]`
  - `Block = {type:'heading', level:1..6, spans} | {type:'paragraph', spans} | {type:'code', lang:string, text:string} | {type:'list', ordered:boolean, items:Span[][]}`
  - Task 4 adds the `table` variant. Task 5 consumes all of them.

- [ ] **Step 1: Write the failing tests**

Amend the import line at the top of `src/scripts/answer-markdown.test.js` to add `parseAnswer`:

```js
import { parseInline, parseAnswer, DOC_ORIGIN as DOC } from './answer-markdown.js';
```

Then append to the same file:

```js
test('empty and whitespace-only input yield no blocks', () => {
  assert.deepEqual(parseAnswer(''), []);
  assert.deepEqual(parseAnswer('   \n\n  \n'), []);
  assert.deepEqual(parseAnswer('\n'), []);
});

test('a run of text is one paragraph', () => {
  assert.deepEqual(parseAnswer('hello there'), [
    { type: 'paragraph', spans: [{ type: 'text', text: 'hello there' }] },
  ]);
});

test('a blank line separates paragraphs', () => {
  assert.deepEqual(parseAnswer('one\n\ntwo'), [
    { type: 'paragraph', spans: [{ type: 'text', text: 'one' }] },
    { type: 'paragraph', spans: [{ type: 'text', text: 'two' }] },
  ]);
});

test('headings carry their level', () => {
  assert.deepEqual(parseAnswer('## Locations file'), [
    { type: 'heading', level: 2, spans: [{ type: 'text', text: 'Locations file' }] },
  ]);
  assert.equal(parseAnswer('###### deep')[0].level, 6);
});

test('seven hashes is not a heading', () => {
  assert.equal(parseAnswer('####### nope')[0].type, 'paragraph');
});

test('a fenced block carries its language and exact text', () => {
  const md = '```xml\n<location id="H1">\n  <x>123</x>\n</location>\n```';
  assert.deepEqual(parseAnswer(md), [
    {
      type: 'code',
      lang: 'xml',
      text: '<location id="H1">\n  <x>123</x>\n</location>',
    },
  ]);
});

test('a fence with no language has an empty lang', () => {
  assert.deepEqual(parseAnswer('```\nplain\n```'), [
    { type: 'code', lang: '', text: 'plain' },
  ]);
});

test('an unterminated fence still renders as a code block', () => {
  // This is the streaming case: the closing fence has not arrived yet.
  assert.deepEqual(parseAnswer('```csv\na,b\n1,2'), [
    { type: 'code', lang: 'csv', text: 'a,b\n1,2' },
  ]);
});

test('an open fence with no body yet is an empty code block', () => {
  assert.deepEqual(parseAnswer('```xml'), [{ type: 'code', lang: 'xml', text: '' }]);
});

test('fence content is opaque to every other construct', () => {
  const md = '```\n# not a heading\n- not a list\n| not | a table |\n**not bold**\n```';
  const blocks = parseAnswer(md);
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, 'code');
  assert.equal(
    blocks[0].text,
    '# not a heading\n- not a list\n| not | a table |\n**not bold**',
  );
});

test('a tilde fence is recognised', () => {
  assert.deepEqual(parseAnswer('~~~xml\n<a/>\n~~~'), [
    { type: 'code', lang: 'xml', text: '<a/>' },
  ]);
});

test('a bullet list collects consecutive items', () => {
  assert.deepEqual(parseAnswer('- one\n- two'), [
    {
      type: 'list',
      ordered: false,
      items: [
        [{ type: 'text', text: 'one' }],
        [{ type: 'text', text: 'two' }],
      ],
    },
  ]);
});

test('asterisk bullets are a list, not bold', () => {
  assert.equal(parseAnswer('* one\n* two')[0].type, 'list');
});

test('a numbered list is ordered', () => {
  const blocks = parseAnswer('1. first\n2. second');
  assert.equal(blocks[0].type, 'list');
  assert.equal(blocks[0].ordered, true);
  assert.equal(blocks[0].items.length, 2);
});

test('a wrapped list item absorbs its continuation line', () => {
  assert.deepEqual(parseAnswer('- one\n  continued\n- two')[0].items[0], [
    { type: 'text', text: 'one continued' },
  ]);
});

test('a blank line ends a list', () => {
  const blocks = parseAnswer('- one\n\nafter');
  assert.equal(blocks.length, 2);
  assert.equal(blocks[0].type, 'list');
  assert.equal(blocks[1].type, 'paragraph');
});

test('list items parse inline spans', () => {
  assert.deepEqual(parseAnswer('- use `id`')[0].items[0], [
    { type: 'text', text: 'use ' },
    { type: 'code', text: 'id' },
  ]);
});

test('a paragraph joins its wrapped lines', () => {
  assert.deepEqual(parseAnswer('one\ntwo'), [
    { type: 'paragraph', spans: [{ type: 'text', text: 'one\ntwo' }] },
  ]);
});

test('growing an answer one character at a time never throws', () => {
  const md = [
    '## Locations',
    '',
    'Add a `<location>` with **id** set. See',
    `${DOC}/reference/locations/.`,
    '',
    '```xml',
    '<location id="H1">|#-*',
    '</location>',
    '```',
    '',
    '- one',
    '- two',
    '',
    '| Field | Type |',
    '| --- | --- |',
    '| id | string |',
    '',
    '[bad](javascript:alert) [nested](https://a.example/x(y))',
  ].join('\n');
  for (let i = 0; i <= md.length; i++) {
    assert.doesNotThrow(() => parseAnswer(md.slice(0, i)), `prefix length ${i}`);
  }
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm run test:js`
Expected: FAIL — `parseAnswer` is not exported.

- [ ] **Step 3: Implement the block parser**

Append to `src/scripts/answer-markdown.js`:

```js
// The capture is the whole rest of the line, not just the language token. A
// fence's info string may carry more than the language (a filename, say), and
// discarding the remainder would violate the never-silently-drop rule.
// Consumers that want only the language take the first token.
const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})[ \t]*(.*)$/;
const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*)$/;
const BULLET = /^ {0,3}[-*+][ \t]+(.*)$/;
const ORDERED = /^ {0,3}\d{1,9}[.)][ \t]+(.*)$/;
const CONTINUATION = /^[ \t]+\S/;

/**
 * Parse a whole answer into blocks.
 *
 * Called on every streaming delta with the answer so far, so it is fed
 * incomplete Markdown constantly. Two cases are deliberate rather than
 * accidental: an unterminated fence closes at end of input (so a code example
 * appears in its box as it streams, instead of arriving as prose and jumping
 * into a box when the closing fence lands), and a pipe row is a table only
 * once its delimiter row exists (Task 4). The same input therefore parses
 * differently as it grows. That is intended.
 */
export function parseAnswer(text) {
  const src = typeof text === 'string' ? text : '';
  const lines = src.split('\n');
  const blocks = [];
  let para = [];
  let i = 0;

  const flushPara = () => {
    if (!para.length) return;
    const joined = para.join('\n').trim();
    if (joined) blocks.push({ type: 'paragraph', spans: parseInline(joined) });
    para = [];
  };

  while (i < lines.length) {
    const line = lines[i];

    const fence = FENCE_OPEN.exec(line);
    if (fence) {
      flushPara();
      const marker = fence[1][0];
      const close = new RegExp(`^ {0,3}\\${marker}{${fence[1].length},}[ \\t]*$`);
      const body = [];
      i += 1;
      while (i < lines.length && !close.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      // Past the closing fence, or past the end if it never arrived. Either
      // way the block is emitted, which is what makes the streaming case work.
      if (i < lines.length) i += 1;
      blocks.push({ type: 'code', lang: fence[2].trim(), text: body.join('\n') });
      continue;
    }

    if (!line.trim()) {
      flushPara();
      i += 1;
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flushPara();
      blocks.push({
        type: 'heading',
        level: heading[1].length,
        spans: parseInline(heading[2].trim()),
      });
      i += 1;
      continue;
    }

    const bullet = BULLET.exec(line);
    const ordered = bullet ? null : ORDERED.exec(line);
    if (bullet || ordered) {
      flushPara();
      const isOrdered = Boolean(ordered);
      const raw = [];
      while (i < lines.length) {
        const m = isOrdered ? ORDERED.exec(lines[i]) : BULLET.exec(lines[i]);
        if (m) {
          raw.push(m[1]);
          i += 1;
          continue;
        }
        // Lazy continuation: an indented, non-blank line that is not itself a
        // marker belongs to the item above. Without this a wrapped bullet
        // silently becomes a separate paragraph mid-list.
        if (raw.length && CONTINUATION.test(lines[i]) && !FENCE_OPEN.test(lines[i])) {
          raw[raw.length - 1] += ' ' + lines[i].trim();
          i += 1;
          continue;
        }
        break;
      }
      blocks.push({
        type: 'list',
        ordered: isOrdered,
        items: raw.map((item) => parseInline(item.trim())),
      });
      continue;
    }

    para.push(line);
    i += 1;
  }

  flushPara();
  return blocks;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm run test:js`
Expected: PASS, 36 tests. The pipe rows inside the growing-prefix test only assert no throw, so they pass before Task 4 exists.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/answer-markdown.js src/scripts/answer-markdown.test.js
git commit -m "feat: parse chat answers into heading, paragraph, code and list blocks

An unterminated fence closes at end of input so a code example renders in
its box while it is still streaming rather than reflowing when the closing
fence lands."
```

---

### Task 4: Table blocks

**Files:**
- Modify: `src/scripts/answer-markdown.js`
- Modify: `src/scripts/answer-markdown.test.js`

**Interfaces:**
- Consumes: `parseAnswer`, `parseInline` from Tasks 2–3.
- Produces: the `{type:'table', header: Span[][], rows: Span[][][]}` block variant.

- [ ] **Step 1: Write the failing tests**

Append to `src/scripts/answer-markdown.test.js`:

```js
test('a pipe table with a delimiter row becomes a table', () => {
  const md = '| Field | Type |\n| --- | --- |\n| id | string |';
  assert.deepEqual(parseAnswer(md), [
    {
      type: 'table',
      header: [
        [{ type: 'text', text: 'Field' }],
        [{ type: 'text', text: 'Type' }],
      ],
      rows: [
        [[{ type: 'text', text: 'id' }], [{ type: 'text', text: 'string' }]],
      ],
    },
  ]);
});

test('a header row with no delimiter row yet stays a paragraph', () => {
  // The streaming case: the delimiter row has not arrived.
  assert.equal(parseAnswer('| Field | Type |')[0].type, 'paragraph');
});

test('adding the delimiter row turns the paragraph into a table', () => {
  assert.equal(parseAnswer('| Field | Type |\n| --- | --- |')[0].type, 'table');
});

test('tables without outer pipes are recognised', () => {
  const blocks = parseAnswer('Field | Type\n--- | ---\nid | string');
  assert.equal(blocks[0].type, 'table');
  assert.equal(blocks[0].header.length, 2);
});

test('alignment markers in the delimiter row are accepted', () => {
  assert.equal(parseAnswer('| a | b |\n| :-- | --: |\n| 1 | 2 |')[0].type, 'table');
});

test('a horizontal rule is not a table', () => {
  assert.equal(parseAnswer('text\n\n---')[0].type, 'paragraph');
  assert.notEqual(parseAnswer('text\n---')[0].type, 'table');
});

test('a short row is padded, and a long row keeps every cell', () => {
  const md = '| a | b |\n| --- | --- |\n| 1 |\n| 1 | 2 | 3 |';
  const t = parseAnswer(md)[0];
  assert.equal(t.rows[0].length, 2);
  assert.deepEqual(t.rows[0][1], []);
  // Never silently drop model output: the extra cell survives.
  assert.equal(t.rows[1].length, 3);
});

test('table cells parse inline spans', () => {
  const t = parseAnswer('| a |\n| --- |\n| `id` |')[0];
  assert.deepEqual(t.rows[0][0], [{ type: 'code', text: 'id' }]);
});

test('a blank line ends a table', () => {
  const blocks = parseAnswer('| a |\n| --- |\n| 1 |\n\nafter');
  assert.equal(blocks.length, 2);
  assert.equal(blocks[1].type, 'paragraph');
});

test('a pipe inside inline code does not split a cell', () => {
  const t = parseAnswer('| a | b |\n| --- | --- |\n| x | y |')[0];
  assert.equal(t.rows[0].length, 2);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm run test:js`
Expected: FAIL — the first table test reports `paragraph`, not `table`.

- [ ] **Step 3: Implement table parsing**

Add these helpers to `src/scripts/answer-markdown.js`, above `parseAnswer`:

```js
function splitRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map((cell) => cell.trim());
}

/**
 * A delimiter row must contain a pipe. Without that check a `---` thematic
 * break directly under a line of prose would turn it into a table.
 */
function isDelimiterRow(line) {
  if (typeof line !== 'string' || !line.includes('|') || !line.includes('-')) {
    return false;
  }
  const cells = splitRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell));
}
```

Then, inside `parseAnswer`'s `while` loop, insert this branch **immediately before** the `const bullet = BULLET.exec(line);` line:

```js
    // List markers are excluded because this branch runs before the list
    // branch; without the guard, a bullet containing a pipe becomes a header
    // row with the marker embedded in the cell.
    if (
      line.includes('|') &&
      !BULLET.test(line) &&
      !ORDERED.test(line) &&
      isDelimiterRow(lines[i + 1])
    ) {
      flushPara();
      const header = splitRow(line).map(parseInline);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
        const cells = splitRow(lines[i]).map(parseInline);
        // Pad short rows so the table stays rectangular; keep the extra cells
        // of a long one rather than discarding model output.
        while (cells.length < header.length) cells.push([]);
        rows.push(cells);
        i += 1;
      }
      blocks.push({ type: 'table', header, rows });
      continue;
    }
```

`splitRow(line).map(parseInline)` passes `map`'s index and array as extra arguments; `parseInline` takes one parameter and ignores them, so this is safe.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm run test:js`
Expected: PASS, 45 tests.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/answer-markdown.js src/scripts/answer-markdown.test.js
git commit -m "feat: parse Markdown pipe tables in chat answers

A pipe row is a table only once its delimiter row arrives, so a table
streaming in renders as a paragraph and then reflows once. Short rows are
padded and long rows keep every cell rather than dropping output."
```

---

### Task 5: DOM builder, code-block chrome, and render coalescing

**Files:**
- Modify: `src/components/ChatPanel.astro` (the `<script>` block and the global `<style>` block from Task 1)

**Interfaces:**
- Consumes: `parseAnswer` from `../scripts/answer-markdown.js`.
- Produces: a `renderAnswer(el, text)` with the same signature as today, plus `flushAnswer(el)` used at end of stream.

- [ ] **Step 1: Replace the renderer**

In `ChatPanel.astro`'s `<script>`, delete the `DOC_LINK` constant and the whole existing `renderAnswer` function (`ChatPanel.astro:78` and `:89-109`). At the top of the script add:

```js
  import { parseAnswer } from '../scripts/answer-markdown.js';
```

In its place add:

```js
  /* Answers are Markdown. The parser turns text into a block array; this
     builder turns that array into nodes. Every node is created with
     createElement/createTextNode and every string goes in through textContent
     — nothing the model emits is ever parsed as HTML, so an XML example
     displays its angle brackets instead of becoming markup. */
  function buildSpans(parent, spans) {
    for (const span of spans) {
      if (span.type === 'code') {
        const code = document.createElement('code');
        code.textContent = span.text;
        parent.appendChild(code);
      } else if (span.type === 'strong') {
        const strong = document.createElement('strong');
        strong.textContent = span.text;
        parent.appendChild(strong);
      } else if (span.type === 'link') {
        /* href is already allowlisted to this site's origin by the parser. */
        const a = document.createElement('a');
        a.href = span.href;
        a.textContent = span.text;
        parent.appendChild(a);
      } else {
        parent.appendChild(document.createTextNode(span.text));
      }
    }
  }

  function copyCode(btn, pre, text) {
    /* Clipboard access can be refused, and the API is absent on insecure
       origins. Select the block and say so rather than appearing to copy and
       doing nothing. */
    const fallback = () => {
      btn.textContent = 'Press Ctrl+C to copy';
      try {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      } catch (e) {}
    };
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      fallback();
      return;
    }
    navigator.clipboard.writeText(text).then(() => {
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    }, fallback);
  }

  function buildCode(block) {
    const wrap = document.createElement('div');
    wrap.className = 'code-block';
    const pre = document.createElement('pre');
    const code = document.createElement('code');
    code.textContent = block.text;
    pre.appendChild(code);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'copy';
    btn.textContent = 'Copy';
    /* The subtree is rebuilt on each frame while streaming, so this button is
       replaced repeatedly and its "Copied" label would not survive. That is
       fine: copying happens after the answer has finished. */
    btn.addEventListener('click', () => copyCode(btn, pre, block.text));
    wrap.appendChild(btn);
    wrap.appendChild(pre);
    return wrap;
  }

  function buildTable(block) {
    const wrap = document.createElement('div');
    wrap.className = 'table-block';
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    const hrow = document.createElement('tr');
    for (const cell of block.header) {
      const th = document.createElement('th');
      buildSpans(th, cell);
      hrow.appendChild(th);
    }
    thead.appendChild(hrow);
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    for (const row of block.rows) {
      const tr = document.createElement('tr');
      for (const cell of row) {
        const td = document.createElement('td');
        buildSpans(td, cell);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  function buildBlock(block) {
    if (block.type === 'heading') {
      /* Shift down one level so an answer's headings never outrank the page's
         own <h2> in the document outline. */
      const h = document.createElement('h' + Math.min(block.level + 1, 6));
      buildSpans(h, block.spans);
      return h;
    }
    if (block.type === 'paragraph') {
      const p = document.createElement('p');
      buildSpans(p, block.spans);
      return p;
    }
    if (block.type === 'code') return buildCode(block);
    if (block.type === 'table') return buildTable(block);
    if (block.type === 'list') {
      const list = document.createElement(block.ordered ? 'ol' : 'ul');
      for (const item of block.items) {
        const li = document.createElement('li');
        buildSpans(li, item);
        list.appendChild(li);
      }
      return list;
    }
    return document.createTextNode('');
  }

  function paintAnswer(el, text) {
    el.textContent = '';
    for (const block of parseAnswer(text)) el.appendChild(buildBlock(block));
  }

  /* Re-parsing the whole answer on each of ~2000 deltas is O(n^2). Coalesce to
     one paint per animation frame; the frame always paints the latest text, so
     no delta is lost, only superseded intermediate states. */
  function renderAnswer(el, text) {
    el._pendingText = text;
    if (el._rafId) return;
    el._rafId = requestAnimationFrame(() => {
      el._rafId = 0;
      paintAnswer(el, el._pendingText);
    });
  }

  /* Paint now, not next frame. requestAnimationFrame does not fire in a
     backgrounded tab, so without this a user who switches away mid-answer
     comes back to a turn frozen at whatever streamed before they left. */
  function flushAnswer(el) {
    if (el._rafId) {
      cancelAnimationFrame(el._rafId);
      el._rafId = 0;
    }
    if (typeof el._pendingText === 'string') paintAnswer(el, el._pendingText);
  }
```

- [ ] **Step 2: Flush at end of stream**

In `send()`, the block that currently reads:

```js
    if (answer && !errored) messages.push({ role: 'assistant', content: answer });
    else if (errored) dropTurn();
```

becomes:

```js
    if (answer) flushAnswer(answerEl);
    if (answer && !errored) messages.push({ role: 'assistant', content: answer });
    else if (errored) dropTurn();
```

Note `answerEl.remove()` runs before this in the error paths, and `flushAnswer` on a detached node is harmless — but it is guarded by `if (answer)` and those paths only remove the element when `answer` is empty.

- [ ] **Step 3: Style the new blocks**

Add to the `<style is:global>` block created in Task 1:

```css
  /* Block spacing. Assistant answers are built from real elements, so they
     need margins rather than pre-wrap. */
  #fews-chat-log .turn.assistant > :first-child { margin-top: 0; }
  #fews-chat-log .turn.assistant > :last-child { margin-bottom: 0; }
  #fews-chat-log .turn.assistant p { margin: 0 0 0.75rem; }
  #fews-chat-log .turn.assistant h3,
  #fews-chat-log .turn.assistant h4,
  #fews-chat-log .turn.assistant h5,
  #fews-chat-log .turn.assistant h6 {
    margin: 1rem 0 0.4rem; font-size: var(--sl-text-sm); font-weight: 700;
  }
  #fews-chat-log .turn.assistant ul,
  #fews-chat-log .turn.assistant ol { margin: 0 0 0.75rem; padding-left: 1.25rem; }
  #fews-chat-log .turn.assistant li { margin: 0.2rem 0; }
  #fews-chat-log .turn.assistant code {
    font-family: var(--sl-font-mono, ui-monospace, monospace);
    font-size: 0.9em; background: var(--sl-color-gray-6);
    padding: 0.1em 0.3em; border-radius: 3px;
  }

  /* Code blocks: the reason this feature exists. Monospace, own background,
     and horizontal scroll so a wide XML line never widens the panel. */
  #fews-chat-log .code-block {
    position: relative; margin: 0 0 0.75rem;
    border: 1px solid var(--sl-color-gray-5); border-radius: 4px;
    background: var(--sl-color-black);
  }
  #fews-chat-log .code-block pre {
    margin: 0; padding: 0.6rem 0.7rem; overflow-x: auto;
  }
  #fews-chat-log .code-block code {
    background: none; padding: 0; white-space: pre;
    font-family: var(--sl-font-mono, ui-monospace, monospace);
    font-size: var(--sl-text-xs);
  }
  #fews-chat-log .code-block .copy {
    position: absolute; top: 0.25rem; right: 0.25rem; z-index: 1;
    padding: 0.15rem 0.45rem; cursor: pointer;
    font-size: var(--sl-text-xs);
    background: var(--sl-color-gray-6); color: var(--sl-color-white);
    border: 1px solid var(--sl-color-gray-5); border-radius: 3px;
  }

  #fews-chat-log .table-block { overflow-x: auto; margin: 0 0 0.75rem; }
  #fews-chat-log .table-block table {
    border-collapse: collapse; font-size: var(--sl-text-xs);
  }
  #fews-chat-log .table-block th,
  #fews-chat-log .table-block td {
    border: 1px solid var(--sl-color-gray-5);
    padding: 0.25rem 0.45rem; text-align: left; vertical-align: top;
    white-space: nowrap;
  }
  #fews-chat-log .table-block th { font-weight: 700; }
```

- [ ] **Step 4: Verify the no-HTML-parsing invariant still holds**

```bash
grep -nE 'innerHTML|outerHTML|insertAdjacentHTML' src/components/ChatPanel.astro src/scripts/answer-markdown.js
```

Expected: **no output.** Any hit is a stop-and-report.

- [ ] **Step 5: Build and check by hand**

Run: `npm run build && npm run preview`

Open the preview, open the panel. Signed out you will get the sign-in notice, which is expected — this step only confirms the bundle builds and the panel still mounts. Confirm the browser console shows no module-resolution error for `answer-markdown.js`.

- [ ] **Step 6: Commit**

```bash
git add src/components/ChatPanel.astro
git commit -m "feat: render chat answers as formatted blocks with copyable code

Builds headings, paragraphs, lists, scrollable code blocks and tables from
the parsed block array using createElement only. Paints once per animation
frame instead of once per delta, and flushes synchronously at end of stream
so a backgrounded tab does not freeze mid-answer."
```

---

### Task 6: Tell the model which formatting to use

**Files:**
- Modify: `chat/agent.py` (the `PERSONA` string)

**Interfaces:**
- Consumes: the supported syntax set from Tasks 3–4.
- Produces: no code interface. Changes the cached system prefix.

- [ ] **Step 1: Add the formatting section**

In `chat/agent.py`, replace this line at the end of `PERSONA`:

```
- Be concise and concrete. Prefer a short XML example over a long explanation.
```

with:

```
- Be concise and concrete. Prefer a short XML example over a long explanation.

Formatting. Your answers render in a narrow (420px) side panel that supports a
limited subset of Markdown. Use it, and stay inside it:
- Put every config example in a fenced code block with a language tag —
  ```xml, ```csv or ```text. Never present XML as indented prose.
- Use ## or ### headings only when an answer has genuinely separate sections.
- Use - bullets and 1. numbered lists for steps and field lists.
- Use `backticks` for element, attribute and file names.
- Tables are supported and are good for field references. Keep them to three
  or four narrow columns; the panel is narrow and wide tables must scroll.
- Emphasis is bold only, written with two asterisks. Single asterisks and
  underscores are not italics here; they reach the reader as punctuation.
- Separate sections with a heading, never with a horizontal rule. A line of
  three hyphens renders as three hyphens.
- Nested lists, blockquotes, images, task lists and raw HTML do NOT render
  correctly. Some reach the reader as stray punctuation; others are silently
  flattened and lose their structure. Do not use them.
```

- [ ] **Step 2: Confirm the Python tests still pass**

Run: `chat/venv/bin/python -m pytest chat/tests -q`
Expected: PASS, 129 tests. If a test asserts on `PASS`/`PERSONA` content, stop and report rather than editing the test.

- [ ] **Step 3: Commit**

```bash
git add chat/agent.py
git commit -m "feat: instruct the chat model to format for the narrow panel

Directs config examples into fenced code blocks with a language tag and
rules out the Markdown constructs the panel does not render, which would
otherwise show as literal characters."
```

---

### Task 7: Deploy and verify live

**Files:**
- Modify: none. Deployment only.

**Interfaces:**
- Consumes: Tasks 1–6, merged to `main`.

- [ ] **Step 1: Run the full test suite on both sides**

```bash
npm run test:js
chat/venv/bin/python -m pytest chat/tests -q
```

Expected: both PASS. Do not deploy on a failure — report it.

- [ ] **Step 2: Merge and push**

```bash
git checkout main && git merge --no-ff <branch> && git push origin main
```

- [ ] **Step 3: Pull and rebuild on the deploy clone**

```bash
sudo -u fewsdocs git -C /home/fewsdocs/repo pull
sudo -u fewsdocs bash -c 'cd /home/fewsdocs/repo && npm ci && npm run build'
```

Expected: 54 pages built.

- [ ] **Step 4: Restart the chat service**

The `PERSONA` change only takes effect on restart, and the corpus is read once at startup.

```bash
sudo systemctl restart fewsdocs-chat
sudo systemctl is-active fewsdocs-chat
```

Expected: `active`.

- [ ] **Step 5: Confirm the gate still holds**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://df-docs.streamflows.org/api/chat \
  -H 'Content-Type: application/json' -d '{"messages":[]}'
curl -s -o /dev/null -w '%{http_code}\n' https://df-docs.streamflows.org/api/chat/status
```

Expected: `401` from both. Anything else is a stop-and-report — the auth gate matters more than the formatting.

- [ ] **Step 6: Verify the rendering live**

Signed in, ask: *"Show me a minimal Locations file and list its required fields."*

Confirm all five:
1. Paragraphs are on separate lines — the original defect is gone.
2. The XML arrives inside a bordered monospace box, not as prose.
3. A long XML line scrolls **inside** its box; the page itself does not scroll sideways.
4. The Copy button copies the example.
5. The citation link still works and points at a real page.

Note the day's spend: the first question after restart pays a cold cache write of about **$0.67** against the $2.00 ceiling.

- [ ] **Step 7: Commit any documentation touch-ups**

If `CLAUDE.md`'s redeploy runbook needs no change, skip. It already covers pull, build and restart.

---

## Self-Review

**Spec coverage.** The defect and `is:global` fix → Task 1. Parser module and `Block`/`Span` shapes → Tasks 2–4. Supported-syntax table, including the `h(min(level+1, 6))` mapping → Tasks 3 and 5. Streaming (unterminated fence, incomplete table, rAF coalescing) → Tasks 3, 4, 5. Security invariant → Task 2 (`safeHref`), Task 5 (Step 4 grep). Copy-button fallback → Task 5 `copyCode`. System prompt and cache cost → Task 6, restated in Task 7 Step 6. Testing section, including every listed case → Tasks 2–4. Deployment → Task 7. No uncovered requirement.

**Type consistency.** `parseInline` and `parseAnswer` are named identically in every task. `DOC_ORIGIN` is exported in Task 2 and imported by the Task 2 tests. Block `type` strings (`heading`, `paragraph`, `code`, `list`, `table`) and span `type` strings (`text`, `code`, `strong`, `link`) match between the parser tasks and `buildBlock`/`buildSpans` in Task 5. `renderAnswer(el, text)` keeps its existing two-argument signature, so its call site inside the SSE loop is unchanged; `flushAnswer(el)` is the only new call.

**Known deviation from the spec.** The spec says the parser exports "one pure function." It exports three: `parseAnswer`, `parseInline` and `DOC_ORIGIN`. `parseInline` is exported so it can be tested directly rather than only through block parsing, and `DOC_ORIGIN` so the tests do not hardcode the origin in sixteen places. `parseAnswer` remains the only entry point the DOM builder uses.

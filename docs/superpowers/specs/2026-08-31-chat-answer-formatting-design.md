# Chat answer formatting

Answers from the docs assistant arrive as one unbroken line. This spec covers
why, and what replaces it.

## The defect

`src/components/ChatPanel.astro` styles conversation turns with
`white-space: pre-wrap`, which would preserve the model's newlines. Astro scopes
component styles, so that rule compiles to:

```css
#fews-chat-log:where(.astro-sy3qanz7) .turn:where(.astro-sy3qanz7){
  margin-bottom:1rem;white-space:pre-wrap
}
```

Every `.turn` element is created at runtime by `addTurn()`, so none of them carry
the `astro-sy3qanz7` class. The whole `.turn` block is dead: not `pre-wrap`, not
the user and notice colours, not the link colour. Newlines collapse to spaces and
the answer runs together.

The fix is to move the `.turn` rules into `<style is:global>`. Every selector in
that block is already prefixed with `#fews-chat`, so nothing leaks to the rest of
the site, and it is the only way a rule can reach an element JavaScript created.

Restoring those rules fixes line breaks but nothing else. The model emits
Markdown — headings, bullets, fenced code blocks — which would then show as
literal `##`, `-` and ` ``` ` characters, and an XML example would still be
unstyled, unscrollable text. The rest of this spec is the formatting layer built
on top of the CSS fix.

## Goals

- Answers render with real paragraphs, headings, lists, code blocks and tables.
- XML and CSV examples are visually distinct, monospaced, and scrollable rather
  than wrapped.
- No regression to the existing safety property: nothing the model emits is ever
  parsed as HTML.
- No new runtime dependency and no JavaScript build pipeline.

## Non-goals

- Widening the chat panel. Code blocks scroll horizontally at the current
  420px.
- Full Markdown. Nested lists, blockquotes, images and raw HTML are out of
  scope and degrade to plain text.
- Rendering Markdown anywhere other than assistant answers. User turns and
  notices stay plain text.

## Architecture

Two units with one boundary between them.

### `src/scripts/answer-markdown.js` — the parser

One exported pure function, `parseAnswer(text) -> Block[]`. It touches no DOM,
imports nothing, and has no side effects. All parsing logic and every edge case
lives here.

```js
Block =
  | { type: 'heading',   level: 1..6, spans: Span[] }
  | { type: 'paragraph', spans: Span[] }
  | { type: 'code',      lang: string, text: string }
  | { type: 'list',      ordered: boolean, items: Span[][] }
  | { type: 'table',     header: Span[][], rows: Span[][][] }

Span =
  | { type: 'text',   text: string }
  | { type: 'code',   text: string }
  | { type: 'strong', text: string }
  | { type: 'link',   text: string, href: string }
```

Keeping the parser pure is what makes it testable with the Node 22 built-in test
runner and plain deep-equal assertions — no jsdom, no test framework, no
dependency. It is also where all the risk is, so it is the part that gets the
test coverage.

### `ChatPanel.astro` — the DOM builder

`renderAnswer(el, text)` calls `parseAnswer`, then walks the block array building
nodes with `createElement` and `createTextNode`. Roughly twenty lines, simple
enough to verify by reading.

The builder is the only place that knows about the DOM; the parser is the only
place that knows about Markdown. Either can be changed without touching the
other.

## Supported syntax

| Construct | Markdown | Rendered as |
|---|---|---|
| Heading | `## Text` (levels 1–6) | `h(min(level + 1, 6))`, so an answer's headings never outrank the page's own `<h2>` |
| Paragraph | blank-line separated | `<p>` |
| Fenced code | ` ```xml ` … ` ``` ` | `<pre><code>` in a scrollable box with a copy button |
| Inline code | `` `text` `` | `<code>` |
| Bold | `**text**` | `<strong>` |
| Bullet list | `- ` or `* ` | `<ul><li>` |
| Numbered list | `1. ` | `<ol><li>` |
| Table | pipe table with delimiter row | `<table>` in a scrollable box |
| Link | `[text](url)` | `<a>`, host-restricted (below) |
| Bare URL | `https://df-docs.streamflows.org/…` | `<a>`, as today |

Anything else is literal text. Unsupported constructs must never be silently
dropped — an unrecognised line renders as a paragraph containing its own source.

The copy button writes the block's raw text via `navigator.clipboard.writeText`
and reports success in place ("Copied"). If the call rejects or the API is
absent, the button says "Press Ctrl+C to copy" and selects the block's text
rather than failing silently.

## Streaming

`renderAnswer` is called on every SSE delta with the full accumulated answer, so
the parser is fed incomplete Markdown continuously. Two cases need explicit
handling or the panel visibly misbehaves.

**Unterminated fence.** When ` ```xml ` has arrived and its closing fence has
not, the parser emits a `code` block containing everything after the opening
fence. Without this the example streams in as prose and jumps into a box when
the fence closes.

**Incomplete table.** A line of pipes is a table only once its delimiter row
(`|---|---|`) has arrived. Until then it is a paragraph. It reflows into a table
in one step, which is acceptable.

Both cases mean the same input parses differently as it grows. That is correct
and intended; the tests assert it explicitly.

**Render coalescing.** Re-parsing the whole answer on each of roughly 2000
deltas is O(n²). `renderAnswer` schedules through `requestAnimationFrame` and
drops superseded frames, capping work at about 60 parses per second regardless
of delta rate. The final delta must always render — the guard schedules, it never
skips the last state.

## Security

The current implementation guarantees that model output is never parsed as HTML,
by never assigning `innerHTML`. That property is preserved, not delegated to a
sanitiser:

- Every node is built with `createElement` / `createTextNode`. No `innerHTML`,
  no `insertAdjacentHTML`, no `outerHTML`, anywhere in the builder.
- An `<a>` is emitted only when the href's origin is exactly
  `https://df-docs.streamflows.org`. `[click me](javascript:alert(1))` renders as
  inert text, not an anchor. This is an allowlist, not a `javascript:` denylist.
- Link detection does not run inside `code` blocks or inline code, so a URL in an
  XML example stays literal.
- Code block text is set with `textContent`, so `<` and `&` in an XML example are
  displayed, not interpreted.

## System prompt

`PERSONA` in `chat/agent.py` gains a short formatting section: use Markdown, use
fenced code blocks with a language tag for config examples, keep to the supported
subset, and prefer compact tables in a narrow panel.

`PERSONA` sits inside the cached system prefix, so editing it invalidates the
1-hour prompt cache. The first question after deploy pays a cold cache write of
about **$0.67** instead of the warm ~$0.03. One-time, but a third of the $2.00
daily ceiling — deploy accordingly.

## Testing

Per the repository's agent-testing constraints, parser tests are written before
implementation and by a separate agent invocation from the one that implements
the parser. Once written they are a locked specification.

Run with `node --test src/scripts/` via a new `npm run test:js` script. No new
dependency.

Coverage targets the failure modes of a hand-written parser, not the happy path:

- Unterminated fence mid-stream renders as an open code block.
- A fence whose body contains `|`, `#`, `-` and `**` yields no table, heading,
  list or bold — fence content is opaque.
- Partial table (header row only) is a paragraph; adding the delimiter row makes
  it a table.
- Table rows with fewer or more cells than the header do not throw.
- `[text](javascript:…)`, `[text](http://evil.example)` and protocol-relative
  `//evil.example` all render as text, not anchors.
- Bare-URL autolinking still strips trailing sentence punctuation, and does not
  fire inside code.
- Unmatched `**` and unmatched `` ` `` render literally rather than swallowing
  the remainder of the answer.
- Empty input, whitespace-only input, and a lone newline produce no blocks and
  do not throw.
- Growing the same answer one character at a time never throws at any prefix.

The DOM builder gets no unit tests — it has no branching worth asserting — but
the existing manual smoke test through the live panel covers it.

## Deployment

Front-end changes ship through the normal `npm run build`. The `PERSONA` change
requires `systemctl restart fewsdocs-chat`, which also drops the prompt cache as
noted above.

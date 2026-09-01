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
// Quantifiers are bounded to prevent catastrophic backtracking on unmatched
// brackets. A label or href longer than 500 chars renders as literal text
// rather than a link — acceptable since link labels, hrefs, and bold phrases
// from LLM output are typically under 100 characters.
const INLINE = new RegExp(
  '`([^`\\n]+)`' +
    '|\\[([^\\]\\n]{0,500})\\]\\(([^)\\s]{0,500})\\)' +
    '|\\*\\*([^\\n]{1,500}?)\\*\\*' +
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

// A fence's info string may carry more than just the language (e.g. filename,
// version). Capture the whole line. Consumers needing only the language take
// the first token. Dropping any part would violate the never-silently-drop rule.
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

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

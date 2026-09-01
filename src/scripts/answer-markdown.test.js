import test from 'node:test';
import assert from 'node:assert/strict';
import { parseInline, parseAnswer, DOC_ORIGIN as DOC } from './answer-markdown.js';

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

test('pathological bracket runs do not backtrack quadratically', () => {
  // The old unbounded label class took ~15s on this input; parseInline runs
  // on every streaming delta, so quadratic here freezes the panel.
  const start = process.hrtime.bigint();
  parseInline('['.repeat(100000));
  parseInline('[x]('.repeat(50000));
  const ms = Number(process.hrtime.bigint() - start) / 1e6;
  assert.ok(ms < 1000, `parsing took ${ms.toFixed(0)}ms`);
});

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

test('a fence info string keeps text past the language token', () => {
  const md = '```xml Config/RegionConfigFiles/Locations.xml\n<a/>\n```';
  assert.deepEqual(parseAnswer(md), [
    { type: 'code', lang: 'xml Config/RegionConfigFiles/Locations.xml', text: '<a/>' },
  ]);
});

test('a bare fence still has a clean language token', () => {
  assert.equal(parseAnswer('```xml\n<a/>\n```')[0].lang, 'xml');
  assert.equal(parseAnswer('```\n<a/>\n```')[0].lang, '');
});

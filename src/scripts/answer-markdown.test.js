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

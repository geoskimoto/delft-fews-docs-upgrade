---
title: How to read a reference page
description: The consistent shape every Config File Reference page will follow, so you always know where to look.
sidebar:
  order: 1
---

The **Config File Reference** section will document individual config file types.
Every page will follow the same shape, so once you've read one you can navigate
them all. This page defines that shape — it's the template the reference pages
are built from.

## The shape of a reference page

Each reference page will have these sections, in this order:

<div class="field-list">

**At a glance**
: A one-box summary — what the file is for, which folder it lives in, and what it
depends on. Read this first; often it's all you need.

**Minimal example**
: The smallest valid, useful version of the file, ready to copy. No optional
frills — just enough to work.

**Common patterns**
: The handful of variations you'll actually reach for, each with a short example
and *when to use it.*

**Field reference**
: The elements and attributes, generated from the official
[`.xsd` schema][schemas] so it stays exhaustive and accurate — with plain-language
notes the schema alone can't give you.

**Gotchas**
: The mistakes that cost people an afternoon: silent failures, ordering rules,
version-suffix traps ([see the directory notes](/concepts/config-directory/#how-fews-finds-a-file)).

**See also**
: Links to the related [task guide](/tasks/locations/), [concepts](/concepts/overview/),
and the raw schema.

</div>

## Why prose *and* schema on one page

The single biggest friction in the official docs is that the **explanation**
lives on the wiki while the **authoritative element list** lives in a separate
`.xsd` file on another domain. You end up with two tabs open, mentally diffing
them.

These reference pages will put both in one place: the schema-derived field list
for completeness, and human notes for the parts a schema can't express — *why*
a field exists, what a sensible default is, and what breaks if you get it wrong.

:::note
The reference section is queued after the task guides. This page exists now so
the format is settled and visible.
:::

[schemas]: https://fews.wldelft.nl/schemas/version1.0/

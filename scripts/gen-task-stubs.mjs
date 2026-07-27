// One-off generator for task-guide placeholder pages.
// Each stub follows the same shape as the (written) Core Concepts pages so the
// format is obvious and pages are ready to be filled in.
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const root = resolve(process.cwd(), 'src/content/docs');

/** @type {Array<{slug:string,order:number,title:string,desc:string,covers:string[],concept:string}>} */
const tasks = [
  {
    slug: 'tasks/locations',
    order: 1,
    title: 'Define locations & location sets',
    desc: 'Declare the places FEWS tracks, and group them into sets.',
    covers: [
      'The `Locations` file: IDs, names, coordinates, and attributes',
      '`LocationSets`: static lists, and sets built from a CSV or shapefile',
      'Referencing sets from other config so one rule covers many places',
      'Common gotchas: duplicate IDs, missing coordinates, set vs. single ID',
    ],
    concept: '/concepts/config-directory/',
  },
  {
    slug: 'tasks/parameters',
    order: 2,
    title: 'Define parameters & units',
    desc: 'Declare the quantities you measure and compute, and their units.',
    covers: [
      'Parameter groups vs. parameters (e.g. discharge group → `Q.obs`, `Q.sim`)',
      'Units, display units, and where conversions happen',
      'Parameter types: instantaneous vs. accumulative vs. mean',
      'Naming conventions that keep large configs sane',
    ],
    concept: '/concepts/glossary/#parameter',
  },
  {
    slug: 'tasks/import-timeseries',
    order: 3,
    title: 'Import time series',
    desc: 'Bring external data into the FEWS internal representation.',
    covers: [
      'Choosing an import type (file, web service, database)',
      'A minimal time-series import, end to end',
      'How import connects to ID mapping and unit/flag conversion',
      'Debugging: why imported data does not appear where you expect',
    ],
    concept: '/concepts/data-flow/',
  },
  {
    slug: 'tasks/id-mapping',
    order: 4,
    title: 'Map external IDs (ID mapping)',
    desc: 'Translate an external system’s names into FEWS internal names.',
    covers: [
      'The `IdMapFiles` structure: external ⇆ internal location & parameter',
      'Exact maps vs. pattern/prefix maps for bulk translation',
      'Why a mismatched map is the #1 cause of “missing” data',
      'Testing a map in isolation before running a full import',
    ],
    concept: '/concepts/data-flow/',
  },
  {
    slug: 'tasks/filters-displays',
    order: 5,
    title: 'Filters & display groups',
    desc: 'Control what users can browse and how data is grouped in the UI.',
    covers: [
      'Filters: building the browsable data tree in the Explorer',
      'Display groups: bundling related series into one view',
      'Relating filters to location sets and parameters',
      'Keeping the tree navigable as the system grows',
    ],
    concept: '/concepts/glossary/#filter',
  },
  {
    slug: 'tasks/thresholds',
    order: 6,
    title: 'Thresholds & warnings',
    desc: 'Attach warning levels to data and raise events when crossed.',
    covers: [
      'Threshold value sets and warning levels',
      'Attaching thresholds to locations/parameters',
      'How threshold crossings become events and alerts',
      'Displaying thresholds on charts and spatial maps',
    ],
    concept: '/concepts/glossary/#threshold',
  },
  {
    slug: 'tasks/transformations',
    order: 7,
    title: 'Transformations',
    desc: 'Compute new series from existing ones.',
    covers: [
      'The transformation module structure and inputs/outputs',
      'Common transforms: rating curves, aggregation, gap filling',
      'Chaining transforms and where they sit in a workflow',
      'Reading and debugging transformation output',
    ],
    concept: '/concepts/lifecycle/',
  },
  {
    slug: 'tasks/general-adapter',
    order: 8,
    title: 'Run a model (General Adapter)',
    desc: 'Bridge FEWS to an external model executable.',
    covers: [
      'The export → run → import cycle the General Adapter performs',
      'Mapping FEWS series to model input/output files',
      'Cold vs. warm states in a model run',
      'Diagnosing a failed or empty model run',
    ],
    concept: '/concepts/glossary/#general-adapter',
  },
  {
    slug: 'tasks/workflows',
    order: 9,
    title: 'Workflows & scheduling',
    desc: 'Chain modules into an ordered recipe and run it.',
    covers: [
      'Anatomy of a workflow file: activities in order',
      'Nesting workflows and reusing sub-workflows',
      'Ensemble and looping activities',
      'How workflows get scheduled and triggered',
    ],
    concept: '/concepts/lifecycle/',
  },
  {
    slug: 'tasks/export',
    order: 10,
    title: 'Export data',
    desc: 'Send results back out to files, databases, and services.',
    covers: [
      'Export modules and the reverse of the import boundary',
      'Applying ID mapping and unit conversion on the way out',
      'Common export formats and destinations',
      'Verifying an export produced what you expect',
    ],
    concept: '/concepts/data-flow/',
  },
];

const stub = (t) => `---
title: ${t.title}
description: ${t.desc}
sidebar:
  order: ${t.order}
  badge:
    text: Coming soon
    variant: caution
---

:::caution[Coming soon]
This task guide is being written. It follows the same format as the completed
[Core Concepts](/concepts/overview/) section: purpose, a minimal copy-paste
example, common patterns, gotchas, and a link to the underlying schema.
:::

## What this guide will cover

${t.covers.map((c) => `- ${c}`).join('\n')}

## Before you dive in

This task builds on the mental model in
[Core Concepts](${t.concept}). If you haven't read the
[data-flow model](/concepts/data-flow/) yet, start there — this guide assumes it.
`;

for (const t of tasks) {
  const file = resolve(root, `${t.slug}.md`);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, stub(t));
  console.log('wrote', t.slug);
}
console.log(`\nGenerated ${tasks.length} task stubs.`);

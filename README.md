# Improved Delft-FEWS Configuration Guide

A clearer, task-oriented rewrite of the Delft-FEWS **configuration** documentation,
built as a local-first static docs site with [Astro Starlight](https://starlight.astro.build/).

## Why

The [official Configuration Guide](https://publicwiki.deltares.nl/spaces/FEWSDOC/pages/8683900/Configuring+Delft-FEWS+-+Configuration+Guide)
is complete but reads as a reference manual for people who already understand
FEWS. This site leads with the **mental model**, organizes content by **task**
rather than by schema, and keeps prose and schema detail in one place.

## Status

- ✅ **Core Concepts** section — written end to end (the format template):
  overview, data-flow model, config-directory layout, forecasting lifecycle,
  glossary.
- ✅ **Getting-started tutorial** — a schema-grounded, build-a-minimal-config
  walkthrough.
- ✅ **Reference generator** — schema-derived field tables, proven end to end on
  the [Locations file](src/content/docs/reference/locations.mdx). See below.
- 🚧 **Task guides** and the rest of the **Config File Reference** — scaffolded
  placeholders describing what each will cover.

## Schema-derived reference tables

Reference pages don't hand-maintain element lists — they're generated from the
official FEWS `.xsd` schemas so they can't drift from the real grammar, and they
reuse the schema's own `<documentation>` annotations as descriptions.

- Vendored schemas live in `schemas/` (an `.xsd` plus its includes, e.g.
  `sharedTypes.xsd`).
- `scripts/schema-to-fields.mjs` resolves an element to its complex types,
  cardinality, enumerations, and docs, emitting JSON to `src/data/schema/`.
- `scripts/gen-schema.mjs` lists which schemas to generate; it runs automatically
  via `predev` / `prebuild` (also `npm run gen:schema`).
- `src/components/FieldReference.astro` renders that JSON as linked tables.

Add a reference page by vendoring its `.xsd`, adding an entry to
`scripts/gen-schema.mjs`, and creating `reference/<element>.mdx`.

## Run it locally

```bash
npm install      # already done once; re-run after pulling changes
npm run dev      # serves at http://localhost:4321
```

Other commands:

```bash
npm run build    # static build into ./dist
npm run preview  # serve the production build locally
```

## Deploying later

`npm run build` emits a fully static site to `./dist`, which can be hosted on any
static host (Netlify, Vercel, GitHub Pages, S3, an internal server, …). No server
runtime required.

## Project layout

```
astro.config.mjs                 # site config + sidebar / navigation
src/content/docs/                # all pages (Markdown / MDX)
  index.mdx                      # landing page
  start-here/                    # intro + planned tutorial
  concepts/                      # ← the completed Core Concepts section
  tasks/                         # task guides (placeholders)
  reference/                     # config-file reference (template + placeholders)
  resources/                     # links back to official sources
src/styles/custom.css            # light theming
scripts/gen-task-stubs.mjs       # one-off generator for task placeholder pages
```

## Notes

- Node 20+ runs the site fine. (The `create-astro` scaffolder wants Node 22, so
  this project was assembled manually — nothing else needs Node 22.)
- This is an independent guide, not an official Deltares product.

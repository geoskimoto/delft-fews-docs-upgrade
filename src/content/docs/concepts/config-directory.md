---
title: The configuration directory
description: How a Delft-FEWS configuration is laid out on disk — the config folders, what each one holds, and how FEWS finds your files.
sidebar:
  order: 3
---

A Delft-FEWS configuration isn't a single file — it's a **tree of XML files in
named folders**. FEWS discovers configuration by folder name and by file-name
convention, so knowing the layout tells you *where a given setting has to live*.

## The big picture

At the top sits a **region home directory**. Everything FEWS reads is under it.
The two folders you'll spend the most time in are `Config` (the definitions) and,
during development, the module data and workflow folders.

<div class="diagram">

```text
<Region Home>/
├── Config/                     ← almost everything you edit lives here
│   ├── RegionConfigFiles/      ← the "what exists" layer: locations, parameters,
│   │                              thresholds, filters, grids, flags…
│   ├── SystemConfigFiles/      ← system-wide behavior: explorer, permissions,
│   │                              logging, time settings…
│   ├── ModuleConfigFiles/      ← the "what to do" layer: one file per module
│   │                              (imports, transformations, model adapters…)
│   ├── WorkflowFiles/          ← ordered recipes that chain modules together
│   ├── DisplayConfigFiles/     ← charts, grids, tables, spatial displays
│   ├── IdMapFiles/             ← external-name ⇆ FEWS-name translation tables
│   ├── UnitConversionsFiles/   ← unit conversions applied on import/export
│   ├── FlagConversionsFiles/   ← quality-flag translation on import
│   ├── MapLayerFiles/          ← background maps, shapefiles, icons
│   ├── ColdStateFiles/         ← initial model states
│   └── … (many more, all optional until you need them)
├── Modules/                    ← external model executables & their data
├── ColdStates/  Warm States/   ← saved model states
└── … (runtime & data folders)
```

</div>

:::tip[You don't need most of these on day one]
A minimal working configuration uses a handful of folders. The rest appear only
when you add a feature that needs them. Don't be intimidated by the full list —
treat it as a map you'll fill in gradually.
:::

## The two layers that matter most

Almost everything in `Config` falls into one of two conceptual layers. Keeping
them straight is half the battle.

### 1. The "what exists" layer — `RegionConfigFiles`

This is your system's **vocabulary**: the set of things that can be talked about.

| File (typical) | Defines |
| --- | --- |
| `Locations` / `LocationSets` | The gauges, reservoirs, catchments — and groupings of them. |
| `Parameters` | The quantities: discharge, water level, precipitation, temperature. |
| `Thresholds` / `ThresholdValueSets` | Warning levels attached to locations/parameters. |
| `Filters` | The tree of data feeds users browse in the UI. |
| `Grids` | Definitions of spatial grids for gridded data. |
| `Flags` | The quality-flag vocabulary. |

Nothing here *does* anything — it declares the nouns that the rest of the config
refers to. See [Define locations](/tasks/locations/) and [Define
parameters](/tasks/parameters/).

### 2. The "what to do" layer — `ModuleConfigFiles` + `WorkflowFiles`

This is the **behavior**: the verbs, and the order to run them in.

- A file in **`ModuleConfigFiles`** configures one module — an import, a
  transformation, a General Adapter model run. One module = one unit of work.
- A file in **`WorkflowFiles`** is a **workflow**: an ordered list of modules to
  execute. Workflows are what actually get scheduled and run. See
  [Workflows](/tasks/workflows/).

<div class="diagram">

```text
   RegionConfigFiles  ── declares ──▶  the nouns (locations, parameters, …)
                                              ▲
                                              │ refer to
   ModuleConfigFiles  ── each is ──▶  one verb (import X, transform Y, run model Z)
                                              ▲
                                              │ ordered by
   WorkflowFiles      ── each is ──▶  a recipe: [module A, module B, module C]
```

</div>

## How FEWS finds a file

Two conventions govern discovery — and they're a common source of "why isn't my
change taking effect?":

1. **Folder placement.** A module config must be in `ModuleConfigFiles`, a
   workflow in `WorkflowFiles`, and so on. Put it in the wrong folder and FEWS
   won't treat it as that kind of file.

2. **File naming & versioning.** FEWS config files typically carry a name plus a
   version suffix, e.g. `ImportGauges 1.00 default.xml`. FEWS reads the
   **highest applicable version**. This lets you keep old versions alongside new
   ones — but it also means an old file with a higher version number can silently
   win. When a change "isn't taking," check for a higher-numbered sibling.

:::caution[The most common layout mistakes]
- Editing a file but leaving a **higher-versioned** copy next to it (the old one
  still wins).
- Referring to a location or parameter **ID that isn't declared** in
  `RegionConfigFiles` (FEWS can't resolve the noun).
- Putting a module in `WorkflowFiles` (or vice versa) — right content, wrong
  folder.
:::

## What to take away

- Config is a **tree of named folders**; placement and file naming are
  significant, not cosmetic.
- Two layers dominate: **`RegionConfigFiles`** (the nouns — what exists) and
  **`ModuleConfigFiles` + `WorkflowFiles`** (the verbs — what to do, and in what
  order).
- FEWS reads the **highest version** of a file name — a frequent cause of
  "changes not taking effect."

Next: watch these files come alive during [the forecasting
lifecycle →](/concepts/lifecycle/)

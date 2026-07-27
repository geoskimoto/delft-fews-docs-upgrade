---
title: How data flows through FEWS
description: The central mental model — external source, import, internal time series, processing, and display/export — and the config files that govern each hop.
sidebar:
  order: 2
---

This is the most important page in the guide. If the diagram below makes sense
to you, most of Delft-FEWS configuration will too.

## The journey of a value

Follow a single number — say, a discharge reading from a gauge — from the
outside world to a chart on a forecaster's screen. It passes through a fixed
sequence of hops. Each hop is governed by a specific kind of config file.

<div class="diagram">

```text
  OUTSIDE WORLD              FEWS INTERNAL WORLD                     OUTPUTS
 ┌──────────────┐    ┌────────────────────────────────────┐   ┌──────────────┐
 │  data files  │    │                                    │   │   displays   │
 │  web service │──▶ │   ①      ②        ③        ④       │──▶│   & charts   │
 │  database    │    │  IMPORT  MAP    STORE    PROCESS    │   │              │
 │  model output│    │  parse   rename  in the  transform, │   │   thresholds │
 └──────────────┘    │          to FEWS local   model runs │   │   & warnings │
        ▲            │          names   data-             │   │              │
        │            │                  store             │──▶│   exports    │
        │            │                    │       │         │   │  (files, DB, │
        └────────────┼── ⑤ EXPORT ◀───────┴───────┘         │   │   services)  │
                     │                                    │   └──────────────┘
                     └────────────────────────────────────┘
```

</div>

Read left to right. The **outside world** speaks its own language (its own IDs,
units, file formats). FEWS's job at the boundary is to translate everything into
one **standardized internal representation**, do its work there, and then
translate back out when it exports.

## The five hops, and who governs each

| # | Hop | What happens | Config that governs it |
| --- | --- | --- | --- |
| ① | **Import** | Read an external file, service, or database and pull out raw values + timestamps. | Import module configs (e.g. a time-series import), plus the **filters/data feeds** that describe the source. |
| ② | **ID mapping** | Translate the source's location & parameter names into FEWS's internal names. | `IdMapFiles` + `parameters` + `locationSets`. |
| ③ | **Store** | Write the values into the FEWS data store as a fully-identified **time series**. | Nothing extra to configure — but the series' identity comes from the **time series header** (location, parameter, module, time step, type). |
| ④ | **Process** | Run transformations and models to compute new series (e.g. rating-curve stage→flow, or a hydrological model). | Transformation modules, model adapters (**General Adapter**), and the **workflows** that order them. |
| ⑤ | **Export / display** | Push results to a screen, a threshold check, or back out to a file/service. | Display configs, threshold configs, export modules. |

:::note[The key insight]
Hops ② and ③ are why FEWS feels "indirect." Data never lives in FEWS under its
original name. A river gauge that the source calls `08NL024` becomes,
internally, something like location `RiverX_Downstream`, parameter `Q.obs`, from
module `Import`. **Most "my data disappeared" problems are really "my ID mapping
didn't match" problems.** See [ID mapping](/tasks/id-mapping/).
:::

## Why the internal representation matters

Everything between the import and the export speaks *one* language:

- **One naming scheme.** Every series is identified by the same small set of
  attributes (its [header](/concepts/glossary/#time-series-header)), no matter
  where it came from.
- **One notion of time.** Series have explicit time steps and time zones, so a
  model never has to guess what "hourly" means.
- **One notion of quality.** Every value carries a **flag** (reliable, doubtful,
  unreliable, missing) and can be edited or completed without losing the
  original.

This standardization is the whole point. It's why FEWS can take data from a
dozen incompatible sources and feed them all into the same model without the
model knowing or caring where anything came from.

## Observed vs. forecast: the same pipeline, twice

The diagram above describes **observed** data flowing in. Forecasts flow through
the *same* hops, just starting from a different place:

<div class="diagram">

```text
 OBSERVED:   external source ──▶ import ──▶ map ──▶ store ──▶ [ Q.obs ]
                                                                  │
                                                                  ▼  (used as input / initial state)
 FORECAST:                              model run (workflow) ──▶ [ Q.simulated ]
                                                                  │
                                                                  ▼
                                          display · thresholds · export
```

</div>

A **model run** is just hop ④ producing a *new* time series — one whose type is
"forecast" rather than "observed" and whose timestamps run into the future. It
lands in the same data store, identified by the same kind of header, and is
displayed by the same kind of config. There is no separate "forecast subsystem"
to learn; it's the pipeline you already know, pointed at the future.

## What to take away

- Every config file configures **one hop.** When you're lost, ask: *which hop is
  this?*
- Data is **renamed at the boundary** (hop ②) and lives internally under FEWS
  names — not its source names.
- Internally, everything is a **time series with a full identity**
  ([header](/concepts/glossary/#time-series-header)).
- **Forecasts reuse the same pipeline** as observations; a model run is just a
  processing hop that writes future-dated series.

Next: see how these hops map onto real folders and files in [the configuration
directory →](/concepts/config-directory/)

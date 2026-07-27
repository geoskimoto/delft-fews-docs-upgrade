---
title: Glossary
description: Plain-language definitions of the Delft-FEWS terms the official documentation assumes you already know.
sidebar:
  order: 5
---

The official docs assume this vocabulary. Here it is in plain language. Terms are
grouped by the concept they belong to; within a group they build on each other.

## Data & identity

### Time series
An ordered sequence of timestamped values for **one** quantity at **one** place —
e.g. hourly discharge at a specific gauge. In FEWS a time series is never bare
numbers; it always carries a full identity (its *header*, below).

### Time series header
The identity attached to every series. It answers: *which
[location](#location), which [parameter](#parameter), which
[module](#module) produced it, what [time step](#time-step), and is it
[observed or a forecast](#simulated--forecast-time-series)?* This header is how
FEWS keeps millions of series distinct. Most "I can't find my data" issues are
really "the header I'm querying doesn't match the header that got stored."

### Location
A place things are measured or modelled: a gauge, reservoir, catchment, grid
cell, structure. Every location has an ID and lives in the
[`RegionConfigFiles`](/concepts/config-directory/) vocabulary.

### Location set
A named *group* of locations — "all discharge gauges," "reservoirs in basin X."
Configuration refers to sets so one rule can apply to many locations at once.

### Parameter
The *quantity* being measured or computed: discharge (`Q`), water level (`H`),
precipitation (`P`), temperature (`T`). Parameters are declared once and reused
everywhere.

### Qualifier
An optional extra tag that distinguishes otherwise-identical series — e.g. two
discharge series that differ only by scenario or ensemble treatment. Think of it
as an adjective on a [parameter](#parameter).

### Flag
A quality marker on each value: *reliable, doubtful, unreliable, missing*.
Editing or completing data changes flags without discarding the original values.

### Time step
The spacing between values — hourly, 15-minute, daily, or *non-equidistant*
(irregular). Declared explicitly so nothing has to guess what "hourly" means.

## Naming & translation

### ID mapping
The translation table between an external system's names and FEWS's internal
names, applied at the [import/export boundary](/concepts/data-flow/). External
`08NL024` → internal location `RiverX_Downstream`. Lives in
[`IdMapFiles`](/concepts/config-directory/). See the [task
guide](/tasks/id-mapping/).

### Internal vs. external
**External** = how the outside world names and formats data. **Internal** = the
single standardized FEWS representation everything is converted into on the way
in and out. The whole system's power comes from doing all its work internally.

## Doing work

### Module
A configured unit of work — one import, one transformation, one model run.
Modules are the *verbs*. Each is one file in
[`ModuleConfigFiles`](/concepts/config-directory/).

### Module instance
A specific configured *use* of a module. The same underlying module type can be
instantiated several times with different settings; each instance has its own ID
and its output series are tagged with it.

### Workflow
An **ordered list of modules** to execute — the recipe. Workflows are what get
scheduled and run. Lives in
[`WorkflowFiles`](/concepts/config-directory/).

### Transformation
A module that computes new series from existing ones: gap-filling, unit changes,
rating curves (stage→flow), aggregation, disaggregation. See the [task
guide](/tasks/transformations/).

### General Adapter
The standard bridge to an **external model**. It exports FEWS inputs into the
model's format, launches the model, and imports its results back — so any model
can plug into the same pipeline. See the [task
guide](/tasks/general-adapter/).

## Time & forecasting

### T0 (time zero)
The instant a forecast run is anchored to — the boundary between *imported
history* (before T0) and *simulated future* (after T0). Covered in depth in [the
lifecycle](/concepts/lifecycle/).

### Simulated / forecast time series
A series produced by a [model run](#general-adapter) whose values run forward
from [T0](#t0-time-zero). Stored and displayed exactly like observed series —
only its *type* and time range differ.

### Cold state / warm state
The model's starting condition. A **cold state** is a fixed, pre-defined starting
point; a **warm state** is carried forward from a previous run for greater
accuracy. See [cold vs. warm start](/concepts/lifecycle/#cold-start-vs-warm-start).

### Ensemble
A *set* of forecast series representing uncertainty — many members instead of one
prediction. Members share a header but are distinguished by an ensemble member
index.

## User-facing

### Filter
The browsable tree of data feeds users click through in the FEWS Explorer.
Filters are declared in [`RegionConfigFiles`](/concepts/config-directory/) and
decide *what data users can reach and how it's grouped* — not what exists.

### Display
A configured view of data — chart, table, spatial map, longitudinal profile.
Displays live in
[`DisplayConfigFiles`](/concepts/config-directory/).

### Threshold
A warning level attached to a [location](#location)/[parameter](#parameter)
(e.g. "minor flood at 4.5 m"). Crossing one can raise an event or alert. See the
[task guide](/tasks/thresholds/).

:::tip
Keep this page open in a second tab while you read the task guides — they lean on
these definitions rather than repeating them.
:::

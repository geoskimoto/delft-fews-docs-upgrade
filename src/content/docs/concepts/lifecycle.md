---
title: The forecasting lifecycle
description: What actually happens when a Delft-FEWS forecast runs, step by step, and which configuration file governs each step.
sidebar:
  order: 4
---

You've seen how data *flows* and where config files *live*. This page connects
them in motion: what happens, in order, when a forecast actually runs — and
which config file is in charge at each moment.

## The lifecycle at a glance

A forecast run is a **workflow** executing its modules in sequence. A typical run
looks like this:

<div class="diagram">

```text
  ┌─ 1 ─┐   ┌─ 2 ─┐   ┌─ 3 ─┐   ┌─ 4 ─┐   ┌─ 5 ─┐   ┌─ 6 ─┐   ┌─ 7 ─┐
  │ set │──▶│import│─▶│ pre- │─▶│ run  │─▶│ post-│─▶│ check│─▶│export│
  │ T0  │   │ obs  │  │ proc │  │ model│  │ proc │  │ thr. │  │ /show│
  └─────┘   └──────┘  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘
```

</div>

Each step maps to configuration you now recognize:

| Step | What happens | Governed by |
| --- | --- | --- |
| 1. **Set T0** | Fix the *forecast time zero* — "now" for this run. Everything before T0 is history; everything after is forecast. | The task/run definition (and system time settings). |
| 2. **Import observations** | Pull in the latest observed data through [hops ①–③](/concepts/data-flow/). | Import modules + [ID mapping](/tasks/id-mapping/). |
| 3. **Pre-processing** | Fill gaps, convert stage→flow via rating curves, aggregate, disaggregate. | [Transformation](/tasks/transformations/) modules. |
| 4. **Run the model** | Hand inputs to an external model (HBV, Sacramento, a hydraulic model…), run it, read results back. | The [General Adapter](/tasks/general-adapter/) module. |
| 5. **Post-processing** | Combine members, apply error correction, derive secondary series. | More [transformation](/tasks/transformations/) modules. |
| 6. **Check thresholds** | Compare results against warning levels; raise events where exceeded. | [Threshold](/tasks/thresholds/) configs. |
| 7. **Export / display** | Write results to files/services and surface them in the UI. | Export modules + display configs. |

All seven steps are listed, in order, in a single **workflow** file. The
workflow is the script; the modules are its lines.

## T0: the concept everything pivots on

**T0 (time zero)** is the single most important idea in a FEWS run. It's the
instant the forecast is anchored to:

<div class="diagram">

```text
        ◀──────────── history ────────────┼──────── forecast ────────▶
                                          T0
     observed data lives here          "now"        model output lives here
     (imported, hop ①–③)                            (produced by hop ④)
```

</div>

- Everything **left of T0** is where FEWS expects *observed* data — imports fill
  this region.
- Everything **right of T0** is where the *model* writes its predictions.
- A single time series can straddle T0: observed up to now, forecast beyond.

When people say a forecast "runs at T0 = 06:00," they mean the whole pipeline is
anchored so that 06:00 is the boundary between imported history and simulated
future. Get T0 wrong and the model gets the wrong initial state — a classic
source of subtly-off forecasts.

## Cold start vs. warm start

Step 4 needs the model's **state** (soil moisture, storages, levels) to begin
from. Where that comes from is a lifecycle choice worth knowing early:

- **Cold start** — begin from a fixed, pre-defined state file
  (`ColdStateFiles`). Simple, but the state may not reflect recent conditions.
- **Warm start** — begin from the state saved by a *previous* run. More
  accurate, because the model carries real recent history forward.

Operational systems run warm; you'll often use cold starts while developing and
testing.

## Where "what-if" and reruns fit

Because a run is just a workflow anchored at a T0, you get powerful things almost
for free:

- **Re-run history** by setting T0 to a past time — useful for testing against
  events you already know the outcome of.
- **What-if scenarios** by running the same workflow with modified inputs and
  comparing the resulting forecast series side by side.

Both reuse the exact same pipeline; only the inputs or the T0 change.

## What to take away

- A forecast run is a **workflow** executing modules in a fixed order:
  set T0 → import → pre-process → model → post-process → thresholds → export.
- **T0** divides imported history from simulated future; it anchors the whole
  run.
- The model starts from a **state** — *cold* (fixed file) or *warm* (previous
  run's output).
- **Reruns and what-ifs** are the same pipeline with a different T0 or different
  inputs.

Next: keep the [Glossary](/concepts/glossary/) handy as you move into the
[task guides](/tasks/locations/).

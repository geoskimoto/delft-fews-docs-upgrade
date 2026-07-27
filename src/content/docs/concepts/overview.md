---
title: Core Concepts — Overview
description: The five ideas that make every Delft-FEWS configuration decision make sense.
sidebar:
  order: 1
---

If you learn nothing else before opening a config file, learn this section.
Delft-FEWS has a reputation for being hard to configure. Most of that
difficulty isn't the XML — it's that the XML only makes sense once you hold a
particular mental model in your head. This section installs that model.

## The one-sentence version

> **Delft-FEWS is a machine for moving time series from the outside world,
> through a standardized internal representation, into forecasts and displays —
> and every config file you'll ever write is configuring one hop of that
> journey.**

Read that again. Almost every question — *"where does this value come from?",
"why isn't my data showing up?", "which file do I edit?"* — is really a question
about **which hop** you're looking at.

## The four things FEWS needs to know

Before FEWS can do anything useful, its configuration has to answer four
questions. Every configuration, however large, is mostly these four answers
repeated at scale:

1. **What are the things I'm tracking?** — the *locations* (a gauge, a reservoir,
   a catchment) and *parameters* (discharge, water level, precipitation). See
   [the data-flow model](/concepts/data-flow/).

2. **Where does data come from, and what is it called out there?** — *imports*
   and *ID mapping*, which translate an external system's names into FEWS's
   names.

3. **What should I compute from it?** — *transformations*, *models*, and the
   *workflows* that run them in order.

4. **How should people see it, and where does it go next?** — *displays*,
   *thresholds*, and *exports*.

## How this section is organized

| Page | What it gives you |
| --- | --- |
| [How data flows through FEWS](/concepts/data-flow/) | The central diagram: external source → import → internal time series → process → display/export. The backbone of everything. |
| [The configuration directory](/concepts/config-directory/) | What the folders and files on disk are, and how FEWS finds them. |
| [The forecasting lifecycle](/concepts/lifecycle/) | What actually happens when a forecast runs, and where each config file plugs in. |
| [Glossary](/concepts/glossary/) | Plain-language definitions of the terms the official docs assume you know. |

:::tip
Read them in order the first time. After that, the [Glossary](/concepts/glossary/)
and [data-flow diagram](/concepts/data-flow/) are the two you'll come back to.
:::

## A note on vocabulary

FEWS uses some words in specific ways that trip people up. Three worth knowing
before you continue — full definitions are in the [Glossary](/concepts/glossary/):

- A **time series** in FEWS is never just "some numbers." It's numbers plus a
  full identity: *which location, which parameter, which module produced it,
  which time step, and whether it's observed or forecast.* That identity is
  called a **time series header**, and it's how FEWS keeps millions of series
  straight.
- A **module** is a configured unit of work — an import, a transformation, a
  model run. Modules are the verbs.
- A **workflow** is an ordered list of modules. It's the recipe that says *do
  this, then this, then this.*

Once these click, [open the data-flow model →](/concepts/data-flow/)

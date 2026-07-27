---
title: Why this guide exists
description: What this guide is, who it's for, and how it differs from the official Delft-FEWS Configuration Guide.
---

Delft-FEWS is a powerful system for managing time series and running operational
forecasts — flood, drought, streamflow, and more. Its configuration is
correspondingly deep: hundreds of XML file types, dozens of display widgets, and
model adapters for everything from HBV to LISFLOOD.

The [official Configuration Guide][official] documents all of it. It is
accurate and complete. But it is a **reference manual**, and it says so — it
assumes you already understand "Delft-FEWS and its structure." If you're new,
that's the exact thing you don't have yet.

## What's different here

This guide is built around three ideas:

1. **Concepts before configuration.** You'll get a mental model of how data
   moves through FEWS *before* being asked to edit XML. Almost every
   configuration decision follows naturally once that model is in place. See
   [Core Concepts](/concepts/overview/).

2. **Organized by task, not by schema.** The official docs are organized around
   the config files themselves. But you don't wake up wanting to edit
   `IdMapFiles`; you wake up wanting to *import a time series and see it on a
   chart* — which happens to touch five different files. The [task
   guides](/tasks/locations/) are organized around what you're trying to do.

3. **Examples you can copy.** Every concept and task is grounded in a small,
   complete, copy-pasteable XML example, with the common mistakes called out.

## Who this is for

- Newcomers configuring their first FEWS region.
- Experienced users who want a faster path to a specific answer.
- Anyone who has bounced between the wiki and the `.xsd` schemas one too many
  times and wanted them in the same place.

## What this is *not*

This is not official Deltares documentation, and it doesn't replace the
[official guide][official] or the [XML schemas][schemas] — it points you to both
when you need the exhaustive detail. It also doesn't cover installing or
operating FEWS; the scope here is **configuration** only.

:::tip[Where to go next]
If you're brand new, read [Core Concepts → Overview](/concepts/overview/) next.
If you just want to try something, jump to [Getting
started](/start-here/getting-started/).
:::

[official]: https://publicwiki.deltares.nl/spaces/FEWSDOC/pages/8683900/Configuring+Delft-FEWS+-+Configuration+Guide
[schemas]: https://fews.wldelft.nl/schemas/version1.0/

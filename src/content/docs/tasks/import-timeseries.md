---
title: Import time series
description: Bring external data into the FEWS internal representation.
sidebar:
  order: 3
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

- Choosing an import type (file, web service, database)
- A minimal time-series import, end to end
- How import connects to ID mapping and unit/flag conversion
- Debugging: why imported data does not appear where you expect

## Before you dive in

This task builds on the mental model in
[Core Concepts](/concepts/data-flow/). If you haven't read the
[data-flow model](/concepts/data-flow/) yet, start there — this guide assumes it.

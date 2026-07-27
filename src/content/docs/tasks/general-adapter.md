---
title: Run a model (General Adapter)
description: Bridge FEWS to an external model executable.
sidebar:
  order: 8
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

- The export → run → import cycle the General Adapter performs
- Mapping FEWS series to model input/output files
- Cold vs. warm states in a model run
- Diagnosing a failed or empty model run

## Before you dive in

This task builds on the mental model in
[Core Concepts](/concepts/glossary/#general-adapter). If you haven't read the
[data-flow model](/concepts/data-flow/) yet, start there — this guide assumes it.

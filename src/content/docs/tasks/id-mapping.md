---
title: Map external IDs (ID mapping)
description: Translate an external system’s names into FEWS internal names.
sidebar:
  order: 4
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

- The `IdMapFiles` structure: external ⇆ internal location & parameter
- Exact maps vs. pattern/prefix maps for bulk translation
- Why a mismatched map is the #1 cause of “missing” data
- Testing a map in isolation before running a full import

## Before you dive in

This task builds on the mental model in
[Core Concepts](/concepts/data-flow/). If you haven't read the
[data-flow model](/concepts/data-flow/) yet, start there — this guide assumes it.

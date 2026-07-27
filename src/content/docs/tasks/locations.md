---
title: Define locations & location sets
description: Declare the places FEWS tracks, and group them into sets.
sidebar:
  order: 1
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

- The `Locations` file: IDs, names, coordinates, and attributes
- `LocationSets`: static lists, and sets built from a CSV or shapefile
- Referencing sets from other config so one rule covers many places
- Common gotchas: duplicate IDs, missing coordinates, set vs. single ID

## Before you dive in

This task builds on the mental model in
[Core Concepts](/concepts/config-directory/). If you haven't read the
[data-flow model](/concepts/data-flow/) yet, start there — this guide assumes it.

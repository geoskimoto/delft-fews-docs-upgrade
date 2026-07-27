---
title: Original docs & schemas
description: The authoritative Delft-FEWS sources this guide is based on and points back to.
sidebar:
  order: 1
---

This guide is a friendlier front-end to the official material — not a
replacement. When you need exhaustive, authoritative detail, go to the source.

## Primary sources

- **[Configuring Delft-FEWS — Configuration Guide][official]**
  The official reference manual on the Deltares public wiki. Complete and
  authoritative; organized by config file and module. This is the canonical word
  on every option.

- **[XML schemas (`version1.0`)][schemas]**
  The machine-readable grammar (`.xsd`) for every FEWS config file. If you want
  to know *exactly* which elements and attributes are valid, this is the source
  of truth. Our reference pages will summarize these with added context.

## How this guide relates to them

| If you want… | Go to… |
| --- | --- |
| The mental model / where to start | This guide's [Core Concepts](/concepts/overview/) |
| Step-by-step for a specific task | This guide's [Task guides](/tasks/locations/) |
| Every option for a config file | The [official guide][official] |
| The exact valid XML grammar | The [schemas][schemas] |

## A note on versions

FEWS evolves, and so do its schemas and docs. This guide targets the concepts and
`version1.0` schema family, which are stable across most current deployments.
Where a detail is version-sensitive, we'll say so. When in doubt, the
[official guide][official] tracks the latest release.

[official]: https://publicwiki.deltares.nl/spaces/FEWSDOC/pages/8683900/Configuring+Delft-FEWS+-+Configuration+Guide
[schemas]: https://fews.wldelft.nl/schemas/version1.0/

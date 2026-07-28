// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// https://astro.build/config
export default defineConfig({
  site: 'http://localhost:4321',
  integrations: [
    starlight({
      title: 'Delft-FEWS Config Guide',
      description:
        'A clearer, task-oriented guide to configuring Delft-FEWS — the streamflow forecasting and time series management system.',
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      // Show an "edit this page" style banner-free, clean reading experience.
      customCss: ['./src/styles/custom.css'],
      sidebar: [
        {
          label: 'Start Here',
          items: [
            { label: 'Why this guide exists', slug: 'start-here/introduction' },
            { label: 'Getting started (tutorial)', slug: 'start-here/getting-started' },
          ],
        },
        {
          label: 'Core Concepts',
          items: [
            { label: 'Overview', slug: 'concepts/overview' },
            { label: 'How data flows through FEWS', slug: 'concepts/data-flow' },
            { label: 'The configuration directory', slug: 'concepts/config-directory' },
            { label: 'The forecasting lifecycle', slug: 'concepts/lifecycle' },
            { label: 'Glossary', slug: 'concepts/glossary' },
          ],
        },
        {
          label: 'Configuration Tasks',
          items: [
            { label: 'Define locations & location sets', slug: 'tasks/locations' },
            { label: 'Define parameters & units', slug: 'tasks/parameters' },
            { label: 'Import time series', slug: 'tasks/import-timeseries' },
            { label: 'Map external IDs (ID mapping)', slug: 'tasks/id-mapping' },
            { label: 'Filters & display groups', slug: 'tasks/filters-displays' },
            { label: 'Thresholds & warnings', slug: 'tasks/thresholds' },
            { label: 'Transformations', slug: 'tasks/transformations' },
            { label: 'Run a model (General Adapter)', slug: 'tasks/general-adapter' },
            { label: 'Workflows & scheduling', slug: 'tasks/workflows' },
            { label: 'Export data', slug: 'tasks/export' },
          ],
        },
        {
          label: 'Config File Reference',
          items: [
            { label: 'How to read a reference page', slug: 'reference/how-to-read' },
            { label: 'Locations file', slug: 'reference/locations' },
            { label: 'Location sets file', slug: 'reference/locationsets' },
            { label: 'Parameters file', slug: 'reference/parameters' },
            { label: 'ID map file', slug: 'reference/idmap' },
            { label: 'Filters file', slug: 'reference/filters' },
            { label: 'Display groups file', slug: 'reference/displaygroups' },
            { label: 'Thresholds file', slug: 'reference/thresholdgroups' },
            { label: 'Threshold value sets file', slug: 'reference/thresholdvaluesets' },
            { label: 'Threshold warning levels file', slug: 'reference/thresholdwarninglevels' },
            { label: 'Import module file', slug: 'reference/timeseriesimportrun' },
            { label: 'Export module file', slug: 'reference/timeseriesexportrun' },
            { label: 'General Adapter file', slug: 'reference/generaladapterrun' },
            { label: 'Workflow file', slug: 'reference/workflow' },
            { label: 'Workflow descriptors file', slug: 'reference/workflowdescriptors' },
          ],
        },
        {
          label: 'Resources',
          items: [
            { label: 'Original docs & schemas', slug: 'resources/sources' },
          ],
        },
      ],
    }),
  ],
});

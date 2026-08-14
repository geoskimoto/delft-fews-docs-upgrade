// Regenerate schema-derived field data for every reference page.
// Runs before `dev` and `build` so the tables never drift from the schemas.
//
// To add a reference page: vendor its .xsd into schemas/ (with any includes)
// and add an entry here, then create the matching reference/<element>.mdx page.
import { generateToFile } from './schema-to-fields.mjs';

const TARGETS = [
  // Region-config vocabulary
  { schema: 'locations.xsd', element: 'locations' },
  { schema: 'locationSets.xsd', element: 'locationSets' },
  { schema: 'parameters.xsd', element: 'parameters' },
  { schema: 'idMap.xsd', element: 'idMap' },
  { schema: 'filters.xsd', element: 'filters' },
  // Thresholds (three related files)
  { schema: 'thresholds.xsd', element: 'thresholdGroups' },
  { schema: 'thresholdValueSets.xsd', element: 'thresholdValueSets' },
  { schema: 'thresholdWarningLevels.xsd', element: 'thresholdWarningLevels' },
  // Displays
  { schema: 'displayGroups.xsd', element: 'displayGroups' },
  // Module-run files
  { schema: 'timeSeriesImportRun.xsd', element: 'timeSeriesImportRun' },
  { schema: 'timeSeriesExportRun.xsd', element: 'timeSeriesExportRun' },
  { schema: 'generalAdapterRun.xsd', element: 'generalAdapterRun' },
  // Workflows
  { schema: 'workflow.xsd', element: 'workflow' },
  { schema: 'workflowDescriptors.xsd', element: 'workflowDescriptors' },
  // Module registration (referenced by workflow steps)
  { schema: 'moduleDescriptors.xsd', element: 'moduleDescriptors' },
  { schema: 'moduleInstanceDescriptors.xsd', element: 'moduleInstanceDescriptors' },
  // Explorer tree
  { schema: 'topology.xsd', element: 'topology' },
  // Other common region-config files
  { schema: 'qualifiers.xsd', element: 'qualifiers' },
  { schema: 'validationRuleSets.xsd', element: 'validationRuleSets' },
  { schema: 'ratingCurves.xsd', element: 'ratingCurves' },
  { schema: 'interpolationSets.xsd', element: 'interpolationSets' },
  // NB: transformationModule.xsd is intentionally omitted — its type graph is
  // ~400 types / ~3000 fields, too large for one readable page. The
  // Transformations task guide defers the full catalogue to the schema instead.
];

let failed = 0;
for (const t of TARGETS) {
  try {
    generateToFile(t.schema, t.element);
  } catch (err) {
    failed++;
    console.error(`✗ ${t.element} (${t.schema}): ${err.message}`);
  }
}
if (failed) {
  console.error(`\n${failed} schema target(s) failed.`);
  process.exit(1);
}
console.log(`\nGenerated ${TARGETS.length} schema field references.`);

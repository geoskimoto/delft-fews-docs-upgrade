// Regenerate schema-derived field data for every reference page.
// Runs before `dev` and `build` so the tables never drift from the schemas.
//
// To add a single-root reference page: vendor its .xsd into schemas/ (with
// any includes) and add a { schema, element } entry to TARGETS, then create
// the matching reference/<element>.mdx page.
//
// To add a multi-root page (splitting one oversized schema across several
// topical pages — see transformationTypes.xsd below): add a
// { schema, multiRoot: { pageId, categories, stopTypes? } } entry, where
// `categories` is a list of keys into TRANSFORMATION_CATEGORIES.
import { generateToFile, generateMultiRootToFile } from './schema-to-fields.mjs';

// transformationTypes.xsd's <transformation> picks one of 43 function
// categories (FunctionChoiceGroup). Each is its own reference page (or
// grouped with siblings); this map is the single source of truth for
// category key -> concrete complexType name, shared by every transform-*
// multiRoot target below and by the "basics" page's stopTypes list.
const TRANSFORMATION_CATEGORIES = {
  accumulation: 'AccumulationTransformationChoiceComplexType',
  adjust: 'AdjustTransformationChoiceComplexType',
  aggregation: 'AggregationTransformationChoiceComplexType',
  altitude: 'AltitudeTransformationChoiceComplexType',
  conditional: 'ConditionalTransformationChoiceComplexType',
  copy: 'CopyTransformationChoiceComplexType',
  custom: 'CustomTransformationChoiceComplexType',
  deaccumulation: 'DeAccumulationTransformationChoiceComplexType',
  disaggregation: 'DisaggregationTransformationChoiceComplexType',
  dischargeStage: 'DischargeStageTransformationChoiceComplexType',
  events: 'EventsTransformationChoiceComplexType',
  filter: 'FilterTransformationChoiceComplexType',
  generation: 'GenerationChoiceComplexType',
  generationEnsemble: 'GenerationEnsembleChoiceComplexType',
  gradient: 'GradientChoiceComplexType',
  interpolationSerial: 'InterpolationSerialTransformationChoiceComplexType',
  interpolationSpatial: 'InterpolationSpatialTransformationChoiceComplexType',
  lookup: 'LookupTransformationChoiceComplexType',
  merge: 'MergeTransformationChoiceComplexType',
  multipleLocationAttributes: 'MultipleLocationAttributesComplexType',
  moisture: 'MoistureTransformationChoiceComplexType',
  performanceIndicatorsLeadTimeAccuracy: 'PerformanceIndicatorsLeadTimeAccuracyChoiceComplexType',
  precipitation: 'PrecipitationTransformationChoiceComplexType',
  profile: 'ProfileTransformationChoiceComplexType',
  regression: 'RegressieTransformationChoiceComplexType',
  review: 'ReviewTransformationChoiceComplexType',
  rotation: 'RotationTransformationChoiceComplexType',
  sample: 'SampleTransformationChoiceComplexType',
  selection: 'SelectionTransformationChoiceComplexType',
  stageDischarge: 'StageDischargeTransformationChoiceComplexType',
  statisticsSameAttributeValue: 'StatisticsSameAttributeValueChoiceComplexType',
  statisticsChildrenLocations: 'StatisticsChildLocationsChoiceComplexType',
  statisticsRelatedLocations: 'StatisticsRelatedLocationsChoiceComplexType',
  statisticsValueProperties: 'StatisticsValuePropertiesChoiceComplexType',
  statisticsEnsemble: 'StatisticsEnsembleChoiceComplexType',
  statisticsPeriodic: 'StatisticsPeriodicChoiceComplexType',
  statisticsSerial: 'StatisticsSerialChoiceComplexType',
  statisticsSummary: 'StatisticsSummaryChoiceComplexType',
  statisticsVerticalLayers: 'StatisticsVerticalLayersChoiceComplexType',
  structure: 'StructureTransformationChoiceComplexType',
  timeShift: 'TimeShiftTransformationChoiceComplexType',
  user: 'UserTransformationChoiceComplexType',
  wave: 'WaveTransformationChoiceComplexType',
};

const categoryRoots = (...keys) =>
  keys.map((key) => {
    const typeName = TRANSFORMATION_CATEGORIES[key];
    if (!typeName) throw new Error(`Unknown transformation category "${key}"`);
    return { typeName, label: key };
  });

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
  { schema: 'unitConversions.xsd', element: 'unitConversions' },
  { schema: 'moduleInstanceSets.xsd', element: 'moduleInstanceSets' },
  { schema: 'coldModuleInstanceStateGroups.xsd', element: 'coldModuleInstanceStateGroups' },
  { schema: 'grids.xsd', element: 'grids' },
  { schema: 'structures.xsd', element: 'structures' },
  // transformationModule.xsd: ~400 types / ~3000 fields total, too large for
  // one page — split across a "basics" page (root file mechanics, pruned at
  // the 43-category boundary) and one multi-root page per topical group of
  // categories. Only the first two groups are wired up so far (pilot); the
  // remaining categories in TRANSFORMATION_CATEGORIES aren't reachable from
  // any page yet.
  {
    schema: 'transformationModule.xsd',
    element: 'transformationModule',
    pageId: 'transformationBasics',
    stopTypes: Object.values(TRANSFORMATION_CATEGORIES),
  },
  {
    schema: 'transformationTypes.xsd',
    multiRoot: {
      pageId: 'transformTemporal',
      roots: categoryRoots('accumulation', 'deaccumulation', 'aggregation', 'disaggregation', 'timeShift', 'gradient', 'copy'),
    },
  },
  {
    schema: 'transformationTypes.xsd',
    multiRoot: {
      pageId: 'transformInterpolation',
      roots: categoryRoots('interpolationSerial', 'interpolationSpatial', 'altitude', 'rotation', 'multipleLocationAttributes'),
    },
  },
  {
    schema: 'transformationTypes.xsd',
    multiRoot: {
      pageId: 'transformStatistics',
      roots: categoryRoots('statisticsSameAttributeValue', 'statisticsChildrenLocations', 'statisticsRelatedLocations', 'statisticsValueProperties', 'statisticsEnsemble', 'statisticsPeriodic', 'statisticsSerial', 'statisticsSummary', 'statisticsVerticalLayers'),
    },
  },
];

let failed = 0;
for (const t of TARGETS) {
  try {
    if (t.multiRoot) {
      generateMultiRootToFile(t.schema, t.multiRoot.roots, t.multiRoot.pageId, { stopTypes: t.multiRoot.stopTypes });
    } else {
      generateToFile(t.schema, t.element, t.pageId ? `src/data/schema/${t.pageId}.json` : undefined, { stopTypes: t.stopTypes });
    }
  } catch (err) {
    failed++;
    console.error(`✗ ${t.pageId || t.multiRoot?.pageId || t.element} (${t.schema}): ${err.message}`);
  }
}
if (failed) {
  console.error(`\n${failed} schema target(s) failed.`);
  process.exit(1);
}
console.log(`\nGenerated ${TARGETS.length} schema field references.`);

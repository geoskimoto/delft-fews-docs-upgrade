// Regenerate schema-derived field data for every reference page.
// Runs before `dev` and `build` so the tables never drift from the schemas.
//
// To add a reference page: vendor its .xsd into schemas/ (with any includes)
// and add an entry here, then create the matching reference/<element>.mdx page.
import { generateToFile } from './schema-to-fields.mjs';

const TARGETS = [
  { schema: 'locations.xsd', element: 'locations' },
  { schema: 'parameters.xsd', element: 'parameters' },
  { schema: 'idMap.xsd', element: 'idMap' },
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

// Prototype: turn a FEWS XSD element into a structured field reference (JSON).
//
// Usage:  node scripts/schema-to-fields.mjs <schemaFile> <rootElement> [outFile]
// Example: node scripts/schema-to-fields.mjs locations.xsd locations \
//            src/data/schema/locations.json
//
// Approach: FEWS schemas use the default XML-Schema namespace (tags are bare:
// <element>, <complexType>, …) and a single `<include>` of sharedTypes.xsd.
// We load the target schema + every included schema, index all named
// complexType / simpleType / attributeGroup / group definitions, then walk the
// root element's type, resolving references, cardinality, enumerations, and the
// schema's own <documentation> annotations.

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { XMLParser } from 'fast-xml-parser';

// Resolve paths relative to the project root (this file's parent's parent),
// so the script works regardless of the current working directory.
const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SCHEMA_DIR = join(PROJECT_ROOT, 'schemas');

const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: '@_',
  textNodeName: '#text',
  parseTagValue: false,
  parseAttributeValue: false,
  trimValues: true,
  isArray: (name) =>
    ['element', 'complexType', 'simpleType', 'attribute', 'attributeGroup',
     'group', 'enumeration', 'include'].includes(name),
});

const arr = (x) => (Array.isArray(x) ? x : x == null ? [] : [x]);
const stripNs = (t) => (t ? String(t).replace(/^[^:]+:/, '') : t);

function docOf(node) {
  if (!node || !node.annotation) return undefined;
  const ann = arr(node.annotation)[0];
  if (!ann || ann.documentation == null) return undefined;
  const d = arr(ann.documentation)[0];
  const text = typeof d === 'object' ? d['#text'] : d;
  if (!text) return undefined;
  return String(text).replace(/\s+/g, ' ').trim();
}

// ---- Load schema + includes into flat registries -------------------------
function loadSchema(file, reg, seen) {
  const abs = join(SCHEMA_DIR, file);
  if (seen.has(abs)) return;
  seen.add(abs);
  const root = parser.parse(readFileSync(abs, 'utf8')).schema;
  for (const ct of arr(root.complexType)) reg.complexTypes.set(ct['@_name'], ct);
  for (const st of arr(root.simpleType)) reg.simpleTypes.set(st['@_name'], st);
  for (const ag of arr(root.attributeGroup)) if (ag['@_name']) reg.attrGroups.set(ag['@_name'], ag);
  for (const g of arr(root.group)) if (g['@_name']) reg.groups.set(g['@_name'], g);
  for (const el of arr(root.element)) if (el['@_name']) reg.elements.set(el['@_name'], el);
  for (const inc of arr(root.include)) if (inc['@_schemaLocation']) loadSchema(inc['@_schemaLocation'], reg, seen);
}

function newRegistry() {
  return {
    complexTypes: new Map(), simpleTypes: new Map(),
    attrGroups: new Map(), groups: new Map(), elements: new Map(),
  };
}

// ---- Enumerations --------------------------------------------------------
function enumValues(reg, typeName) {
  const st = reg.simpleTypes.get(stripNs(typeName));
  if (!st || !st.restriction) return undefined;
  const vals = arr(st.restriction.enumeration).map((e) => e['@_value']);
  return vals.length ? vals : undefined;
}

// ---- Cardinality ---------------------------------------------------------
function cardinality(el) {
  const min = el['@_minOccurs'] != null ? Number(el['@_minOccurs']) : 1;
  const maxRaw = el['@_maxOccurs'];
  const repeatable = maxRaw === 'unbounded' || (maxRaw != null && Number(maxRaw) > 1);
  return { required: min >= 1, repeatable };
}

// ---- Collect the <element> children of a complexType (through sequence/
//      choice/all, extension bases, and group refs) ------------------------
function collectElements(reg, ct, acc = [], guard = new Set()) {
  if (!ct) return acc;
  // complexContent → extension(base) then local particles
  if (ct.complexContent && ct.complexContent.extension) {
    const ext = ct.complexContent.extension;
    const base = reg.complexTypes.get(stripNs(ext['@_base']));
    if (base && !guard.has(ext['@_base'])) {
      guard.add(ext['@_base']);
      collectElements(reg, base, acc, guard);
    }
    collectParticles(reg, ext, acc, guard);
    return acc;
  }
  collectParticles(reg, ct, acc, guard);
  return acc;
}

function collectParticles(reg, container, acc, guard) {
  for (const wrapper of ['sequence', 'choice', 'all']) {
    const w = container[wrapper];
    if (!w) continue;
    for (const w2 of arr(w)) {
      for (const el of arr(w2.element)) acc.push(el);
      // nested sequence/choice
      collectParticles(reg, w2, acc, guard);
      // group refs
      for (const g of arr(w2.group)) {
        const def = reg.groups.get(stripNs(g['@_ref']));
        if (def && !guard.has(g['@_ref'])) {
          guard.add(g['@_ref']);
          collectParticles(reg, def, acc, guard);
        }
      }
    }
  }
}

// ---- Attributes of a complexType (direct + via attributeGroup) -----------
function collectAttributes(reg, ct, acc = []) {
  if (!ct) return acc;
  const host = ct.complexContent && ct.complexContent.extension
    ? ct.complexContent.extension
    : ct;
  for (const a of arr(host.attribute)) {
    acc.push({
      name: a['@_name'] || stripNs(a['@_ref']),
      type: stripNs(a['@_type']),
      use: a['@_use'] || 'optional',
      fixed: a['@_fixed'],
      default: a['@_default'],
      doc: docOf(a),
    });
  }
  for (const ag of arr(host.attributeGroup)) {
    const def = reg.attrGroups.get(stripNs(ag['@_ref']));
    if (def) collectAttributes(reg, def, acc);
  }
  if (ct.complexContent && ct.complexContent.extension) {
    const base = reg.complexTypes.get(stripNs(ct.complexContent.extension['@_base']));
    if (base) collectAttributes(reg, base, acc);
  }
  return acc;
}

// ---- Build ONE field (no recursion). Complex fields carry a `typeRef` that
//      links to that type's own table, so every table stays shallow. --------
function buildField(reg, el) {
  const name = el['@_name'];
  // Inline simpleType with enumeration?
  let typeName = el['@_type'];
  let enumVals;
  if (!typeName && el.simpleType && el.simpleType.restriction) {
    enumVals = arr(el.simpleType.restriction.enumeration).map((e) => e['@_value']);
    typeName = stripNs(el.simpleType.restriction['@_base']) || 'string';
  }
  const shortType = stripNs(typeName);
  if (!enumVals) enumVals = enumValues(reg, typeName);

  const { required, repeatable } = cardinality(el);
  const field = {
    name,
    type: shortType || (el.complexType ? '(inline)' : 'string'),
    required,
    repeatable,
    doc: docOf(el),
  };
  if (enumVals && enumVals.length) {
    field.kind = 'enum';
    field.enumValues = enumVals;
    return field;
  }

  const ct = reg.complexTypes.get(shortType) || el.complexType;
  const isComplex = !!ct && (ct.sequence || ct.choice || ct.all || ct.complexContent);
  if (isComplex) {
    field.kind = 'complex';
    // Anonymous inline complexType → synthesise a stable name for its table.
    field.typeRef = shortType || `${name}_inline`;
    if (!reg.complexTypes.has(field.typeRef) && el.complexType) {
      reg.complexTypes.set(field.typeRef, el.complexType);
    }
  } else {
    field.kind = 'scalar';
  }
  return field;
}

// Build the field list + attributes for a single complex type, and report the
// complex typeRefs it points at (so the caller can traverse the graph).
function buildType(reg, typeName) {
  const ct = reg.complexTypes.get(typeName);
  if (!ct) return null;
  const fields = collectElements(reg, ct).map((el) => buildField(reg, el));
  const refs = fields.filter((f) => f.kind === 'complex' && f.typeRef).map((f) => f.typeRef);
  return {
    node: { doc: docOf(ct), attributes: collectAttributes(reg, ct), fields },
    refs,
  };
}

// ---- Main: emit the root type plus every complex type reachable from it,
//      each as its own table keyed by type name (BFS, cycle-safe). ----------
export function generate(schemaFile, rootElementName) {
  const reg = newRegistry();
  loadSchema(schemaFile, reg, new Set());

  const rootEl = reg.elements.get(rootElementName);
  if (!rootEl) throw new Error(`Root element <${rootElementName}> not found in ${schemaFile}`);
  const rootType = stripNs(rootEl['@_type']);
  if (!reg.complexTypes.get(rootType)) throw new Error(`Type ${rootEl['@_type']} not found`);

  const types = {};
  const queue = [rootType];
  const seen = new Set(queue);
  while (queue.length) {
    const t = queue.shift();
    const built = buildType(reg, t);
    if (!built) continue;
    types[t] = built.node;
    for (const ref of built.refs) {
      if (!seen.has(ref)) { seen.add(ref); queue.push(ref); }
    }
  }

  return {
    element: rootElementName,
    rootType,
    schemaFile,
    schemaUrl: `https://fews.wldelft.nl/schemas/version1.0/${schemaFile}`,
    doc: docOf(rootEl) || docOf(reg.complexTypes.get(rootType)),
    types,
  };
}

// Write a generated result to src/data/schema (or an explicit path).
export function generateToFile(schemaFile, rootElement, outFile) {
  const result = generate(schemaFile, rootElement);
  const out = outFile
    ? resolve(process.cwd(), outFile)
    : join(PROJECT_ROOT, 'src/data/schema', `${rootElement}.json`);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, JSON.stringify(result, null, 2));
  const typeNames = Object.keys(result.types);
  const totalFields = typeNames.reduce((n, t) => n + result.types[t].fields.length, 0);
  console.log(`✓ ${rootElement}: ${typeNames.length} types, ${totalFields} fields total ` +
    `(root <${rootElement}> → ${result.types[result.rootType].fields.length} fields) → ${out}`);
  return result;
}

// ---- CLI (only when run directly, not when imported) ---------------------
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [schemaFile, rootElement, outFile] = process.argv.slice(2);
  if (!schemaFile || !rootElement) {
    console.error('usage: node scripts/schema-to-fields.mjs <schemaFile> <rootElement> [outFile]');
    process.exit(1);
  }
  generateToFile(schemaFile, rootElement, outFile);
}

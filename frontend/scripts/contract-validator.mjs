export const MAX_SAFE_MINOR = 9007199254740991;
export const HTTP_METHODS = ["get", "post", "put", "patch", "delete"];

function resolveRef(ref, rootSpec) {
  if (!ref.startsWith("#/")) {
    throw new Error(`Unsupported external $ref: ${ref}`);
  }
  let target = rootSpec;
  for (const part of ref.slice(2).split("/")) {
    if (!target || typeof target !== "object" || !(part in target)) {
      throw new Error(`Broken $ref: ${ref}`);
    }
    target = target[part];
  }
  if (!target || typeof target !== "object" || Array.isArray(target)) {
    throw new Error(`Non-object $ref target: ${ref}`);
  }
  return target;
}

function isValidIsoDate(value) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return false;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  const dt = new Date(Date.UTC(y, mo - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === mo - 1 && dt.getUTCDate() === d;
}

function isValidIsoDateTime(value) {
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?$/.exec(
    value,
  );
  if (!m) return false;
  if (!isValidIsoDate(m[1])) return false;
  const hh = Number(m[2]);
  const mm = Number(m[3]);
  const ss = Number(m[4]);
  if (hh > 23 || mm > 59 || ss > 59) return false;
  return !Number.isNaN(Date.parse(value.replace(" ", "T")));
}

function matchesFormat(value, fmt) {
  if (fmt === "date") return isValidIsoDate(value);
  if (fmt === "date-time") return isValidIsoDateTime(value);
  return true;
}

export function validateAgainstSchema(
  value,
  schema,
  rootSpec,
  { allowExtraProperties = false, path = "$" } = {},
) {
  if (!schema || typeof schema !== "object") return [];

  if (typeof schema.$ref === "string") {
    const target = resolveRef(schema.$ref, rootSpec);
    return validateAgainstSchema(value, target, rootSpec, { allowExtraProperties, path });
  }

  if (Array.isArray(schema.anyOf)) {
    const branchErrors = [];
    for (const branch of schema.anyOf) {
      const errs = validateAgainstSchema(value, branch, rootSpec, { allowExtraProperties, path });
      if (errs.length === 0) return [];
      branchErrors.push(errs);
    }
    const joined = branchErrors.map((errs) => errs[0]).filter(Boolean).join(" | ");
    return [`${path}: failed anyOf (${joined})`];
  }

  if (Array.isArray(schema.oneOf)) {
    let matches = 0;
    for (const branch of schema.oneOf) {
      if (validateAgainstSchema(value, branch, rootSpec, { allowExtraProperties, path }).length === 0) {
        matches += 1;
      }
    }
    if (matches !== 1) {
      return [`${path}: expected exactly 1 oneOf match, got ${matches}`];
    }
    return [];
  }

  if (Array.isArray(schema.allOf)) {
    const errs = [];
    for (const branch of schema.allOf) {
      errs.push(...validateAgainstSchema(value, branch, rootSpec, { allowExtraProperties, path }));
    }
    return errs;
  }

  if ("const" in schema && value !== schema.const) {
    return [`${path}: expected const ${JSON.stringify(schema.const)}, got ${JSON.stringify(value)}`];
  }

  if (Array.isArray(schema.enum) && !schema.enum.includes(value)) {
    return [
      `${path}: value ${JSON.stringify(value)} not in enum ${JSON.stringify(schema.enum)}`,
    ];
  }

  if (schema.type !== undefined) {
    const allowed = Array.isArray(schema.type) ? schema.type : [schema.type];
    const typeOk = allowed.some((t) => {
      if (t === "null") return value === null;
      if (t === "boolean") return typeof value === "boolean";
      if (t === "integer") return typeof value === "number" && Number.isInteger(value);
      if (t === "number") return typeof value === "number" && Number.isFinite(value);
      if (t === "string") return typeof value === "string";
      if (t === "array") return Array.isArray(value);
      if (t === "object") return typeof value === "object" && value !== null && !Array.isArray(value);
      return false;
    });
    if (!typeOk) {
      const actual = value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
      return [`${path}: expected type ${JSON.stringify(schema.type)}, got ${actual}`];
    }
  }

  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      return [`${path}: string length ${value.length} < minLength ${schema.minLength}`];
    }
    if (schema.maxLength !== undefined && value.length > schema.maxLength) {
      return [`${path}: string length ${value.length} > maxLength ${schema.maxLength}`];
    }
    if (schema.pattern !== undefined && !new RegExp(schema.pattern).test(value)) {
      return [`${path}: string ${JSON.stringify(value)} does not match pattern ${schema.pattern}`];
    }
    if (schema.format !== undefined && !matchesFormat(value, schema.format)) {
      return [`${path}: string ${JSON.stringify(value)} does not match format ${schema.format}`];
    }
  }

  if (typeof value === "number" && Number.isFinite(value)) {
    if (schema.minimum !== undefined && value < schema.minimum) {
      return [`${path}: ${value} < minimum ${schema.minimum}`];
    }
    if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) {
      return [`${path}: ${value} <= exclusiveMinimum ${schema.exclusiveMinimum}`];
    }
    if (schema.maximum !== undefined && value > schema.maximum) {
      return [`${path}: ${value} > maximum ${schema.maximum}`];
    }
    if (schema.exclusiveMaximum !== undefined && value >= schema.exclusiveMaximum) {
      return [`${path}: ${value} >= exclusiveMaximum ${schema.exclusiveMaximum}`];
    }
  }

  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      return [`${path}: array length ${value.length} < minItems ${schema.minItems}`];
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      return [`${path}: array length ${value.length} > maxItems ${schema.maxItems}`];
    }
    if (schema.items && typeof schema.items === "object") {
      const errs = [];
      for (let i = 0; i < value.length; i += 1) {
        errs.push(
          ...validateAgainstSchema(value[i], schema.items, rootSpec, {
            allowExtraProperties,
            path: `${path}[${i}]`,
          }),
        );
      }
      return errs;
    }
  }

  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const errs = [];
    const hasDeclaredProps = "properties" in schema;
    const props = schema.properties ?? {};
    for (const reqKey of schema.required ?? []) {
      if (!(reqKey in value)) {
        errs.push(`${path}.${reqKey}: missing required property`);
      }
    }
    const addProps = schema.additionalProperties;
    for (const [key, item] of Object.entries(value)) {
      if (key in props) {
        errs.push(
          ...validateAgainstSchema(item, props[key], rootSpec, {
            allowExtraProperties,
            path: `${path}.${key}`,
          }),
        );
      } else if (
        addProps === true ||
        (addProps !== false && (allowExtraProperties || !hasDeclaredProps))
      ) {
        continue;
      } else if (addProps && typeof addProps === "object") {
        errs.push(
          ...validateAgainstSchema(item, addProps, rootSpec, {
            allowExtraProperties,
            path: `${path}.${key}`,
          }),
        );
      } else {
        errs.push(`${path}.${key}: unexpected property not declared in schema`);
      }
    }
    return errs;
  }

  return [];
}

export function validateOpenApiInvariants(spec) {
  const errors = [];
  const schemas = spec.components?.schemas ?? {};

  function walkRefs(node, where) {
    if (Array.isArray(node)) {
      for (const item of node) walkRefs(item, where);
    } else if (node && typeof node === "object") {
      if (typeof node.$ref === "string") {
        try {
          resolveRef(node.$ref, spec);
        } catch (err) {
          errors.push(`${where}: ${err instanceof Error ? err.message : String(err)}`);
        }
      }
      for (const val of Object.values(node)) walkRefs(val, where);
    }
  }

  walkRefs(spec.paths ?? {}, "paths");
  walkRefs(schemas, "components.schemas");

  for (const [path, methods] of Object.entries(spec.paths ?? {}).sort()) {
    for (const [method, op] of Object.entries(methods).sort()) {
      if (!HTTP_METHODS.includes(method) || !op || typeof op !== "object") continue;
      const paramNames = new Set((op.parameters ?? []).map((p) => String(p.name).toLowerCase()));
      if (paramNames.has("x-dev-member-id") || paramNames.has("authorization")) {
        errors.push(`${method.toUpperCase()} ${path}: exposes forbidden auth header param`);
      }
      if (Array.isArray(op.security) && op.security.length > 0) {
        const schemes = new Set(op.security.map((s) => Object.keys(s)[0]));
        if (schemes.size !== 2 || !schemes.has("SessionCookie") || !schemes.has("BearerToken")) {
          errors.push(`${method.toUpperCase()} ${path}: unexpected security schemes`);
        }
        const resps = op.responses ?? {};
        if (!("401" in resps) || !("403" in resps)) {
          errors.push(`${method.toUpperCase()} ${path}: missing 401/403 responses`);
        }
      }
    }
  }

  for (const [schemaName, schema] of Object.entries(schemas).sort()) {
    for (const [propName, propSchema] of Object.entries(schema.properties ?? {})) {
      if (propName.endsWith("Cents")) {
        const options = propSchema.anyOf ?? [propSchema];
        const intOpt = options.find((opt) => opt?.type === "integer");
        if (!intOpt || intOpt.maximum !== MAX_SAFE_MINOR) {
          errors.push(`${schemaName}.${propName}: missing MAX_SAFE_MINOR upper bound`);
        }
      }
      if (propName === "currency" || propName === "baseCurrency" || propName === "accountCurrency") {
        const options = propSchema.anyOf ?? [propSchema];
        const strOpt = options.find((opt) => opt?.type === "string");
        if (!strOpt || strOpt.pattern !== "^[A-Z]{3}$") {
          errors.push(`${schemaName}.${propName}: missing ISO 4217 currency pattern`);
        }
      }
    }
  }

  return errors;
}

export function parseSchemaGenPaths(tsText) {
  const m = /export interface paths \{([\s\S]*?)\r?\n\}/.exec(tsText);
  if (!m) return {};
  const body = m[1];
  const matches = [...body.matchAll(/^    "([^"]+)": \{/gm)];
  const result = {};
  for (let i = 0; i < matches.length; i += 1) {
    const start = matches[i].index + matches[i][0].length;
    const end = i + 1 < matches.length ? matches[i + 1].index : body.length;
    const block = body.slice(start, end);
    const methods = new Set();
    for (const method of HTTP_METHODS) {
      if (new RegExp(`^        ${method}: operations\\[`, "m").test(block)) {
        methods.add(method);
      }
    }
    result[matches[i][1]] = methods;
  }
  return result;
}

export function parseSchemaGenSchemas(tsText) {
  const m = /    schemas: \{([\s\S]*?)\r?\n    \};/.exec(tsText);
  if (!m) return {};
  const body = m[1];
  const matches = [...body.matchAll(/^        ([A-Za-z0-9_]+): \{/gm)];
  const result = {};
  for (let i = 0; i < matches.length; i += 1) {
    const start = matches[i].index + matches[i][0].length;
    const end = i + 1 < matches.length ? matches[i + 1].index : body.length;
    const block = body.slice(start, end);
    const props = new Set([...block.matchAll(/^            ([A-Za-z0-9_]+)\??:/gm)].map((x) => x[1]));
    result[matches[i][1]] = props;
  }
  return result;
}

export function parseResourcesCalls(transportText) {
  let fnMatches = [...transportText.matchAll(/^export function ([A-Za-z0-9_]+)\(/gm)];
  if (fnMatches.length === 0) {
    fnMatches = [...transportText.matchAll(/^  ([A-Za-z0-9_]+)\(/gm)];
  }
  const apiPattern =
    /apiFetch(?:<[^>]+>)?\(\s*([`"'])([^`"']+)\1(?:\s*,\s*\{([\s\S]*?)\}\s*\))?/g;
  const calls = [];
  for (let i = 0; i < fnMatches.length; i += 1) {
    const fnName = fnMatches[i][1];
    const start = fnMatches[i].index + fnMatches[i][0].length;
    const end = i + 1 < fnMatches.length ? fnMatches[i + 1].index : transportText.length;
    const body = transportText.slice(start, end);
    for (const cm of body.matchAll(apiPattern)) {
      const rawPath = cm[2];
      const opts = cm[3] ?? "";
      const mm = /method:\s*"([A-Z]+)"/.exec(opts);
      const method = mm ? mm[1] : "GET";
      const normPath = rawPath.replace(/\$\{[^}]+\}/g, "{}");
      const formFields = [...body.matchAll(/form\.append\("([^"]+)"/g)].map((fm) => fm[1]);
      calls.push({ fnName, method, rawPath, normPath, formFields });
    }
  }
  return calls;
}

function parseExportedResourceFns(resourcesText) {
  return new Set(
    [...resourcesText.matchAll(/^export (?:const|function) ([A-Za-z0-9_]+)\b/gm)].map((m) => m[1]),
  );
}

function setDiff(a, b) {
  return [...a].filter((x) => !b.has(x)).sort();
}

export function validateFrontendContractDrift({
  spec,
  endpointsManifest,
  schemaGenTsText,
  typesTsText,
  resourcesTsText,
  httpTsText = null,
  generatedSchemaTsText = null,
}) {
  const errors = [];

  if (generatedSchemaTsText !== null) {
    const normGen = generatedSchemaTsText.replace(/\r\n/g, "\n").trim();
    const normCur = schemaGenTsText
      .replace(/\r\n/g, "\n")
      .replace(/^\/\*\*[\s\S]*?\*\/\n+/, "")
      .trim();
    if (normGen !== normCur) {
      errors.push(
        "frontend/src/api/schema.gen.ts is out of sync with openapi.yaml (run `npm run api:gen`)",
      );
    }
  }

  // 1. schema.gen.ts paths & methods
  const genPaths = parseSchemaGenPaths(schemaGenTsText);
  const openapiPaths = {};
  for (const [p, methods] of Object.entries(spec.paths ?? {})) {
    openapiPaths[p] = new Set(Object.keys(methods).filter((m) => HTTP_METHODS.includes(m)));
  }
  const genPathKeys = new Set(Object.keys(genPaths));
  const openPathKeys = new Set(Object.keys(openapiPaths));
  if (setDiff(openPathKeys, genPathKeys).length || setDiff(genPathKeys, openPathKeys).length) {
    errors.push(
      `schema.gen.ts paths drifted: missing=${JSON.stringify(setDiff(openPathKeys, genPathKeys))}, extra=${JSON.stringify(setDiff(genPathKeys, openPathKeys))}`,
    );
  } else {
    for (const p of Object.keys(openapiPaths).sort()) {
      if (
        setDiff(openapiPaths[p], genPaths[p]).length ||
        setDiff(genPaths[p], openapiPaths[p]).length
      ) {
        errors.push(`schema.gen.ts methods drifted for ${p}`);
      }
    }
  }

  // 2. schema.gen.ts schemas & properties
  const genSchemas = parseSchemaGenSchemas(schemaGenTsText);
  const openapiSchemas = spec.components?.schemas ?? {};
  const genSchemaKeys = new Set(Object.keys(genSchemas));
  const openSchemaKeys = new Set(Object.keys(openapiSchemas));
  if (
    setDiff(openSchemaKeys, genSchemaKeys).length ||
    setDiff(genSchemaKeys, openSchemaKeys).length
  ) {
    errors.push(
      `schema.gen.ts schemas drifted: missing=${JSON.stringify(setDiff(openSchemaKeys, genSchemaKeys))}, extra=${JSON.stringify(setDiff(genSchemaKeys, openSchemaKeys))}`,
    );
  } else {
    for (const name of Object.keys(openapiSchemas).sort()) {
      const expectedProps = new Set(Object.keys(openapiSchemas[name].properties ?? {}));
      const actualProps = genSchemas[name];
      if (setDiff(expectedProps, actualProps).length || setDiff(actualProps, expectedProps).length) {
        errors.push(
          `schema.gen.ts properties drifted for ${name}: missing=${JSON.stringify(setDiff(expectedProps, actualProps))}, extra=${JSON.stringify(setDiff(actualProps, expectedProps))}`,
        );
      }
    }
  }

  // 3. types.ts S["..."] references
  for (const m of typesTsText.matchAll(/S\["([^"]+)"\]/g)) {
    if (!(m[1] in openapiSchemas)) {
      errors.push(`frontend/src/data/types.ts references unknown schema ${m[1]}`);
    }
  }

  // 4. endpoints.json vs openapi.yaml
  const manifestByKey = new Map();
  for (const entry of endpointsManifest.endpoints ?? []) {
    const key = `${entry.method.toUpperCase()} ${entry.openApiPath}`;
    if (manifestByKey.has(key)) errors.push(`endpoints.json duplicate entry ${key}`);
    manifestByKey.set(key, entry);
  }
  const openOpKeys = new Set();
  for (const [p, methods] of Object.entries(spec.paths ?? {})) {
    for (const m of Object.keys(methods)) {
      if (HTTP_METHODS.includes(m)) openOpKeys.add(`${m.toUpperCase()} ${p}`);
    }
  }
  const manifestKeySet = new Set(manifestByKey.keys());
  if (setDiff(openOpKeys, manifestKeySet).length || setDiff(manifestKeySet, openOpKeys).length) {
    errors.push(
      `endpoints.json coverage drifted: missing=${JSON.stringify(setDiff(openOpKeys, manifestKeySet))}, extra=${JSON.stringify(setDiff(manifestKeySet, openOpKeys))}`,
    );
  }
  for (const [key, entry] of manifestByKey.entries()) {
    const op = spec.paths?.[entry.openApiPath]?.[entry.method.toLowerCase()];
    if (!op) continue;
    if (entry.clientPath !== entry.openApiPath.replace(/^\/api/, "")) {
      errors.push(`endpoints.json ${key}: clientPath mismatch ${entry.clientPath}`);
    }
    const expectedStatus = Number(["200", "201", "204"].find((code) => code in (op.responses ?? {})));
    if (entry.successStatus !== expectedStatus) {
      errors.push(`endpoints.json ${key}: successStatus ${entry.successStatus} != ${expectedStatus}`);
    }
    const opQuery = (op.parameters ?? []).filter((p) => p.in === "query").map((p) => p.name);
    if (JSON.stringify(entry.queryParams) !== JSON.stringify(opQuery)) {
      errors.push(`endpoints.json ${key}: queryParams drift`);
    }
  }

  // 5. http.ts / resources.ts vs openapi.yaml & endpoints.json
  const transportText = httpTsText ?? resourcesTsText;
  const clientCalls = parseResourcesCalls(transportText);
  const exportedResourceFns = parseExportedResourceFns(resourcesTsText);
  const clientByKey = new Map();
  for (const call of clientCalls) {
    const normOpenApi = `/api${call.normPath}`;
    const matchedPath = Object.keys(spec.paths ?? {}).find(
      (p) => p.replace(/\{[^}]+\}/g, "{}") === normOpenApi,
    );
    if (!matchedPath) {
      errors.push(`http.ts ${call.fnName} calls unknown endpoint ${call.method} ${call.normPath}`);
      continue;
    }
    if (!(call.method.toLowerCase() in spec.paths[matchedPath])) {
      errors.push(`http.ts ${call.fnName} uses invalid method ${call.method} on ${matchedPath}`);
      continue;
    }
    if (matchedPath === "/api/imports" && call.method === "POST") {
      const uploadSchema = openapiSchemas.Body_upload_import_api_imports_post;
      const declaredFields = new Set(Object.keys(uploadSchema?.properties ?? {}));
      for (const field of call.formFields) {
        if (!declaredFields.has(field)) {
          errors.push(`http.ts ${call.fnName} appends undeclared multipart field ${field}`);
        }
      }
      for (const reqField of uploadSchema?.required ?? []) {
        if (!call.formFields.includes(reqField)) {
          errors.push(`http.ts ${call.fnName} misses required multipart field ${reqField}`);
        }
      }
    }
    clientByKey.set(`${call.method} ${matchedPath}`, call.fnName);
  }

  for (const [key, entry] of manifestByKey.entries()) {
    if (entry.opsOnly) continue;
    const actualFn = clientByKey.get(key) ?? null;
    if (actualFn !== entry.resourceFn) {
      errors.push(
        `http.ts drift for ${key}: expected ${JSON.stringify(entry.resourceFn)}, found ${JSON.stringify(actualFn)}`,
      );
    } else if (entry.resourceFn && !exportedResourceFns.has(entry.resourceFn)) {
      errors.push(
        `resources.ts missing exported facade fn ${JSON.stringify(entry.resourceFn)} for ${key}`,
      );
    }
  }

  return errors;
}

export function validateFixtureCorpus(spec, fixtures) {
  const errors = [];
  const schemas = spec.components?.schemas ?? {};
  const validMap = fixtures.valid ?? {};
  const invalidMap = fixtures.invalid ?? {};

  const expectedSchemas = new Set(
    Object.keys(schemas).filter((name) => !name.startsWith("Body_upload_import_")),
  );
  const missing = [...expectedSchemas].filter((name) => !(name in validMap)).sort();
  if (missing.length > 0) {
    errors.push(`schemas.json missing valid fixtures for: ${JSON.stringify(missing)}`);
  }

  for (const [schemaName, cases] of Object.entries(validMap).sort()) {
    if (!(schemaName in schemas)) {
      errors.push(`schemas.json valid references unknown schema ${schemaName}`);
      continue;
    }
    for (const c of cases) {
      errors.push(
        ...validateAgainstSchema(
          c.data,
          { $ref: `#/components/schemas/${schemaName}` },
          spec,
          { allowExtraProperties: false, path: `${schemaName}[${c.name}]` },
        ),
      );
    }
  }

  for (const [schemaName, cases] of Object.entries(invalidMap).sort()) {
    if (!(schemaName in schemas)) {
      errors.push(`schemas.json invalid references unknown schema ${schemaName}`);
      continue;
    }
    for (const c of cases) {
      const errs = validateAgainstSchema(
        c.data,
        { $ref: `#/components/schemas/${schemaName}` },
        spec,
        { allowExtraProperties: false, path: `${schemaName}[${c.name}]` },
      );
      if (errs.length === 0) {
        errors.push(`Invalid fixture ${schemaName}[${c.name}] unexpectedly passed validation`);
      }
    }
  }

  return errors;
}

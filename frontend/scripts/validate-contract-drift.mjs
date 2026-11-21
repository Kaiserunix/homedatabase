#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import openapiTS, { astToString } from "openapi-typescript";
import {
  validateFixtureCorpus,
  validateFrontendContractDrift,
  validateOpenApiInvariants,
} from "./contract-validator.mjs";

const REPO_ROOT = fileURLToPath(new URL("../../", import.meta.url));
const readUtf8 = (rel) => readFileSync(join(REPO_ROOT, rel), "utf8");

const spec = JSON.parse(readUtf8("openapi.yaml"));
const endpointsManifest = JSON.parse(readUtf8("contracts/fixtures/endpoints.json"));
const schemaFixtures = JSON.parse(readUtf8("contracts/fixtures/schemas.json"));
const schemaGenTsText = readUtf8("frontend/src/api/schema.gen.ts");
const typesTsText = readUtf8("frontend/src/data/types.ts");
const resourcesTsText = readUtf8("frontend/src/api/resources.ts");
const httpTsText = readUtf8("frontend/src/api/http.ts");

const generatedSchemaTsText = astToString(await openapiTS(spec));

const errors = [
  ...validateOpenApiInvariants(spec),
  ...validateFrontendContractDrift({
    spec,
    endpointsManifest,
    schemaGenTsText,
    typesTsText,
    resourcesTsText,
    httpTsText,
    generatedSchemaTsText,
  }),
  ...validateFixtureCorpus(spec, schemaFixtures),
];

if (errors.length > 0) {
  console.error(`Frontend contract drift detected (${errors.length} issue(s)):`);
  for (const err of errors) {
    console.error(`  - ${err}`);
  }
  process.exit(1);
}

console.log(
  "Frontend contract drift check passed: openapi.yaml, schema.gen.ts, resources.ts, types.ts, and fixtures are aligned.",
);

export const MAX_SAFE_MINOR: number;
export const HTTP_METHODS: string[];

export interface SchemaValidationOptions {
  allowExtraProperties?: boolean;
  path?: string;
}

export function validateAgainstSchema(
  value: unknown,
  schema: Record<string, unknown>,
  rootSpec: Record<string, unknown>,
  opts?: SchemaValidationOptions,
): string[];

export function validateOpenApiInvariants(spec: Record<string, unknown>): string[];

export function parseSchemaGenPaths(tsText: string): Record<string, Set<string>>;
export function parseSchemaGenSchemas(tsText: string): Record<string, Set<string>>;

export interface ResourceCall {
  fnName: string;
  method: string;
  rawPath: string;
  normPath: string;
  formFields: string[];
}

export function parseResourcesCalls(resourcesText: string): ResourceCall[];

export interface FrontendDriftInput {
  spec: Record<string, unknown>;
  endpointsManifest: Record<string, unknown>;
  schemaGenTsText: string;
  typesTsText: string;
  resourcesTsText: string;
  httpTsText?: string | null;
  generatedSchemaTsText?: string | null;
}

export function validateFrontendContractDrift(input: FrontendDriftInput): string[];
export function validateFixtureCorpus(
  spec: Record<string, unknown>,
  fixtures: Record<string, unknown>,
): string[];

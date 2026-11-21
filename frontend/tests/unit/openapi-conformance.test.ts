import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import openapiTS, { astToString } from "openapi-typescript";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as resources from "@/api/resources";
import {
  accounts,
  backups,
  categories,
  importBatches,
  members,
  passkeys,
  periodLocks,
  reportTiers,
  splits,
  transactions,
} from "@/data/mock";
import {
  validateAgainstSchema,
  validateFixtureCorpus,
  validateFrontendContractDrift,
  validateOpenApiInvariants,
} from "../../scripts/contract-validator.mjs";

const REPO_ROOT = resolve(__dirname, "../../..");
const readJson = (rel: string) => JSON.parse(readFileSync(resolve(REPO_ROOT, rel), "utf8"));
const readUtf8 = (rel: string) => readFileSync(resolve(REPO_ROOT, rel), "utf8");

const spec = readJson("openapi.yaml") as Record<string, any>;
const endpointsManifest = readJson("contracts/fixtures/endpoints.json") as {
  endpoints: Array<{
    method: string;
    openApiPath: string;
    clientPath: string;
    resourceFn: string | null;
    opsOnly: boolean;
    successStatus: number;
    responseContentType: string | null;
    responseSchema: string | null;
    responseIsArray: boolean;
    queryParams: string[];
  }>;
};
const schemaFixtures = readJson("contracts/fixtures/schemas.json") as {
  valid: Record<string, Array<{ name: string; data: unknown }>>;
  invalid: Record<string, Array<{ name: string; data: unknown }>>;
};

function responseSchemaFor(method: string, openApiPath: string, status: number): Record<string, unknown> {
  const op = spec.paths[openApiPath]?.[method.toLowerCase()];
  expect(op, `Missing OpenAPI operation ${method} ${openApiPath}`).toBeTruthy();
  const resp = op.responses?.[String(status)];
  expect(resp, `Missing status ${status} on ${method} ${openApiPath}`).toBeTruthy();
  return resp.content?.["application/json"]?.schema ?? {};
}

function requestSchemaFor(method: string, openApiPath: string): Record<string, unknown> | null {
  const op = spec.paths[openApiPath]?.[method.toLowerCase()];
  expect(op, `Missing OpenAPI operation ${method} ${openApiPath}`).toBeTruthy();
  return op.requestBody?.content?.["application/json"]?.schema ?? null;
}

function assertValidSchema(value: unknown, schema: Record<string, unknown>, label: string): void {
  const errs = validateAgainstSchema(value, schema, spec, {
    allowExtraProperties: false,
    path: label,
  });
  expect(errs, `${label} failed OpenAPI schema validation:\n${errs.join("\n")}`).toEqual([]);
}

describe("OpenAPI schema conformance & cross-stack drift validator", () => {
  it("keeps openapi.yaml, schema.gen.ts, resources.ts, types.ts, and contract fixtures in sync", async () => {
    const generatedSchemaTsText = astToString(await openapiTS(spec as any));
    const errors = [
      ...validateOpenApiInvariants(spec),
      ...validateFrontendContractDrift({
        spec,
        endpointsManifest,
        schemaGenTsText: readUtf8("frontend/src/api/schema.gen.ts"),
        typesTsText: readUtf8("frontend/src/data/types.ts"),
        resourcesTsText: readUtf8("frontend/src/api/resources.ts"),
        httpTsText: readUtf8("frontend/src/api/http.ts"),
        generatedSchemaTsText,
      }),
      ...validateFixtureCorpus(spec, schemaFixtures),
    ];
    expect(errors).toEqual([]);
  });

  it("detects injected schema.gen.ts drift, extra fields, and legacy string confidence", () => {
    const mutatedSchemaGen = readUtf8("frontend/src/api/schema.gen.ts").replace(
      "            isAdmin: boolean;",
      "            isAdminDrifted: boolean;",
    );
    const driftErrors = validateFrontendContractDrift({
      spec,
      endpointsManifest,
      schemaGenTsText: mutatedSchemaGen,
      typesTsText: readUtf8("frontend/src/data/types.ts"),
      resourcesTsText: readUtf8("frontend/src/api/resources.ts"),
      httpTsText: readUtf8("frontend/src/api/http.ts"),
    });
    expect(driftErrors.some((e) => e.includes("MemberOut"))).toBe(true);

    const extraPropErrors = validateAgainstSchema(
      { ...members[0], unexpectedLeak: true },
      { $ref: "#/components/schemas/MemberOut" },
      spec,
      { allowExtraProperties: false, path: "MemberOut" },
    );
    expect(extraPropErrors.some((e) => e.includes("unexpectedLeak"))).toBe(true);

    const legacyConfidenceErrors = validateAgainstSchema(
      { ...importBatches[0].candidates[0], confidence: "high" },
      { $ref: "#/components/schemas/ImportCandidateOut" },
      spec,
    );
    expect(legacyConfidenceErrors.some((e) => e.includes("confidence"))).toBe(true);
  });

  it("validates every fixture in frontend/src/data/mock.ts against OpenAPI schemas", () => {
    assertValidSchema(members, { type: "array", items: { $ref: "#/components/schemas/MemberOut" } }, "mock.members");
    assertValidSchema(accounts, { type: "array", items: { $ref: "#/components/schemas/AccountOut" } }, "mock.accounts");
    assertValidSchema(categories, { type: "array", items: { $ref: "#/components/schemas/CategoryOut" } }, "mock.categories");
    assertValidSchema(transactions, { type: "array", items: { $ref: "#/components/schemas/TransactionOut" } }, "mock.transactions");
    assertValidSchema(importBatches, { type: "array", items: { $ref: "#/components/schemas/ImportBatchOut" } }, "mock.importBatches");
    assertValidSchema(splits, { type: "array", items: { $ref: "#/components/schemas/SplitOut" } }, "mock.splits");
    assertValidSchema(backups, { type: "array", items: { $ref: "#/components/schemas/BackupRecordOut" } }, "mock.backups");
    assertValidSchema(passkeys, { type: "array", items: { $ref: "#/components/schemas/PasskeyOut" } }, "mock.passkeys");
    assertValidSchema(periodLocks, { type: "array", items: { $ref: "#/components/schemas/PeriodLockOut" } }, "mock.periodLocks");
    assertValidSchema(reportTiers, { type: "array", items: { $ref: "#/components/schemas/ReportTierOut" } }, "mock.reportTiers");
  });
});

describe("Mock mode (VITE_USE_MOCKS=true) responses conform to OpenAPI schemas", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "true");
    resources.setMockLatency(0);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("returns OpenAPI-conformant payloads across all resource methods", async () => {
    assertValidSchema(await resources.login("dad", "pw"), { $ref: "#/components/schemas/AuthOut" }, "mock:login");
    await expect(resources.logout()).resolves.toBeUndefined();
    assertValidSchema(await resources.fetchMe(), { $ref: "#/components/schemas/MemberOut" }, "mock:fetchMe");
    assertValidSchema(
      await resources.passkeyAuthenticationOptions("dad"),
      { $ref: "#/components/schemas/WebAuthnCeremonyOut" },
      "mock:passkeyAuthOptions",
    );
    assertValidSchema(
      await resources.passkeyAuthenticationVerify("c-1", { id: "cred" }),
      { $ref: "#/components/schemas/AuthOut" },
      "mock:passkeyAuthVerify",
    );
    assertValidSchema(
      await resources.passkeyRegistrationOptions(),
      { $ref: "#/components/schemas/WebAuthnCeremonyOut" },
      "mock:passkeyRegOptions",
    );
    assertValidSchema(
      await resources.passkeyRegistrationVerify("c-2", "Mac", { id: "cred" }),
      { $ref: "#/components/schemas/PasskeyOut" },
      "mock:passkeyRegVerify",
    );
    assertValidSchema(
      await resources.listPasskeys(),
      { type: "array", items: { $ref: "#/components/schemas/PasskeyOut" } },
      "mock:listPasskeys",
    );
    await expect(resources.deletePasskey("pk-1")).resolves.toBeUndefined();

    assertValidSchema(
      await resources.listAccounts(),
      { type: "array", items: { $ref: "#/components/schemas/AccountOut" } },
      "mock:listAccounts",
    );
    assertValidSchema(
      await resources.createAccount({
        name: "新账户",
        kind: "asset",
        scope: "personal",
        currency: "CNY",
        openingBalanceCents: 1000,
        icon: "wallet",
      }),
      { $ref: "#/components/schemas/AccountOut" },
      "mock:createAccount",
    );
    assertValidSchema(
      await resources.updateAccount("a-cmb", { expectedVersion: 1, name: "改名卡" }),
      { $ref: "#/components/schemas/AccountOut" },
      "mock:updateAccount",
    );
    assertValidSchema(
      await resources.listAccountGrants("a-cmb"),
      { type: "array", items: { $ref: "#/components/schemas/AccountGrantOut" } },
      "mock:listAccountGrants",
    );
    assertValidSchema(
      await resources.setAccountGrant("a-cmb", "m-mom", "view"),
      { $ref: "#/components/schemas/AccountGrantOut" },
      "mock:setAccountGrant",
    );
    await expect(resources.deleteAccountGrant("a-cmb", "m-mom")).resolves.toBeUndefined();

    assertValidSchema(
      await resources.listCategories({ includeInactive: true }),
      { type: "array", items: { $ref: "#/components/schemas/CategoryOut" } },
      "mock:listCategories",
    );
    assertValidSchema(
      await resources.createCategory({ name: "宠物", kind: "expense", parentId: null, icon: "shapes" }),
      { $ref: "#/components/schemas/CategoryOut" },
      "mock:createCategory",
    );
    assertValidSchema(
      await resources.updateCategory("c-food", { expectedVersion: 1, active: false }),
      { $ref: "#/components/schemas/CategoryOut" },
      "mock:updateCategory",
    );

    assertValidSchema(
      await resources.listTransactions(),
      { type: "array", items: { $ref: "#/components/schemas/TransactionOut" } },
      "mock:listTransactions",
    );
    assertValidSchema(
      await resources.getTransaction("t-001"),
      { $ref: "#/components/schemas/TransactionOut" },
      "mock:getTransaction",
    );
    assertValidSchema(
      await resources.createTransaction({
        type: "expense",
        payee: "盒马",
        note: "",
        categoryId: "c-grocery",
        accountId: "a-cmb",
        amountCents: 5000,
        currency: "CNY",
        date: "2026-08-05T12:00:00Z",
      }),
      { $ref: "#/components/schemas/TransactionOut" },
      "mock:createTransaction",
    );
    assertValidSchema(
      await resources.updateTransaction("t-001", { expectedVersion: 1, note: "备注" }),
      { $ref: "#/components/schemas/TransactionOut" },
      "mock:updateTransaction",
    );
    await expect(resources.deleteTransaction("t-001", "作废")).resolves.toBeUndefined();
    assertValidSchema(
      await resources.listRecycledTransactions(),
      { type: "array", items: { $ref: "#/components/schemas/TransactionOut" } },
      "mock:listRecycledTransactions",
    );
    assertValidSchema(
      await resources.restoreTransaction("t-001", 1),
      { $ref: "#/components/schemas/TransactionOut" },
      "mock:restoreTransaction",
    );

    assertValidSchema(
      await resources.uploadImport([new File(["a,b"], "bill.csv", { type: "text/csv" })], "微信", "a-cmb"),
      { $ref: "#/components/schemas/ImportBatchOut" },
      "mock:uploadImport",
    );
    assertValidSchema(
      await resources.getImportStatus("b-003"),
      { $ref: "#/components/schemas/ImportJobOut" },
      "mock:getImportStatus",
    );
    assertValidSchema(
      await resources.listImportBatches(),
      { type: "array", items: { $ref: "#/components/schemas/ImportBatchOut" } },
      "mock:listImportBatches",
    );
    assertValidSchema(
      await resources.getImportBatch("b-003"),
      { $ref: "#/components/schemas/ImportBatchOut" },
      "mock:getImportBatch",
    );
    assertValidSchema(
      await resources.patchImportCandidate("b-003", "cd-7", {
        expectedVersion: 1,
        fxRate: "7.20",
        baseAmountCents: 11513,
      }),
      { $ref: "#/components/schemas/ImportCandidateOut" },
      "mock:patchImportCandidate",
    );
    assertValidSchema(
      await resources.confirmImportCandidates("b-003", ["cd-1"]),
      { $ref: "#/components/schemas/ImportBatchOut" },
      "mock:confirmImportCandidates",
    );
    assertValidSchema(
      await resources.skipDuplicateCandidates("b-003", ["cd-3"]),
      { $ref: "#/components/schemas/ImportBatchOut" },
      "mock:skipDuplicateCandidates",
    );
    assertValidSchema(
      await resources.undoImportBatch("b-004"),
      { $ref: "#/components/schemas/ImportBatchOut" },
      "mock:undoImportBatch",
    );

    assertValidSchema(
      await resources.listSplits(),
      { type: "array", items: { $ref: "#/components/schemas/SplitOut" } },
      "mock:listSplits",
    );
    const freshTxn = { ...transactions[0], id: "t-mock-split-temp", type: "expense" as const, splitId: undefined };
    transactions.push(freshTxn);
    try {
      assertValidSchema(
        await resources.createSplit({
          txnId: freshTxn.id,
          totalCents: freshTxn.amountCents,
          shares: [
            { memberId: "m-dad", shareCents: freshTxn.amountCents - 100 },
            { memberId: "m-mom", shareCents: 100 },
          ],
        }),
        { $ref: "#/components/schemas/SplitOut" },
        "mock:createSplit",
      );
    } finally {
      transactions.pop();
    }
    assertValidSchema(
      await resources.respondToSplit("s-002", { expectedVersion: 2, accept: true }),
      { $ref: "#/components/schemas/SplitOut" },
      "mock:respondToSplit",
    );
    assertValidSchema(
      await resources.updateSplitShare("s-001", "m-mom", { expectedVersion: 1, shareCents: 20000 }),
      { $ref: "#/components/schemas/SplitOut" },
      "mock:updateSplitShare",
    );

    assertValidSchema(
      await resources.listMembers(),
      { type: "array", items: { $ref: "#/components/schemas/MemberOut" } },
      "mock:listMembers",
    );
    assertValidSchema(
      await resources.listBackups(),
      { type: "array", items: { $ref: "#/components/schemas/BackupRecordOut" } },
      "mock:listBackups",
    );
    assertValidSchema(
      await resources.createBackup(),
      { $ref: "#/components/schemas/BackupRecordOut" },
      "mock:createBackup",
    );
    assertValidSchema(
      await resources.verifyBackup("bk-0731"),
      { $ref: "#/components/schemas/BackupRecordOut" },
      "mock:verifyBackup",
    );
    expect(Object.prototype.toString.call(await resources.downloadBackup("bk-0731"))).toBe("[object Blob]");
    assertValidSchema(
      await resources.listPeriodLocks(),
      { type: "array", items: { $ref: "#/components/schemas/PeriodLockOut" } },
      "mock:listPeriodLocks",
    );
    assertValidSchema(
      await resources.createPeriodLock({ startsOn: "2026-08-01", endsOn: "2026-08-31", reason: "核对" }),
      { $ref: "#/components/schemas/PeriodLockOut" },
      "mock:createPeriodLock",
    );
    await expect(resources.deletePeriodLock("pl-2026-06", "重新核对")).resolves.toBeUndefined();
    assertValidSchema(
      await resources.listReportTiers(),
      { type: "array", items: { $ref: "#/components/schemas/ReportTierOut" } },
      "mock:listReportTiers",
    );
    assertValidSchema(
      await resources.getCfoCapabilities(),
      { $ref: "#/components/schemas/CfoCapabilitiesOut" },
      "mock:getCfoCapabilities",
    );
    assertValidSchema(
      await resources.askCfo({ question: "这个月花了多少", asOfDate: "2026-08-31" }),
      { $ref: "#/components/schemas/CfoAskOut" },
      "mock:askCfo",
    );
    assertValidSchema(
      await resources.getCashflowRadar({ asOfDate: "2026-08-31", horizonDays: 90 }),
      { $ref: "#/components/schemas/CashflowRadarOut" },
      "mock:getCashflowRadar",
    );
    expect(
      Object.prototype.toString.call(
        await resources.exportReport(5, { from: "2026-08-01", to: "2026-08-31" }),
      ),
    ).toBe("[object Blob]");
  });
});

describe("HTTP client request payloads & endpoints conform 100% to OpenAPI", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("exercises all 47 client functions and validates outgoing requests against openapi.yaml", async () => {
    const exercised = new Set<string>();

    const fixtureFor = (schemaName: string | null, isArray: boolean): unknown => {
      if (!schemaName) return undefined;
      const sample = schemaFixtures.valid[schemaName]?.[0]?.data;
      expect(sample, `Missing valid fixture for ${schemaName}`).toBeDefined();
      return isArray ? [sample] : sample;
    };

    const fetchMock = vi.fn().mockImplementation((input: unknown, init: RequestInit = {}) => {
      const fullUrl = String(input);
      const [rawPath, rawQuery = ""] = fullUrl.split("?");
      const method = (init.method ?? "GET").toUpperCase();
      expect(init.credentials).toBe("include");
      const headers = (init.headers ?? {}) as Record<string, string>;
      expect(headers).not.toHaveProperty("Authorization");

      const matched = endpointsManifest.endpoints.find((ep) => {
        if (ep.method !== method) return false;
        const regex = new RegExp(`^${ep.openApiPath.replace(/\{[^}]+\}/g, "[^/]+")}$`);
        return regex.test(rawPath);
      });
      expect(matched, `Unmapped HTTP call: ${method} ${fullUrl}`).toBeTruthy();
      exercised.add(`${method} ${matched!.openApiPath}`);

      if (rawQuery) {
        const searchParams = new URLSearchParams(rawQuery);
        for (const key of searchParams.keys()) {
          expect(
            matched!.queryParams.includes(key),
            `Unexpected query param ${key} on ${method} ${matched!.openApiPath}`,
          ).toBe(true);
        }
      }

      if (init.body instanceof FormData) {
        expect(headers["Content-Type"]).toBeUndefined();
        const uploadProps = Object.keys(spec.components.schemas.Body_upload_import_api_imports_post.properties);
        for (const key of init.body.keys()) {
          expect(uploadProps.includes(key), `Undeclared FormData key ${key}`).toBe(true);
        }
      } else if (init.body !== undefined) {
        expect(headers["Content-Type"]).toBe("application/json");
        const reqSchema = requestSchemaFor(method, matched!.openApiPath);
        expect(reqSchema, `Unexpected JSON body on ${method} ${matched!.openApiPath}`).toBeTruthy();
        assertValidSchema(JSON.parse(String(init.body)), reqSchema!, `req:${method} ${matched!.openApiPath}`);
      } else {
        expect(requestSchemaFor(method, matched!.openApiPath)).toBeNull();
      }

      if (matched!.successStatus === 204) {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      if (matched!.responseContentType && matched!.responseContentType !== "application/json") {
        return Promise.resolve(
          new Response(new Uint8Array([0x50, 0x4b, 0x03, 0x04]), {
            status: matched!.successStatus,
            headers: { "Content-Type": matched!.responseContentType },
          }),
        );
      }
      const payload = fixtureFor(matched!.responseSchema, matched!.responseIsArray);
      assertValidSchema(
        payload,
        responseSchemaFor(method, matched!.openApiPath, matched!.successStatus),
        `res:${method} ${matched!.openApiPath}`,
      );
      return Promise.resolve(
        new Response(JSON.stringify(payload), {
          status: matched!.successStatus,
          headers: { "Content-Type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    await resources.login("son", "password-123");
    await resources.logout();
    await resources.fetchMe();
    await resources.passkeyAuthenticationOptions("son");
    await resources.passkeyAuthenticationVerify("cer-1", { id: "cred-1" });
    await resources.passkeyRegistrationOptions();
    await resources.passkeyRegistrationVerify("cer-2", "MacBook", { id: "cred-2" });
    await resources.listPasskeys();
    await resources.deletePasskey("pk-1");

    await resources.listAccounts();
    await resources.createAccount({
      name: "招行卡",
      kind: "asset",
      scope: "personal",
      currency: "CNY",
      openingBalanceCents: 0,
      icon: "landmark",
    });
    await resources.updateAccount("a-cmb", { expectedVersion: 1, name: "招行工资卡" });
    await resources.listAccountGrants("a-cmb");
    await resources.setAccountGrant("a-cmb", "m-mom", "view");
    await resources.deleteAccountGrant("a-cmb", "m-mom");

    await resources.listCategories({ includeInactive: true });
    await resources.createCategory({ name: "宠物", kind: "expense", parentId: null, icon: "shapes" });
    await resources.updateCategory("c-food", { expectedVersion: 1, active: false });

    await resources.listTransactions({ type: "expense", accountId: "a-cmb", month: "2026-08", keyword: "超市" });
    await resources.getTransaction("t-001");
    await resources.createTransaction({
      type: "expense",
      payee: "盒马",
      note: "",
      categoryId: "c-grocery",
      accountId: "a-cmb",
      amountCents: 1200,
      currency: "CNY",
      date: "2026-08-05T12:00:00Z",
    });
    await resources.updateTransaction("t-001", { expectedVersion: 1, payee: "盒马鲜生" });
    await resources.deleteTransaction("t-001", "录入错误");
    await resources.listRecycledTransactions();
    await resources.restoreTransaction("t-001", 2);

    await resources.uploadImport([new File(["a,b"], "bill.csv", { type: "text/csv" })], "微信", "a-cmb");
    await resources.getImportStatus("b-003");
    await resources.listImportBatches();
    await resources.getImportBatch("b-003");
    await resources.patchImportCandidate("b-003", "cd-7", {
      expectedVersion: 1,
      fxRate: "7.20",
      baseAmountCents: 11513,
    });
    await resources.confirmImportCandidates("b-003", ["cd-1"]);
    await resources.skipDuplicateCandidates("b-003", ["cd-3"]);
    await resources.undoImportBatch("b-003");

    await resources.listSplits();
    await resources.createSplit({
      txnId: "t-003",
      totalCents: 46800,
      shares: [
        { memberId: "m-dad", shareCents: 23400 },
        { memberId: "m-mom", shareCents: 23400 },
      ],
    });
    await resources.respondToSplit("s-001", { expectedVersion: 1, accept: true });
    await resources.updateSplitShare("s-001", "m-mom", { expectedVersion: 1, shareCents: 20000 });

    await resources.listMembers();
    await resources.listBackups();
    await resources.createBackup();
    await resources.verifyBackup("bk-0731");
    await resources.downloadBackup("bk-0731");
    await resources.listPeriodLocks();
    await resources.createPeriodLock({ startsOn: "2026-08-01", endsOn: "2026-08-31", reason: "月度核对" });
    await resources.deletePeriodLock("pl-1", "取消锁定");
    await resources.listReportTiers();
    await resources.exportReport(5, { from: "2026-08-01", to: "2026-08-31" });
    await resources.getCfoCapabilities();
    await resources.askCfo({ question: "这个月花了多少", asOfDate: "2026-08-31" });
    await resources.getCashflowRadar({ asOfDate: "2026-08-31", horizonDays: 90 });

    const expectedClientOps = endpointsManifest.endpoints
      .filter((ep) => !ep.opsOnly)
      .map((ep) => `${ep.method} ${ep.openApiPath}`)
      .sort();
    expect([...exercised].sort()).toEqual(expectedClientOps);
  });
});

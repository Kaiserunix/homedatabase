import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import type { ImportBatch, ImportCandidate, SourceFile } from "@/data/types";
import {
  canUndoBatch,
  classifyCandidateConfidence,
  createCandidateEditDraft,
  extractConflictIds,
  filterBatchCandidates,
  formatImportSource,
  inferCandidateCategoryHint,
  isoToShanghaiDateTimeInput,
  needsCandidateAttention,
  selectBatchReviewSummary,
  shanghaiDateTimeInputToIso,
  summarizeSourceFiles,
  validateCandidateEdit,
} from "@/domain/imports";

const makeCandidate = (overrides: Partial<ImportCandidate> = {}): ImportCandidate => ({
  id: "cd-1",
  date: "2026-07-31T16:30:45Z", // 上海 2026-08-01 00:30:45
  payee: "盒马鲜生",
  direction: "expense",
  amountCents: 35_80,
  currency: "CNY",
  suggestedCategoryId: "c-grocery",
  originalCategory: "商超购物",
  targetAccountId: "a-alipay",
  duplicateOfTxnId: null,
  status: "pending",
  validationStatus: "valid",
  confidence: "high",
  version: 2,
  ...overrides,
});

const makeFile = (overrides: Partial<SourceFile> = {}): SourceFile => ({
  id: "f-1",
  filename: "wechat.csv",
  mimeType: "text/csv",
  sizeBytes: 4096,
  sha256: "sha-1",
  encrypted: true,
  expiresAt: null,
  duplicateOfFileId: null,
  parseStatus: "parsed",
  parseMessage: "",
  recordCount: 10,
  candidateCount: 8,
  rejectedCount: 2,
  ...overrides,
});

const makeBatch = (candidates: ImportCandidate[], status: ImportBatch["status"] = "reviewing"): ImportBatch => ({
  id: "b-1",
  filename: "wechat.csv",
  source: "wechat",
  importedAt: "2026-08-02T01:00:00Z",
  status,
  ownerId: "m-dad",
  targetAccountId: "a-alipay",
  jobId: "job-1",
  progress: 100,
  message: "",
  fileCount: 1,
  filesProcessed: 1,
  candidateCount: candidates.length,
  needsOcrCount: 0,
  failedFileCount: 0,
  version: 1,
  files: [makeFile()],
  candidates,
});

describe("domain/imports 纯函数与选择器", () => {
  it("formatImportSource：归一化英文短标识与历史中文名", () => {
    expect(formatImportSource("wechat")).toBe("微信支付");
    expect(formatImportSource("ALIPAY")).toBe("支付宝");
    expect(formatImportSource("cmb")).toBe("招商银行");
    expect(formatImportSource("csv")).toBe("手动CSV");
    expect(formatImportSource("招商银行信用卡")).toBe("招商银行信用卡");
  });

  it("classifyCandidateConfidence：兼容 OCR 0~1 十进制与语义枚举", () => {
    expect(classifyCandidateConfidence(null)).toBeNull();
    expect(classifyCandidateConfidence("")).toBeNull();
    expect(classifyCandidateConfidence("low")).toEqual({
      level: "low",
      label: "低置信度",
      tone: "warn",
    });
    expect(classifyCandidateConfidence("medium")).toEqual({
      level: "medium",
      label: "中置信度",
      tone: "neutral",
    });
    expect(classifyCandidateConfidence("high")).toEqual({
      level: "high",
      label: "高置信度",
      tone: "ok",
    });
    expect(classifyCandidateConfidence("0.54")).toEqual({
      level: "low",
      label: "低置信度 54%",
      tone: "warn",
    });
    expect(classifyCandidateConfidence("0.82")).toEqual({
      level: "medium",
      label: "中置信度 82%",
      tone: "neutral",
    });
    expect(classifyCandidateConfidence("0.96")).toEqual({
      level: "high",
      label: "高置信度 96%",
      tone: "ok",
    });
  });

  it("上海挂钟 datetime-local 双向转换：跨日边界精确且拒绝非法日历日", () => {
    // UTC 7/31 16:30 → 上海 8/1 00:30
    expect(isoToShanghaiDateTimeInput("2026-07-31T16:30:00Z")).toBe("2026-08-01T00:30");
    expect(shanghaiDateTimeInputToIso("2026-08-01T00:30")).toBe("2026-07-31T16:30:00.000Z");

    // 非法日期（2 月 30 日）不得静默进位到 3 月
    expect(shanghaiDateTimeInputToIso("2026-02-30T12:00")).toBeNull();
    expect(shanghaiDateTimeInputToIso("not-a-date")).toBeNull();
  });

  it("validateCandidateEdit：仅生成实际变更字段（未改时间时不抹掉原秒数），精确校验金额", () => {
    const orig = makeCandidate(); // date 含 :45 秒
    const unchanged = createCandidateEditDraft(orig);
    expect(validateCandidateEdit(unchanged, orig)).toEqual({ ok: true, patch: {} });

    // 只改商户与金额：patch 不含 date，保留原秒级时间戳
    const edited = validateCandidateEdit(
      { ...unchanged, payee: "  永辉超市  ", amount: "128.05", targetAccountId: "a-cmb" },
      orig,
    );
    expect(edited).toEqual({
      ok: true,
      patch: {
        payee: "永辉超市",
        amountCents: 128_05,
        targetAccountId: "a-cmb",
      },
    });

    // 修改上海时间：patch 含转换后的 UTC ISO
    const dateEdited = validateCandidateEdit(
      { ...unchanged, date: "2026-08-02T09:15" },
      orig,
    );
    expect(dateEdited).toEqual({
      ok: true,
      patch: { date: "2026-08-02T01:15:00.000Z" },
    });

    // 非法输入拦截
    expect(validateCandidateEdit({ ...unchanged, payee: "   " }, orig).ok).toBe(false);
    expect(validateCandidateEdit({ ...unchanged, amount: "0" }, orig).ok).toBe(false);
    expect(validateCandidateEdit({ ...unchanged, amount: "12.345" }, orig).ok).toBe(false);
    expect(validateCandidateEdit({ ...unchanged, date: "2026-02-31T08:00" }, orig).ok).toBe(false);
  });

  it("selectBatchReviewSummary & filterBatchCandidates：汇总勾选、缺账户、低置信度与筛选集", () => {
    const cValid = makeCandidate({ id: "c-1" });
    const cNoAcct = makeCandidate({ id: "c-2", targetAccountId: null });
    const cLowConf = makeCandidate({ id: "c-3", confidence: "0.45" });
    const cDup = makeCandidate({ id: "c-4", validationStatus: "exact_duplicate", duplicateOfTxnId: "t-1" });
    const cFx = makeCandidate({ id: "c-5", currency: "USD", validationStatus: "needs_fx_review" });
    const cDone = makeCandidate({ id: "c-6", status: "confirmed" });

    const batch = makeBatch([cValid, cNoAcct, cLowConf, cDup, cFx, cDone]);
    const summary = selectBatchReviewSummary(batch, {});

    expect(summary.pending).toHaveLength(5);
    // confirmable: c-1, c-2, c-3（重复与待补汇率不可确认）
    expect(summary.confirmable.map((c) => c.id)).toEqual(["c-1", "c-2", "c-3"]);
    expect(summary.selected.map((c) => c.id)).toEqual(["c-1", "c-2", "c-3"]);
    expect(summary.missingAccountCount).toBe(1);
    expect(summary.hasPendingDup).toBe(true);
    expect(summary.dupSkippedCount).toBe(1);
    expect(summary.lowConfidenceCount).toBe(1);
    expect(summary.headerChecked).toBe(true);

    // 需关注：缺账户(c-2)、低置信度(c-3)、重复(c-4)、待补汇率(c-5)
    expect(needsCandidateAttention(cValid)).toBe(false);
    expect(filterBatchCandidates(batch.candidates, "attention").map((c) => c.id)).toEqual([
      "c-2",
      "c-3",
      "c-4",
      "c-5",
    ]);
    expect(filterBatchCandidates(batch.candidates, "processed").map((c) => c.id)).toEqual(["c-6"]);
  });

  it("summarizeSourceFiles & canUndoBatch & extractConflictIds", () => {
    const files = [
      makeFile({ id: "f-1", parseStatus: "parsed", rejectedCount: 2 }),
      makeFile({ id: "f-2", parseStatus: "needs_ocr", rejectedCount: 0 }),
    ];
    expect(summarizeSourceFiles(files)).toEqual({
      total: 2,
      parsedCount: 1,
      needsOcrCount: 1,
      duplicateCount: 0,
      issueCount: 2,
      totalRejectedRows: 2,
    });

    expect(canUndoBatch(makeBatch([makeCandidate({ status: "confirmed" })], "posted"))).toBe(true);
    expect(canUndoBatch(makeBatch([makeCandidate({ status: "pending" })], "needs_ocr"))).toBe(false);

    const err = new ApiError("阻断", 409, {
      details: { message: "阻断", transactionIds: ["t-1", "t-2"], candidateIds: ["c-9"] },
    });
    expect(extractConflictIds(err)).toEqual({
      transactionIds: ["t-1", "t-2"],
      candidateIds: ["c-9"],
    });
  });

  it("inferCandidateCategoryHint：优先匹配历史同名商户，次选匹配微信/支付宝分类别名", () => {
    const cats = [
      { id: "c-food", name: "餐饮", kind: "expense" as const, active: true },
      { id: "c-grocery", name: "买菜超市", kind: "expense" as const, active: true },
    ];
    const txns = [
      { payee: "老乡鸡", type: "expense", categoryId: "c-food", date: "2026-07-20T04:00:00Z" },
    ];

    // 1. 已有建议分类时不重复提示
    expect(inferCandidateCategoryHint(makeCandidate(), cats, txns)).toBeNull();

    // 2. 命中历史同名商户
    expect(
      inferCandidateCategoryHint(
        makeCandidate({ payee: "老乡鸡", suggestedCategoryId: null, originalCategory: "其他" }),
        cats,
        txns,
      ),
    ).toEqual({
      categoryId: "c-food",
      categoryName: "餐饮",
      reason: "历史同商户",
    });

    // 3. 无历史同名商户时回退匹配原始账单分类别名（商超购物 → 买菜超市）
    expect(
      inferCandidateCategoryHint(
        makeCandidate({ payee: "新开便利店", suggestedCategoryId: null, originalCategory: "商超购物" }),
        cats,
        txns,
      ),
    ).toEqual({
      categoryId: "c-grocery",
      categoryName: "买菜超市",
      reason: "原分类「商超购物」",
    });
  });
});

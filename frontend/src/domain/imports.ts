/**
 * 账单导入与候选复核的纯领域模型、选择器与校验器（零 React / DOM 依赖）。
 *
 * 严格遵循后端口径：
 * - 仅 status=pending 且 validationStatus=valid 的候选可勾选确认；
 * - 日期编辑一律按 Asia/Shanghai 挂钟解释并归一化为 UTC ISO，禁止依赖浏览器本地时区；
 * - 金额编辑一律走精确十进制分解析（BigInt），绝不经 parseFloat × 100；
 * - OCR/解析器置信度兼容后端十进制字符串（"0.0"~"1.0"）与语义枚举（"low"/"medium"/"high"）。
 */
import { ApiError } from "@/api/client";
import type {
  BatchStatus, CandidateStatus, ImportBatch, ImportCandidate, ImportCandidatePatch,
  SourceFile, SourceFileParseStatus,
} from "@/data/types";
import { formatCents, parsePositiveCents } from "@/lib/decimal";
import { isConfirmable, isDuplicate, needsFxReview } from "@/lib/fx-review";
import { isoToShanghaiDateTimeInput, shanghaiDateTimeInputToIso } from "@/lib/ledger-date";

export { isoToShanghaiDateTimeInput, shanghaiDateTimeInputToIso };

export type BadgeTone = "neutral" | "accent" | "warn" | "ok" | "danger";

/** 桌面管理端批次状态展示元数据（needs_ocr 恒为警告态，绝非成功） */
export const ADMIN_BATCH_STATUS_META: Record<BatchStatus, { tone: BadgeTone; label: string }> = {
  queued: { tone: "neutral", label: "排队中" },
  processing: { tone: "accent", label: "解析中" },
  reviewing: { tone: "warn", label: "待确认" },
  needs_ocr: { tone: "warn", label: "需要人工识别" },
  rejected: { tone: "danger", label: "已拒绝" },
  failed: { tone: "danger", label: "解析失败" },
  posted: { tone: "ok", label: "已入账" },
  undone: { tone: "neutral", label: "已撤销" },
};

/** 手机端批次状态展示元数据 */
export const MOBILE_BATCH_STATUS_META: Record<BatchStatus, { tone: BadgeTone; label: string }> = {
  queued: { tone: "neutral", label: "排队中" },
  processing: { tone: "accent", label: "解析中" },
  reviewing: { tone: "accent", label: "待确认" },
  needs_ocr: { tone: "warn", label: "需要 OCR/人工识别" },
  rejected: { tone: "danger", label: "无法解析" },
  failed: { tone: "danger", label: "导入失败" },
  posted: { tone: "ok", label: "已入账" },
  undone: { tone: "neutral", label: "已撤销" },
};

export const CANDIDATE_STATUS_META: Record<CandidateStatus, { tone: "neutral" | "ok"; label: string }> = {
  pending: { tone: "neutral", label: "待确认" },
  confirmed: { tone: "ok", label: "已入账" },
  skipped: { tone: "neutral", label: "已跳过" },
};

export const SOURCE_FILE_STATUS_META: Record<SourceFileParseStatus, { tone: BadgeTone; label: string }> = {
  processing: { tone: "accent", label: "解析中" },
  parsed: { tone: "ok", label: "已解析" },
  duplicate: { tone: "warn", label: "重复文件" },
  needs_ocr: { tone: "warn", label: "待 OCR 识别" },
  rejected: { tone: "danger", label: "已拒绝" },
  failed: { tone: "danger", label: "解析失败" },
};

/** 导入任务轮询终态 */
export const TERMINAL_BATCH_STATUSES: readonly BatchStatus[] = [
  "reviewing",
  "needs_ocr",
  "rejected",
  "failed",
  "posted",
  "undone",
];

const SOURCE_LABELS: Record<string, string> = {
  wechat: "微信支付",
  alipay: "支付宝",
  cmb: "招商银行",
  csv: "手动CSV",
  unknown: "未标注来源",
};

/** 统一归一化历史中文来源名与短标识（如 wechat → 微信支付） */
export function formatImportSource(source: string): string {
  const trimmed = source.trim();
  return SOURCE_LABELS[trimmed.toLowerCase()] ?? (trimmed || "未标注来源");
}

/**
 * 撤销入口判定：仅 posted/needs_ocr 批次且实际含 confirmed 候选时允许整批撤销
 *（纯 needs_ocr 无已入账账目时后端会 409，前端不渲染误导按钮）。
 */
export function canUndoBatch(batch: ImportBatch): boolean {
  return (
    (batch.status === "posted" || batch.status === "needs_ocr") &&
    batch.candidates.some((c) => c.status === "confirmed")
  );
}

export interface ConfidenceMeta {
  level: "high" | "medium" | "low";
  label: string;
  tone: BadgeTone;
}

/**
 * 归一化候选置信度：
 * - 后端 OCR 契约输出 0~1 十进制字符串（如 "0.62"、"0.95"）；
 * - 启发式解析器 / mock 输出 "low" | "medium" | "high"；
 * - null 或空串返回 null。
 */
export function classifyCandidateConfidence(confidence: string | null | undefined): ConfidenceMeta | null {
  if (confidence == null) return null;
  const raw = confidence.trim();
  if (!raw) return null;
  const lower = raw.toLowerCase();
  if (lower === "high") return { level: "high", label: "高置信度", tone: "ok" };
  if (lower === "medium") return { level: "medium", label: "中置信度", tone: "neutral" };
  if (lower === "low") return { level: "low", label: "低置信度", tone: "warn" };

  if (/^\d+(?:\.\d+)?$/.test(raw)) {
    const val = Number(raw);
    if (Number.isFinite(val) && val >= 0 && val <= 1) {
      const pct = Math.round(val * 100);
      if (val < 0.7) return { level: "low", label: `低置信度 ${pct}%`, tone: "warn" };
      if (val < 0.9) return { level: "medium", label: `中置信度 ${pct}%`, tone: "neutral" };
      return { level: "high", label: `高置信度 ${pct}%`, tone: "ok" };
    }
  }
  return { level: "medium", label: `置信度 ${raw}`, tone: "neutral" };
}

/** 候选是否需要用户重点关注（疑似重复、待补汇率、缺账户、低置信度或非 valid 校验态） */
export function needsCandidateAttention(c: ImportCandidate): boolean {
  if (c.status !== "pending") return false;
  const conf = classifyCandidateConfidence(c.confidence);
  return (
    isDuplicate(c) ||
    needsFxReview(c) ||
    !c.targetAccountId ||
    !isConfirmable(c) ||
    conf?.level === "low"
  );
}

export type CandidateFilter = "all" | "pending" | "attention" | "processed";

export function filterBatchCandidates(
  candidates: readonly ImportCandidate[],
  filter: CandidateFilter,
): ImportCandidate[] {
  switch (filter) {
    case "pending":
      return candidates.filter((c) => c.status === "pending");
    case "attention":
      return candidates.filter(needsCandidateAttention);
    case "processed":
      return candidates.filter((c) => c.status !== "pending");
    case "all":
    default:
      return [...candidates];
  }
}

/** 候选当前勾选态：仅 confirmable 候选可被勾选；疑似重复默认不勾选 */
export function isCandidateChecked(
  c: ImportCandidate,
  checks: Readonly<Record<string, boolean>>,
): boolean {
  return isConfirmable(c) ? (checks[c.id] ?? !isDuplicate(c)) : false;
}

export interface BatchReviewSummary {
  pending: ImportCandidate[];
  confirmable: ImportCandidate[];
  selected: ImportCandidate[];
  missingAccountCount: number;
  hasPendingDup: boolean;
  pendingDupIds: string[];
  dupSkippedCount: number;
  attentionCount: number;
  lowConfidenceCount: number;
  headerChecked: boolean | "indeterminate";
}

/** 聚合单批次候选审阅状态（桌面详情页与手机导入页共用单一选择器） */
export function selectBatchReviewSummary(
  batch: ImportBatch,
  checks: Readonly<Record<string, boolean>>,
): BatchReviewSummary {
  const pending = batch.candidates.filter((c) => c.status === "pending");
  const confirmable = pending.filter(isConfirmable);
  const selected = confirmable.filter((c) => isCandidateChecked(c, checks));
  const missingAccountCount = selected.filter((c) => !c.targetAccountId).length;
  const pendingDups = pending.filter(isDuplicate);
  const dupSkippedCount = batch.candidates.filter(
    (c) => isDuplicate(c) && (c.status === "skipped" || !isCandidateChecked(c, checks)),
  ).length;
  const attentionCount = batch.candidates.filter(needsCandidateAttention).length;
  const lowConfidenceCount = pending.filter(
    (c) => classifyCandidateConfidence(c.confidence)?.level === "low",
  ).length;

  const headerChecked: boolean | "indeterminate" =
    confirmable.length === 0
      ? false
      : confirmable.every((c) => isCandidateChecked(c, checks))
        ? true
        : confirmable.some((c) => isCandidateChecked(c, checks))
          ? "indeterminate"
          : false;

  return {
    pending,
    confirmable,
    selected,
    missingAccountCount,
    hasPendingDup: pendingDups.length > 0,
    pendingDupIds: pendingDups.map((c) => c.id),
    dupSkippedCount,
    attentionCount,
    lowConfidenceCount,
    headerChecked,
  };
}

const ORIGINAL_CATEGORY_ALIASES: Record<string, string[]> = {
  餐饮: ["餐饮美食", "美食", "外卖", "咖啡茶饮"],
  买菜超市: ["商超购物", "日用百货", "超市便利", "生鲜蔬果"],
  交通: ["交通出行", "公共交通", "出行"],
  打车: ["网约车", "出租车"],
  居家: ["居家物业", "住房物业", "生活缴费", "家居家装"],
  医疗: ["医疗健康", "医药卫生"],
  娱乐: ["休闲娱乐", "文化娱乐", "数码电器"],
  孩子: ["母婴亲子", "教育培训"],
  工资: ["工资薪金", "薪资"],
  理财收益: ["投资理财", "红包转账"],
};

/**
 * 为尚未分配分类（suggestedCategoryId === null）的待确认候选计算可解释的分类建议：
 * 1) 优先匹配账本中同名历史交易（同收支方向）的启用分类（商户记忆）；
 * 2) 次选匹配微信/支付宝常见原始分类别名。
 * 仅返回建议与来源说明，绝不静默篡改数据，需用户显式点击套用。
 */
export function inferCandidateCategoryHint(
  candidate: ImportCandidate,
  categories: readonly { id: string; name: string; kind: "expense" | "income"; active: boolean }[],
  transactions: readonly { payee: string; type: string; categoryId: string | null; date: string }[],
): { categoryId: string; categoryName: string; reason: string } | null {
  if (candidate.status !== "pending" || candidate.suggestedCategoryId !== null) return null;
  const active = categories.filter((c) => c.active && c.kind === candidate.direction);
  if (active.length === 0) return null;
  const activeById = new Map(active.map((c) => [c.id, c]));

  const normalizedPayee = candidate.payee.trim().toLowerCase();
  if (normalizedPayee) {
    const sorted = [...transactions].sort((a, b) => b.date.localeCompare(a.date));
    for (const t of sorted) {
      if (
        t.type === candidate.direction &&
        t.categoryId &&
        t.payee.trim().toLowerCase() === normalizedPayee
      ) {
        const matched = activeById.get(t.categoryId);
        if (matched) {
          return {
            categoryId: matched.id,
            categoryName: matched.name,
            reason: "历史同商户",
          };
        }
      }
    }
  }

  const orig = candidate.originalCategory?.trim();
  if (orig) {
    for (const cat of active) {
      const aliases = ORIGINAL_CATEGORY_ALIASES[cat.name] ?? [];
      if (cat.name === orig || aliases.some((a) => orig.includes(a))) {
        return {
          categoryId: cat.id,
          categoryName: cat.name,
          reason: `原分类「${orig}」`,
        };
      }
    }
  }

  return null;
}

export interface CandidateEditDraft {
  date: string;
  payee: string;
  amount: string;
  targetAccountId: string;
}

export function createCandidateEditDraft(candidate: ImportCandidate): CandidateEditDraft {
  return {
    date: isoToShanghaiDateTimeInput(candidate.date),
    payee: candidate.payee,
    amount: formatCents(candidate.amountCents),
    targetAccountId: candidate.targetAccountId ?? "",
  };
}

export type CandidateEditValidation =
  | { ok: true; patch: Omit<ImportCandidatePatch, "expectedVersion"> }
  | { ok: false; error: string };

/** 校验候选行内编辑表单并构造 PATCH 负载（不含 expectedVersion） */
export function validateCandidateEdit(
  draft: CandidateEditDraft,
  original: ImportCandidate,
): CandidateEditValidation {
  const payee = draft.payee.trim();
  if (!payee) {
    return { ok: false, error: "请填写商户或交易对方名称" };
  }
  if (payee.length > 300) {
    return { ok: false, error: "商户名称不能超过 300 个字符" };
  }

  const isoDate = shanghaiDateTimeInputToIso(draft.date);
  if (!isoDate) {
    return { ok: false, error: "请输入有效的交易日期与时间（上海时间）" };
  }

  const parsedAmount = parsePositiveCents(draft.amount);
  if (!parsedAmount.ok) {
    return {
      ok: false,
      error: parsedAmount.error.includes("大于 0")
        ? "请输入大于 0 的有效金额（最多两位小数）"
        : parsedAmount.error,
    };
  }

  const patch: Omit<ImportCandidatePatch, "expectedVersion"> = {};
  if (draft.date.trim() !== isoToShanghaiDateTimeInput(original.date)) {
    patch.date = isoDate;
  }
  if (payee !== original.payee) {
    patch.payee = payee;
  }
  if (parsedAmount.cents !== original.amountCents) {
    patch.amountCents = parsedAmount.cents;
  }
  const nextAccount = draft.targetAccountId.trim();
  if (nextAccount && nextAccount !== (original.targetAccountId ?? "")) {
    patch.targetAccountId = nextAccount;
  }

  return { ok: true, patch };
}

/** 汇总源文件诊断：是否存在需要提示给用户的异常/重复/待 OCR 文件 */
export function summarizeSourceFiles(files: readonly SourceFile[]) {
  return {
    total: files.length,
    parsedCount: files.filter((f) => f.parseStatus === "parsed").length,
    needsOcrCount: files.filter((f) => f.parseStatus === "needs_ocr").length,
    duplicateCount: files.filter((f) => f.parseStatus === "duplicate").length,
    issueCount: files.filter(
      (f) => f.parseStatus !== "parsed" || f.rejectedCount > 0,
    ).length,
    totalRejectedRows: files.reduce((sum, f) => sum + f.rejectedCount, 0),
  };
}

/** 从结构化 409 ApiError.details 提取阻断交易 ID 或冲突候选 ID */
export function extractConflictIds(err: unknown): {
  transactionIds: string[];
  candidateIds: string[];
} {
  if (!(err instanceof ApiError) || !err.details || typeof err.details !== "object") {
    return { transactionIds: [], candidateIds: [] };
  }
  const detail = err.details as { transactionIds?: unknown; candidateIds?: unknown };
  const toStrList = (v: unknown): string[] =>
    Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  return {
    transactionIds: toStrList(detail.transactionIds),
    candidateIds: toStrList(detail.candidateIds),
  };
}

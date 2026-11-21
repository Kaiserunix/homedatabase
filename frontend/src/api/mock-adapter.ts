/**
 * 内存 Mock 仿真适配层：仅在 VITE_USE_MOCKS=true 时被 resources.ts 调用。
 *
 * 与 HTTP 传输层（./http）完全隔离；写操作按后端服务关键规则做契约级仿真与回显（不落库），
 * 非法请求同样以 ApiError(401/403/404/409/422) 拒绝，绝不吞成假成功。
 */
import { ApiError } from "./client";
import type { ReportRange, TransactionFilter } from "./http";
import { ledgerMonthKey } from "@/lib/ledger-date";
import {
  accounts, backups, categories, CURRENT_USER_ID, importBatches, members,
  passkeys, periodLocks, reportTiers, splits, transactions,
} from "@/data/mock";
import type {
  Account, AccountCreateInput, AccountGrant, AccountUpdateInput, AuthSession,
  BackupRecord, Category, CategoryCreateInput, CategoryUpdateInput, ImportBatch,
  ImportCandidate, ImportCandidatePatch, ImportJob, Member, NewSplitInput,
  NewTransactionInput, Passkey, PeriodLock, PeriodLockInput, ReportTier, ReportTierMeta,
  Split, SplitResponseInput, SplitShareUpdateInput, Transaction, TransactionUpdateInput,
  WebAuthnCeremony,
} from "@/data/types";

let mockLatency = 300;

/** 测试/演示用：注入 mock 延迟毫秒数 */
export function setMockLatency(ms: number): void {
  mockLatency = ms;
}

function mockResolve<T>(data: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(data), mockLatency));
}

function mockNotFound(message: string): Promise<never> {
  return new Promise((_, reject) =>
    setTimeout(() => reject(new ApiError(message, 404, { code: "NOT_FOUND" })), mockLatency),
  );
}

/** mock 409：extra 结构化写入 ApiError.details（与后端 HTTPException(409, detail={...}) 对齐） */
function mockConflict(message: string, extra?: Record<string, unknown>): Promise<never> {
  return new Promise((_, reject) =>
    setTimeout(
      () =>
        reject(
          new ApiError(message, 409, {
            code: "CONFLICT",
            details: extra ? { message, ...extra } : undefined,
          }),
        ),
      mockLatency,
    ),
  );
}

function mockUnauthorized(message: string): Promise<never> {
  return new Promise((_, reject) =>
    setTimeout(() => reject(new ApiError(message, 401, { code: "UNAUTHORIZED" })), mockLatency),
  );
}

const clone = <T>(value: T): T => structuredClone(value);

const localId = (prefix: string) => `${prefix}-local-${Date.now()}`;

const unprocessable = (msg: string): ApiError => new ApiError(msg, 422, { code: "VALIDATION" });

/** 与后端 calculate_base_minor 一致：十进制精确乘 + HALF_UP 取整（全程 BigInt，不经浮点） */
function convertByFxRate(amountCents: number, fxRate: string): number {
  const m = /^(\d+)(?:\.(\d+))?$/.exec(fxRate.trim());
  if (!m) throw unprocessable("汇率无效");
  const frac = m[2] ?? "";
  const scale = 10n ** BigInt(frac.length);
  const rateScaled = BigInt(m[1] + frac);
  const product = BigInt(amountCents) * rateScaled;
  // HALF_UP：(2p + s) / 2s 向下取整等价于四舍五入
  const converted = (product * 2n + scale) / (scale * 2n);
  if (converted < 1n || converted > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw unprocessable("汇率折算结果无效");
  }
  return Number(converted);
}

type LocalLine = Transaction["lines"][number];

/**
 * mock 模式按后端 create_transaction 已实现的关键规则拼出借贷平衡的交易（仅回显，不落库）。
 */
function buildLocalTransaction(input: NewTransactionInput): Transaction {
  const amt = input.amountCents;
  const requireSafePositive = (n: number): void => {
    if (!Number.isSafeInteger(n) || n <= 0) throw unprocessable("金额必须为不超过安全整数范围的正整数分");
  };
  requireSafePositive(amt);
  if (input.baseAmountCents != null) requireSafePositive(input.baseAmountCents);
  const account = accounts.find((a) => a.id === input.accountId);
  if (!account) throw new ApiError("账户不存在", 404, { code: "NOT_FOUND" });
  const accountCurrency = account.currency;
  if (input.type === "transfer") {
    if (!input.toAccountId || input.toAccountId === input.accountId) {
      throw unprocessable("转账必须选择不同的转入账户");
    }
    if (input.categoryId != null) throw unprocessable("转账不能设置收支分类");
  } else if (input.toAccountId != null) {
    throw unprocessable("只有转账可以设置转入账户");
  }
  if (input.type === "refund" && !input.refundOf) throw unprocessable("退款必须关联原支出");
  if (input.type !== "refund" && input.refundOf != null) throw unprocessable("只有退款可以关联原支出");
  let categoryId = input.categoryId ?? null;
  let refundOriginal: Transaction | null = null;
  if (input.type === "refund") {
    const original = transactions.find((t) => t.id === input.refundOf);
    if (!original || original.type !== "expense") throw unprocessable("退款关联的原支出不存在");
    refundOriginal = original;
    if (input.currency !== original.currency) throw unprocessable("退款币种必须与原支出一致");
    if (categoryId !== null && categoryId !== original.categoryId) {
      throw unprocessable("退款分类必须继承原支出");
    }
    categoryId = original.categoryId;
  }
  if (input.type !== "transfer" && categoryId !== null) {
    const cat = categories.find((c) => c.id === categoryId);
    if (!cat || !cat.active) throw unprocessable("分类不存在");
    const expected = input.type === "income" ? "income" : "expense";
    if (cat.kind !== expected) throw unprocessable("分类与收支类型不匹配");
  }
  if (input.type === "transfer") {
    const target = accounts.find((a) => a.id === input.toAccountId);
    if (!target) throw new ApiError("账户不存在", 404, { code: "NOT_FOUND" });
    if (account.currency !== target.currency) {
      throw unprocessable("首版不支持两个不同币种账户直接转账");
    }
    if (input.currency !== account.currency) {
      throw unprocessable("转账币种必须与转出、转入账户币种一致");
    }
  } else if (accountCurrency !== input.currency && accountCurrency !== "CNY") {
    throw unprocessable("交易币种必须与账户币种一致，或由基础币账户按人民币实扣金额入账");
  }
  let baseAmt: number;
  if (input.currency === "CNY") {
    if (input.fxRate != null && Number(String(input.fxRate).trim()) !== 1) {
      throw unprocessable("本币交易汇率必须等于 1");
    }
    if (input.baseAmountCents != null && input.baseAmountCents !== amt) {
      throw unprocessable("本币交易折算金额必须等于交易金额");
    }
    baseAmt = amt;
  } else {
    const converted = input.fxRate != null ? convertByFxRate(amt, String(input.fxRate)) : null;
    if (input.baseAmountCents != null) {
      if (converted !== null && Math.abs(converted - input.baseAmountCents) > 1) {
        throw unprocessable("汇率与本币折算金额不一致");
      }
      baseAmt = input.baseAmountCents;
    } else if (converted !== null) {
      baseAmt = converted;
    } else {
      throw unprocessable("外币交易必须提供实际人民币金额或固定汇率");
    }
  }
  if (refundOriginal) {
    const refunded = transactions
      .filter((t) => t.type === "refund" && t.refundOf === refundOriginal.id)
      .reduce((s, t) => s + t.baseAmountCents, 0);
    if (refunded + baseAmt > refundOriginal.baseAmountCents) {
      throw unprocessable("累计退款金额不能超过原支出");
    }
  }
  const accountAmount = accountCurrency === input.currency ? amt : baseAmt;
  const acctLine = (accountId: string, currency: string, debitCents: number, creditCents: number): LocalLine => ({
    accountId, ledgerCode: null, debitCents, creditCents,
    accountAmountCents: accountAmount, accountCurrency: currency,
  });
  const code = `${input.type === "income" ? "income" : "expense"}:${categoryId ?? "uncategorized"}`;
  const plLine = (debitCents: number, creditCents: number): LocalLine => ({
    accountId: null, ledgerCode: code, debitCents, creditCents,
    accountAmountCents: null, accountCurrency: null,
  });
  const lines =
    input.type === "transfer"
      ? [
          acctLine(input.toAccountId ?? "", accountCurrency, baseAmt, 0),
          acctLine(input.accountId, accountCurrency, 0, baseAmt),
        ]
      : input.type === "income"
        ? [
            acctLine(input.accountId, accountCurrency, baseAmt, 0),
            plLine(0, baseAmt),
          ]
        : input.type === "refund"
          ? [
              acctLine(input.accountId, accountCurrency, baseAmt, 0),
              plLine(0, baseAmt),
            ]
          : [
              plLine(baseAmt, 0),
              acctLine(input.accountId, accountCurrency, 0, baseAmt),
            ];
  return {
    id: localId("t"),
    date: input.date,
    type: input.type,
    payee: input.payee,
    note: input.note,
    categoryId,
    currency: input.currency,
    amountCents: amt,
    baseAmountCents: baseAmt,
    baseCurrency: "CNY",
    lines,
    source: "manual",
    createdBy: CURRENT_USER_ID,
    version: 1,
    ...(input.refundOf ? { refundOf: input.refundOf } : {}),
    ...(input.fxRate != null ? { fxRate: String(input.fxRate) } : {}),
  };
}

/** 与后端 _recompute_status 一致：任一 returned → returned；全部 accepted → settled；否则 open */
function recomputeSplitStatus(split: Split): void {
  if (split.shares.some((sh) => sh.status === "returned")) split.status = "returned";
  else if (split.shares.every((sh) => sh.status === "accepted")) split.status = "settled";
  else split.status = "open";
}

export const mockApi = {
  // ==================== 认证 ====================
  login(username: string, password: string): Promise<AuthSession> {
    const member = members.find((m) => m.username === username);
    if (!member || !password) return mockUnauthorized("用户名或密码错误");
    return mockResolve({ member, expiresAt: new Date(Date.now() + 7 * 86400_000).toISOString() });
  },

  logout(): Promise<void> {
    return mockResolve(undefined);
  },

  fetchMe(): Promise<Member> {
    const me = members.find((m) => m.id === CURRENT_USER_ID)!;
    return mockResolve(me);
  },

  passkeyAuthenticationOptions(username: string): Promise<WebAuthnCeremony> {
    if (!members.some((m) => m.username === username)) return mockUnauthorized("用户名不存在");
    return mockResolve({
      ceremonyId: localId("ceremony"),
      options: { challenge: "mock-challenge", rpId: "localhost", userVerification: "preferred" },
    });
  },

  passkeyAuthenticationVerify(_ceremonyId: string, _credential: unknown): Promise<AuthSession> {
    const me = members.find((m) => m.id === CURRENT_USER_ID)!;
    return mockResolve({ member: me, expiresAt: new Date(Date.now() + 7 * 86400_000).toISOString() });
  },

  passkeyRegistrationOptions(): Promise<WebAuthnCeremony> {
    return mockResolve({
      ceremonyId: localId("ceremony"),
      options: { challenge: "mock-challenge", rp: { name: "家庭账本" }, userVerification: "preferred" },
    });
  },

  passkeyRegistrationVerify(_ceremonyId: string, name: string, _credential: unknown): Promise<Passkey> {
    return mockResolve({
      id: localId("pk"),
      name,
      createdAt: new Date().toISOString(),
      lastUsedAt: new Date().toISOString(),
    });
  },

  listPasskeys(): Promise<Passkey[]> {
    return mockResolve(passkeys);
  },

  deletePasskey(id: string): Promise<void> {
    if (!passkeys.some((p) => p.id === id)) return mockNotFound("Passkey 不存在");
    return mockResolve(undefined);
  },

  // ==================== 账户 ====================
  listAccounts(): Promise<Account[]> {
    return mockResolve(accounts);
  },

  createAccount(input: AccountCreateInput): Promise<Account> {
    return mockResolve<Account>({
      id: localId("a"),
      name: input.name,
      kind: input.kind,
      scope: input.scope,
      ownerId: CURRENT_USER_ID,
      permission: "manage",
      currency: input.currency,
      balanceCents: input.openingBalanceCents,
      icon: input.icon,
      version: 1,
    });
  },

  updateAccount(id: string, input: AccountUpdateInput): Promise<Account> {
    const account = accounts.find((a) => a.id === id);
    if (!account) return mockNotFound("账户不存在");
    return mockResolve<Account>({
      ...clone(account),
      name: input.name ?? account.name,
      icon: input.icon ?? account.icon,
      version: account.version + 1,
    });
  },

  listAccountGrants(accountId: string): Promise<AccountGrant[]> {
    if (!accounts.some((a) => a.id === accountId)) return mockNotFound("账户不存在");
    return mockResolve<AccountGrant[]>([]);
  },

  setAccountGrant(
    accountId: string,
    memberId: string,
    permission: "view" | "manage",
  ): Promise<AccountGrant> {
    if (!accounts.some((a) => a.id === accountId)) return mockNotFound("账户不存在");
    if (!members.some((m) => m.id === memberId)) return mockNotFound("成员不存在");
    return mockResolve<AccountGrant>({ memberId, permission });
  },

  deleteAccountGrant(accountId: string, _memberId: string): Promise<void> {
    if (!accounts.some((a) => a.id === accountId)) return mockNotFound("账户不存在");
    return mockResolve(undefined);
  },

  // ==================== 分类 ====================
  listCategories(opts?: { includeInactive?: boolean }): Promise<Category[]> {
    return mockResolve(opts?.includeInactive ? categories : categories.filter((c) => c.active));
  },

  createCategory(input: CategoryCreateInput): Promise<Category> {
    return mockResolve<Category>({
      id: localId("c"),
      name: input.name,
      kind: input.kind,
      icon: input.icon,
      parentId: input.parentId ?? null,
      active: true,
      version: 1,
    });
  },

  updateCategory(id: string, input: CategoryUpdateInput): Promise<Category> {
    const category = categories.find((c) => c.id === id);
    if (!category) return mockNotFound("分类不存在");
    return mockResolve<Category>({
      ...clone(category),
      name: input.name ?? category.name,
      icon: input.icon ?? category.icon,
      active: input.active ?? category.active,
      version: category.version + 1,
    });
  },

  // ==================== 交易 ====================
  listTransactions(filter: TransactionFilter = {}): Promise<Transaction[]> {
    const list = transactions.filter(
      (t) =>
        (!filter.type || t.type === filter.type) &&
        (!filter.accountId || t.lines.some((l) => l.accountId === filter.accountId)) &&
        (!filter.month || ledgerMonthKey(t.date) === filter.month) &&
        (!filter.keyword || t.payee.includes(filter.keyword) || t.note.includes(filter.keyword)),
    );
    return mockResolve(list);
  },

  getTransaction(id: string): Promise<Transaction> {
    const txn = transactions.find((t) => t.id === id);
    return txn ? mockResolve(txn) : mockNotFound("交易不存在");
  },

  createTransaction(input: NewTransactionInput): Promise<Transaction> {
    let built: Transaction;
    try {
      built = buildLocalTransaction(input);
    } catch (err) {
      return Promise.reject(err);
    }
    return mockResolve(built);
  },

  updateTransaction(id: string, input: TransactionUpdateInput): Promise<Transaction> {
    const txn = transactions.find((t) => t.id === id);
    if (!txn) return mockNotFound("交易不存在");
    const next = clone(txn);
    if (input.payee !== undefined && input.payee !== null) next.payee = input.payee;
    if (input.note !== undefined && input.note !== null) next.note = input.note;
    if (input.categoryId !== undefined) next.categoryId = input.categoryId;
    next.version += 1;
    return mockResolve(next);
  },

  deleteTransaction(id: string, _reason: string): Promise<void> {
    if (!transactions.some((t) => t.id === id)) return mockNotFound("交易不存在");
    return mockResolve(undefined);
  },

  listRecycledTransactions(): Promise<Transaction[]> {
    return mockResolve<Transaction[]>([]);
  },

  restoreTransaction(id: string, _expectedVersion: number): Promise<Transaction> {
    const txn = transactions.find((t) => t.id === id);
    return txn ? mockResolve(txn) : mockNotFound("交易不存在");
  },

  // ==================== 导入 ====================
  uploadImport(
    files: File[],
    source: string,
    targetAccountId?: string | null,
  ): Promise<ImportBatch> {
    const now = new Date().toISOString();
    return mockResolve<ImportBatch>({
      id: localId("b"),
      filename: files[0]?.name ?? "导入文件",
      source,
      importedAt: now,
      status: "queued",
      ownerId: CURRENT_USER_ID,
      targetAccountId: targetAccountId ?? null,
      jobId: localId("job"),
      progress: 0,
      message: "已上传，排队解析中",
      fileCount: files.length,
      filesProcessed: 0,
      candidateCount: 0,
      needsOcrCount: 0,
      failedFileCount: 0,
      version: 1,
      files: files.map((f, i) => ({
        id: `${localId("f")}-${i}`,
        filename: f.name,
        mimeType: f.type || "application/octet-stream",
        sizeBytes: f.size,
        sha256: "mock-sha256",
        parseStatus: "processing" as const,
        parseMessage: "",
        recordCount: 0,
        candidateCount: 0,
        rejectedCount: 0,
        duplicateOfFileId: null,
        encrypted: true,
        expiresAt: null,
      })),
      candidates: [],
    });
  },

  getImportStatus(batchId: string): Promise<ImportJob> {
    const batch = importBatches.find((b) => b.id === batchId);
    if (!batch) return mockNotFound("导入批次不存在");
    return mockResolve<ImportJob>({
      id: batch.jobId ?? localId("job"),
      batchId: batch.id,
      status: batch.status,
      progress: batch.progress,
      message: batch.message,
      fileCount: batch.fileCount,
      filesProcessed: batch.filesProcessed,
      candidateCount: batch.candidateCount,
      needsOcrCount: batch.needsOcrCount,
      failedFileCount: batch.failedFileCount,
    });
  },

  listImportBatches(): Promise<ImportBatch[]> {
    return mockResolve(importBatches);
  },

  getImportBatch(id: string): Promise<ImportBatch> {
    const batch = importBatches.find((b) => b.id === id);
    return batch ? mockResolve(batch) : mockNotFound("导入批次不存在");
  },

  patchImportCandidate(
    batchId: string,
    candidateId: string,
    patch: ImportCandidatePatch,
  ): Promise<ImportCandidate> {
    const batch = importBatches.find((b) => b.id === batchId);
    if (!batch) return mockNotFound("导入批次不存在");
    if (batch.status !== "reviewing") return mockConflict("该批次已结束复核");
    const candidate = batch.candidates.find((c) => c.id === candidateId);
    if (!candidate) return mockNotFound("候选不存在");
    if (candidate.status !== "pending") return mockConflict("候选账目已经处理");

    const next = clone(candidate);
    if (patch.date !== undefined && patch.date !== null) {
      const parsedTime = new Date(patch.date).getTime();
      if (Number.isNaN(parsedTime)) return Promise.reject(unprocessable("候选日期格式无效"));
      next.date = new Date(parsedTime).toISOString();
    }
    if (patch.payee !== undefined && patch.payee !== null) {
      const trimmed = patch.payee.trim();
      if (!trimmed) return Promise.reject(unprocessable("商户名称不能为空"));
      next.payee = trimmed;
    }
    if (patch.amountCents !== undefined && patch.amountCents !== null) {
      if (!Number.isSafeInteger(patch.amountCents) || patch.amountCents <= 0) {
        return Promise.reject(unprocessable("金额必须为不超过安全整数范围的正整数分"));
      }
      next.amountCents = patch.amountCents;
      // 与后端 services/imports.py:patch_candidate 对齐：改原币金额但未同步提交折算金额时清空旧折算
      if (patch.baseAmountCents === undefined) {
        next.baseAmountCents = null;
      }
    }
    if (patch.suggestedCategoryId !== undefined) next.suggestedCategoryId = patch.suggestedCategoryId;
    if (patch.targetAccountId !== undefined) next.targetAccountId = patch.targetAccountId;
    if (patch.fxRate !== undefined) {
      next.fxRate = patch.fxRate === null ? null : String(patch.fxRate);
    }
    if (patch.baseAmountCents !== undefined) {
      next.baseAmountCents = patch.baseAmountCents;
    }

    // 与后端 _candidate_fx_status 对齐校验汇率与本币折算一致性
    if (next.validationStatus !== "exact_duplicate" && next.validationStatus !== "duplicate_source_record") {
      try {
        if (next.currency === "CNY") {
          if (next.fxRate != null || next.baseAmountCents != null) {
            return Promise.reject(unprocessable("本币候选不应填写汇率或本币折算金额"));
          }
          next.validationStatus = "valid";
        } else if (next.fxRate == null && next.baseAmountCents == null) {
          next.validationStatus = "needs_fx_review";
        } else if (next.fxRate != null && next.baseAmountCents != null) {
          const calculated = convertByFxRate(next.amountCents, String(next.fxRate));
          if (Math.abs(calculated - next.baseAmountCents) > 1) {
            return Promise.reject(unprocessable("汇率与本币折算金额不一致"));
          }
          next.validationStatus = "valid";
        } else if (next.fxRate != null) {
          convertByFxRate(next.amountCents, String(next.fxRate));
          next.validationStatus = "valid";
        } else {
          next.validationStatus = "valid";
        }
      } catch (err) {
        return Promise.reject(err);
      }
    }

    next.version += 1;
    return mockResolve(next);
  },

  confirmImportCandidates(batchId: string, candidateIds: string[]): Promise<ImportBatch> {
    const batch = importBatches.find((b) => b.id === batchId);
    if (!batch) return mockNotFound("导入批次不存在");
    const next = clone(batch);
    for (const c of next.candidates) {
      if (candidateIds.includes(c.id)) {
        c.status = "confirmed";
        c.version += 1;
      }
    }
    return mockResolve(next);
  },

  skipDuplicateCandidates(batchId: string, candidateIds: string[]): Promise<ImportBatch> {
    const batch = importBatches.find((b) => b.id === batchId);
    if (!batch) return mockNotFound("导入批次不存在");
    const next = clone(batch);
    for (const c of next.candidates) {
      if (candidateIds.includes(c.id)) {
        c.status = "skipped";
        c.version += 1;
      }
    }
    return mockResolve(next);
  },

  undoImportBatch(batchId: string): Promise<ImportBatch> {
    const batch = importBatches.find((b) => b.id === batchId);
    if (!batch) return mockNotFound("导入批次不存在");
    if (batch.status !== "posted" && batch.status !== "needs_ocr") {
      return mockConflict("只有包含已入账账目的批次可以撤销");
    }
    const posted = transactions.filter((t) => t.batchId === batch.id);
    if (posted.length === 0) return mockConflict("该批次没有可撤销的已入账账目");
    const refundedIds = new Set(
      transactions.filter((t) => t.type === "refund").map((t) => t.refundOf),
    );
    const blocked = posted
      .filter((t) => t.version > 1 || t.splitId != null || refundedIds.has(t.id))
      .map((t) => t.id);
    if (blocked.length > 0) {
      return mockConflict("部分账目后来被修改、分摊、退款或位于锁定账期，不能整批撤销", {
        transactionIds: blocked,
      });
    }
    const next = clone(batch);
    next.status = "undone";
    next.version += 1;
    return mockResolve(next);
  },

  // ==================== 分摊 ====================
  listSplits(): Promise<Split[]> {
    return mockResolve(splits);
  },

  createSplit(input: NewSplitInput): Promise<Split> {
    const txn = transactions.find((t) => t.id === input.txnId);
    if (!txn) return mockNotFound("交易不存在");
    if (txn.type !== "expense") return Promise.reject(unprocessable("只有支出可以分摊"));
    if (input.totalCents !== txn.amountCents) {
      return Promise.reject(unprocessable("分摊总额必须等于原交易金额"));
    }
    if (txn.splitId != null || splits.some((s) => s.txnId === txn.id)) {
      return mockConflict("该账目已经存在分摊");
    }
    if (!input.shares.every((sh) => members.some((m) => m.id === sh.memberId))) {
      return Promise.reject(unprocessable("分摊成员不存在或已停用"));
    }
    if (!input.shares.some((sh) => sh.memberId === CURRENT_USER_ID)) {
      return Promise.reject(unprocessable("分摊必须保留发起人的份额"));
    }
    const split: Split = {
      id: localId("s"),
      txnId: input.txnId,
      title: txn.payee,
      totalCents: txn.amountCents,
      currency: txn.currency,
      categoryId: txn.categoryId,
      transactionDate: txn.date,
      createdBy: CURRENT_USER_ID,
      createdAt: new Date().toISOString(),
      status: "open",
      version: 1,
      shares: input.shares.map((sh) => ({
        memberId: sh.memberId,
        shareCents: sh.shareCents,
        status: (sh.memberId === CURRENT_USER_ID ? "accepted" : "pending") as Split["shares"][number]["status"],
      })),
    };
    recomputeSplitStatus(split);
    return mockResolve(split);
  },

  respondToSplit(splitId: string, input: SplitResponseInput): Promise<Split> {
    const split = splits.find((s) => s.id === splitId);
    if (!split) return mockNotFound("分摊不存在");
    if (split.version !== input.expectedVersion) {
      return mockConflict("分摊已被修改，请刷新后重新审阅");
    }
    const mine = split.shares.find((sh) => sh.memberId === CURRENT_USER_ID);
    if (!mine) {
      return Promise.reject(new ApiError("这笔分摊没有分给你", 403, { code: "FORBIDDEN" }));
    }
    if (mine.status !== "pending") return mockConflict("这笔分摊已经处理");
    const next = clone(split);
    const target = next.shares.find((sh) => sh.memberId === CURRENT_USER_ID)!;
    target.status = input.accept ? "accepted" : "returned";
    target.note = input.note ?? null;
    recomputeSplitStatus(next);
    return mockResolve(next);
  },

  updateSplitShare(
    splitId: string,
    memberId: string,
    input: SplitShareUpdateInput,
  ): Promise<Split> {
    const split = splits.find((s) => s.id === splitId);
    if (!split) return mockNotFound("分摊不存在");
    if (split.createdBy !== CURRENT_USER_ID) {
      return Promise.reject(new ApiError("只有分摊发起人可以修改", 403, { code: "FORBIDDEN" }));
    }
    if (split.version !== input.expectedVersion) {
      return mockConflict("分摊已被修改，请刷新后重试");
    }
    const target = split.shares.find((sh) => sh.memberId === memberId);
    const creator = split.shares.find((sh) => sh.memberId === CURRENT_USER_ID);
    if (!target) return mockNotFound("分摊成员不存在");
    if (target.memberId === CURRENT_USER_ID || !creator) {
      return Promise.reject(unprocessable("请保留发起人的份额用于平衡差额"));
    }
    const delta = input.shareCents - target.shareCents;
    if (creator.shareCents - delta <= 0) {
      return Promise.reject(unprocessable("修改后发起人的份额必须大于零"));
    }
    const next = clone(split);
    for (const sh of next.shares) {
      sh.note = null;
      if (sh.memberId === memberId) {
        sh.shareCents = input.shareCents;
        sh.status = "pending";
      } else if (sh.memberId === CURRENT_USER_ID) {
        sh.shareCents -= delta;
        sh.status = "accepted";
      } else {
        sh.status = "pending";
      }
    }
    next.version += 1;
    next.status = "open";
    return mockResolve(next);
  },

  // ==================== 成员 / 备份 / 锁账 / 报表 ====================
  listMembers(): Promise<Member[]> {
    return mockResolve(members);
  },

  listBackups(): Promise<BackupRecord[]> {
    return mockResolve(backups);
  },

  createBackup(): Promise<BackupRecord> {
    return mockResolve<BackupRecord>({
      id: localId("bk"),
      createdAt: new Date().toISOString(),
      sizeMb: 0,
      encrypted: true,
      sha256: null,
      locations: [
        { kind: "tencent", status: "syncing" },
        { kind: "local", status: "syncing" },
        { kind: "overseas", status: "syncing" },
      ],
      lastVerifiedAt: null,
      status: "creating",
    });
  },

  verifyBackup(id: string): Promise<BackupRecord> {
    const backup = backups.find((b) => b.id === id);
    if (!backup) return mockNotFound("备份不存在");
    return mockResolve<BackupRecord>({ ...clone(backup), lastVerifiedAt: new Date().toISOString() });
  },

  downloadBackup(id: string): Promise<Blob> {
    const backup = backups.find((b) => b.id === id);
    if (!backup) return mockNotFound("备份不存在");
    return mockResolve(new Blob([`mock backup ${id}`], { type: "application/octet-stream" }));
  },

  listPeriodLocks(): Promise<PeriodLock[]> {
    return mockResolve(periodLocks);
  },

  createPeriodLock(input: PeriodLockInput): Promise<PeriodLock> {
    return mockResolve<PeriodLock>({
      id: localId("pl"),
      startsOn: input.startsOn,
      endsOn: input.endsOn,
      reason: input.reason,
      lockedBy: CURRENT_USER_ID,
      createdAt: new Date().toISOString(),
    });
  },

  deletePeriodLock(id: string, _reason: string): Promise<void> {
    if (!periodLocks.some((p) => p.id === id)) return mockNotFound("锁账记录不存在");
    return mockResolve(undefined);
  },

  listReportTiers(): Promise<ReportTierMeta[]> {
    return mockResolve(reportTiers);
  },

  exportReport(tier: ReportTier, range: ReportRange): Promise<Blob> {
    return mockResolve(new Blob([`mock report tier=${tier} ${range.from}~${range.to}`], { type: "text/plain" }));
  },
};

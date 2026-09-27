/**
 * 面向页面的 API 资源层：页面只依赖本文件与 ./useApi。
 *
 * 双模式：
 * - http（默认）：经 apiFetch 走同源 /api，会话靠 HttpOnly Cookie；
 * - mock：仅当 VITE_USE_MOCKS=true 时启用，数据取自 @/data/mock，
 *   ~300ms 延迟 resolve，写操作只回显不落库。
 *
 * 所有路径与字段以仓库根 openapi.yaml 为准（类型见 @/data/types）；
 * 金额一律为分（Cents）。任何分支都不允许把错误吞成假成功。
 */
import { ApiError, apiFetch } from "./client";
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
  TxnType, WebAuthnCeremony,
} from "@/data/types";

// 输入类型也从本层再导出：页面只依赖这一个 seams，不必直连 schema
export type {
  AccountCreateInput, AccountUpdateInput, CategoryCreateInput, CategoryUpdateInput,
  ImportCandidatePatch, NewSplitInput, NewTransactionInput, PeriodLockInput,
  SplitResponseInput, SplitShareUpdateInput, TransactionUpdateInput,
} from "@/data/types";

// ---------- 模式判定 ----------
export function isMockMode(): boolean {
  return import.meta.env.VITE_USE_MOCKS === "true";
}

// ---------- mock 延迟（测试可注入） ----------
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

/** mock 409：extra 并入 ApiError.data（如 undo 的 transactionIds） */
function mockConflict(message: string, extra?: Record<string, unknown>): Promise<never> {
  return new Promise((_, reject) =>
    setTimeout(
      () => reject(new ApiError(message, 409, { code: "CONFLICT", ...extra })),
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

// ==================== 认证 ====================

// POST /auth/login
export function login(username: string, password: string): Promise<AuthSession> {
  if (isMockMode()) {
    const member = members.find((m) => m.username === username);
    if (!member || !password) return mockUnauthorized("用户名或密码错误");
    return mockResolve({ member, expiresAt: new Date(Date.now() + 7 * 86400_000).toISOString() });
  }
  return apiFetch<AuthSession>("/auth/login", { method: "POST", body: { username, password } });
}

// POST /auth/logout
export function logout(): Promise<void> {
  if (isMockMode()) return mockResolve(undefined);
  return apiFetch<void>("/auth/logout", { method: "POST" });
}

// GET /auth/me
export function fetchMe(): Promise<Member> {
  if (isMockMode()) {
    const me = members.find((m) => m.id === CURRENT_USER_ID)!;
    return mockResolve(me);
  }
  return apiFetch<Member>("/auth/me");
}

// POST /auth/passkeys/authentication/options
export function passkeyAuthenticationOptions(username: string): Promise<WebAuthnCeremony> {
  if (isMockMode()) {
    if (!members.some((m) => m.username === username)) return mockUnauthorized("用户名不存在");
    return mockResolve({
      ceremonyId: localId("ceremony"),
      options: { challenge: "mock-challenge", rpId: "localhost", userVerification: "preferred" },
    });
  }
  return apiFetch<WebAuthnCeremony>("/auth/passkeys/authentication/options", {
    method: "POST",
    body: { username },
  });
}

// POST /auth/passkeys/authentication/verify
export function passkeyAuthenticationVerify(
  ceremonyId: string,
  credential: unknown,
): Promise<AuthSession> {
  if (isMockMode()) {
    const me = members.find((m) => m.id === CURRENT_USER_ID)!;
    return mockResolve({ member: me, expiresAt: new Date(Date.now() + 7 * 86400_000).toISOString() });
  }
  return apiFetch<AuthSession>("/auth/passkeys/authentication/verify", {
    method: "POST",
    body: { ceremonyId, credential },
  });
}

// POST /auth/passkeys/registration/options（依赖当前会话，无请求体）
export function passkeyRegistrationOptions(): Promise<WebAuthnCeremony> {
  if (isMockMode()) {
    return mockResolve({
      ceremonyId: localId("ceremony"),
      options: { challenge: "mock-challenge", rp: { name: "家庭账本" }, userVerification: "preferred" },
    });
  }
  return apiFetch<WebAuthnCeremony>("/auth/passkeys/registration/options", { method: "POST" });
}

// POST /auth/passkeys/registration/verify
export function passkeyRegistrationVerify(
  ceremonyId: string,
  name: string,
  credential: unknown,
): Promise<Passkey> {
  if (isMockMode()) {
    return mockResolve({
      id: localId("pk"),
      name,
      createdAt: new Date().toISOString(),
      lastUsedAt: new Date().toISOString(),
    });
  }
  return apiFetch<Passkey>("/auth/passkeys/registration/verify", {
    method: "POST",
    body: { ceremonyId, name, credential },
  });
}

// GET /passkeys
export function listPasskeys(): Promise<Passkey[]> {
  if (isMockMode()) return mockResolve(passkeys);
  return apiFetch<Passkey[]>("/passkeys");
}

// DELETE /passkeys/{id}
export function deletePasskey(id: string): Promise<void> {
  if (isMockMode()) {
    if (!passkeys.some((p) => p.id === id)) return mockNotFound("Passkey 不存在");
    return mockResolve(undefined);
  }
  return apiFetch<void>(`/passkeys/${id}`, { method: "DELETE" });
}

// ==================== 账户 ====================

// GET /accounts
export function listAccounts(): Promise<Account[]> {
  if (isMockMode()) return mockResolve(accounts);
  return apiFetch<Account[]>("/accounts");
}

// POST /accounts
export function createAccount(input: AccountCreateInput): Promise<Account> {
  if (isMockMode()) {
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
  }
  return apiFetch<Account>("/accounts", { method: "POST", body: input });
}

// PATCH /accounts/{id}（携带 expectedVersion 乐观锁）
export function updateAccount(id: string, input: AccountUpdateInput): Promise<Account> {
  if (isMockMode()) {
    const account = accounts.find((a) => a.id === id);
    if (!account) return mockNotFound("账户不存在");
    return mockResolve<Account>({
      ...clone(account),
      name: input.name ?? account.name,
      icon: input.icon ?? account.icon,
      version: account.version + 1,
    });
  }
  return apiFetch<Account>(`/accounts/${id}`, { method: "PATCH", body: input });
}

// GET /accounts/{id}/grants
export function listAccountGrants(accountId: string): Promise<AccountGrant[]> {
  if (isMockMode()) {
    if (!accounts.some((a) => a.id === accountId)) return mockNotFound("账户不存在");
    return mockResolve<AccountGrant[]>([]);
  }
  return apiFetch<AccountGrant[]>(`/accounts/${accountId}/grants`);
}

// PUT /accounts/{id}/grants/{memberId}
export function setAccountGrant(
  accountId: string,
  memberId: string,
  permission: "view" | "manage",
): Promise<AccountGrant> {
  if (isMockMode()) {
    if (!accounts.some((a) => a.id === accountId)) return mockNotFound("账户不存在");
    if (!members.some((m) => m.id === memberId)) return mockNotFound("成员不存在");
    return mockResolve<AccountGrant>({ memberId, permission });
  }
  return apiFetch<AccountGrant>(`/accounts/${accountId}/grants/${memberId}`, {
    method: "PUT",
    body: { permission },
  });
}

// DELETE /accounts/{id}/grants/{memberId}
export function deleteAccountGrant(accountId: string, memberId: string): Promise<void> {
  if (isMockMode()) {
    if (!accounts.some((a) => a.id === accountId)) return mockNotFound("账户不存在");
    return mockResolve(undefined);
  }
  return apiFetch<void>(`/accounts/${accountId}/grants/${memberId}`, { method: "DELETE" });
}

// ==================== 分类 ====================

// GET /categories（includeInactive=true 仅管理员可用，返回含已停用的完整列表）
export function listCategories(opts?: { includeInactive?: boolean }): Promise<Category[]> {
  if (isMockMode()) {
    // 与后端默认口径一致：默认只返回启用中的分类；includeInactive=true 返回全部
    return mockResolve(opts?.includeInactive ? categories : categories.filter((c) => c.active));
  }
  return apiFetch<Category[]>("/categories", {
    params: { includeInactive: opts?.includeInactive === true ? true : undefined },
  });
}

// POST /categories
export function createCategory(input: CategoryCreateInput): Promise<Category> {
  if (isMockMode()) {
    return mockResolve<Category>({
      id: localId("c"),
      name: input.name,
      kind: input.kind,
      icon: input.icon,
      parentId: input.parentId ?? null,
      active: true,
      version: 1,
    });
  }
  return apiFetch<Category>("/categories", { method: "POST", body: input });
}

// PATCH /categories/{id}（active:false 即停用；携带 expectedVersion）
export function updateCategory(id: string, input: CategoryUpdateInput): Promise<Category> {
  if (isMockMode()) {
    const category = categories.find((c) => c.id === id);
    if (!category) return mockNotFound("分类不存在");
    return mockResolve<Category>({
      ...clone(category),
      name: input.name ?? category.name,
      icon: input.icon ?? category.icon,
      active: input.active ?? category.active,
      version: category.version + 1,
    });
  }
  return apiFetch<Category>(`/categories/${id}`, { method: "PATCH", body: input });
}

// ==================== 交易 ====================

export interface TransactionFilter {
  type?: TxnType;
  accountId?: string;
  /** YYYY-MM，按上海账期月（ledgerMonthKey）匹配 */
  month?: string;
  /** 匹配 payee / note */
  keyword?: string;
}

// GET /transactions?type=&accountId=&month=&keyword=
export function listTransactions(filter: TransactionFilter = {}): Promise<Transaction[]> {
  if (isMockMode()) {
    const list = transactions.filter(
      (t) =>
        (!filter.type || t.type === filter.type) &&
        (!filter.accountId || t.lines.some((l) => l.accountId === filter.accountId)) &&
        (!filter.month || ledgerMonthKey(t.date) === filter.month) &&
        (!filter.keyword || t.payee.includes(filter.keyword) || t.note.includes(filter.keyword)),
    );
    return mockResolve(list);
  }
  return apiFetch<Transaction[]>("/transactions", { params: { ...filter } });
}

// GET /transactions/{id}
export function getTransaction(id: string): Promise<Transaction> {
  if (isMockMode()) {
    const txn = transactions.find((t) => t.id === id);
    return txn ? mockResolve(txn) : mockNotFound("交易不存在");
  }
  return apiFetch<Transaction>(`/transactions/${id}`);
}

/** 与后端 calculate_base_minor 一致：十进制精确乘 + HALF_UP 取整（全程 BigInt，不经浮点） */
function convertByFxRate(amountCents: number, fxRate: string): number {
  const m = /^(\d+)(?:\.(\d+))?$/.exec(fxRate.trim());
  if (!m) throw new ApiError("汇率无效", 422, { code: "VALIDATION" });
  const frac = m[2] ?? "";
  const scale = 10n ** BigInt(frac.length);
  const rateScaled = BigInt(m[1] + frac);
  const product = BigInt(amountCents) * rateScaled;
  // HALF_UP：(2p + s) / 2s 向下取整等价于四舍五入
  const converted = (product * 2n + scale) / (scale * 2n);
  if (converted < 1n || converted > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw new ApiError("汇率折算结果无效", 422, { code: "VALIDATION" });
  }
  return Number(converted);
}

type LocalLine = Transaction["lines"][number];

const unprocessable = (msg: string): ApiError => new ApiError(msg, 422, { code: "VALIDATION" });

/**
 * mock 模式按后端 create_transaction 已实现的关键规则拼出借贷平衡的交易（仅回显，不落库）。
 * 已覆盖的后端规则：形状校验（转账/退款字段约束）、账户存在与币种推导、同/跨币转账、
 * 退款原单存在且为活动支出 + 额度 + 币种与分类继承、分类有效性、本币 1:1、
 * 外币 fxRate/baseAmountCents 一致性与 HALF_UP 折算、整数 JS-safe。
 * 未模拟：权限/锁账期/审计等——本 mock 不是完整后端等价物。
 * 分录形态与后端 presenter 一致：账户行 accountId + accountAmountCents/accountCurrency
 * （账户币实际变动），损益行 accountId=null + ledgerCode（expense:/income: 前缀）。
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
  // ---- 形状约束（后端 NewTransactionInput.transaction_shape） ----
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
  // ---- 退款：原单/币种/分类继承（后端 _locked_refund_original + 继承规则） ----
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
  // ---- 分类有效性（后端 _category_for） ----
  if (input.type !== "transfer" && categoryId !== null) {
    const cat = categories.find((c) => c.id === categoryId);
    if (!cat || !cat.active) throw unprocessable("分类不存在");
    const expected = input.type === "income" ? "income" : "expense";
    if (cat.kind !== expected) throw unprocessable("分类与收支类型不匹配");
  }
  // ---- 转账币种约束（后端 create_transaction） ----
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
    // 第三币种账户无法推导账户币金额（后端同口径 422）
    throw unprocessable("交易币种必须与账户币种一致，或由基础币账户按人民币实扣金额入账");
  }
  // ---- 折算口径（后端 _base_amount，V1 家庭基础币固定 CNY） ----
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
  // ---- 退款额度（后端 _require_refund_capacity；mock 无删除态，全部为活动退款） ----
  if (refundOriginal) {
    const refunded = transactions
      .filter((t) => t.type === "refund" && t.refundOf === refundOriginal.id)
      .reduce((s, t) => s + t.baseAmountCents, 0);
    if (refunded + baseAmt > refundOriginal.baseAmountCents) {
      throw unprocessable("累计退款金额不能超过原支出");
    }
  }
  // 账户币金额：账户币种==交易币种记原币金额；基础币账户代付外币时记折后基础币金额
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
    // mock 回显：基础币固定 CNY（与后端折算口径一致）
    baseCurrency: "CNY",
    lines,
    source: "manual",
    createdBy: CURRENT_USER_ID,
    version: 1,
    ...(input.refundOf ? { refundOf: input.refundOf } : {}),
    ...(input.fxRate != null ? { fxRate: String(input.fxRate) } : {}),
  };
}

// POST /transactions
export function createTransaction(input: NewTransactionInput): Promise<Transaction> {
  if (isMockMode()) {
    // mock 与后端同口径校验，失败同样以 422 拒绝（不吞成假成功）
    let built: Transaction;
    try {
      built = buildLocalTransaction(input);
    } catch (err) {
      return Promise.reject(err);
    }
    return mockResolve(built);
  }
  return apiFetch<Transaction>("/transactions", { method: "POST", body: input });
}

// PATCH /transactions/{id}（携带 expectedVersion 乐观锁）
export function updateTransaction(id: string, input: TransactionUpdateInput): Promise<Transaction> {
  if (isMockMode()) {
    const txn = transactions.find((t) => t.id === id);
    if (!txn) return mockNotFound("交易不存在");
    const next = clone(txn);
    if (input.payee !== undefined && input.payee !== null) next.payee = input.payee;
    if (input.note !== undefined && input.note !== null) next.note = input.note;
    if (input.categoryId !== undefined) next.categoryId = input.categoryId;
    next.version += 1;
    return mockResolve(next);
  }
  return apiFetch<Transaction>(`/transactions/${id}`, { method: "PATCH", body: input });
}

// DELETE /transactions/{id}（DELETE 携带 JSON body { reason }）
export function deleteTransaction(id: string, reason: string): Promise<void> {
  if (isMockMode()) {
    if (!transactions.some((t) => t.id === id)) return mockNotFound("交易不存在");
    return mockResolve(undefined);
  }
  return apiFetch<void>(`/transactions/${id}`, { method: "DELETE", body: { reason } });
}

// GET /transactions/recycle-bin
export function listRecycledTransactions(): Promise<Transaction[]> {
  if (isMockMode()) return mockResolve<Transaction[]>([]); // mock 不落库，回收站恒为空
  return apiFetch<Transaction[]>("/transactions/recycle-bin");
}

// POST /transactions/{id}/restore
export function restoreTransaction(id: string, expectedVersion: number): Promise<Transaction> {
  if (isMockMode()) {
    const txn = transactions.find((t) => t.id === id);
    return txn ? mockResolve(txn) : mockNotFound("交易不存在");
  }
  return apiFetch<Transaction>(`/transactions/${id}/restore`, {
    method: "POST",
    body: { expectedVersion },
  });
}

// ==================== 导入 ====================

// POST /imports（multipart：files[] + source + targetAccountId）
export function uploadImport(
  files: File[],
  source: string,
  targetAccountId?: string | null,
): Promise<ImportBatch> {
  if (isMockMode()) {
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
  }
  const form = new FormData();
  for (const file of files) form.append("files", file);
  form.append("source", source);
  if (targetAccountId) form.append("targetAccountId", targetAccountId);
  return apiFetch<ImportBatch>("/imports", { method: "POST", body: form });
}

// GET /imports/{id}/status
export function getImportStatus(batchId: string): Promise<ImportJob> {
  if (isMockMode()) {
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
  }
  return apiFetch<ImportJob>(`/imports/${batchId}/status`);
}

// GET /import-batches
export function listImportBatches(): Promise<ImportBatch[]> {
  if (isMockMode()) return mockResolve(importBatches);
  return apiFetch<ImportBatch[]>("/import-batches");
}

// GET /import-batches/{id}
export function getImportBatch(id: string): Promise<ImportBatch> {
  if (isMockMode()) {
    const batch = importBatches.find((b) => b.id === id);
    return batch ? mockResolve(batch) : mockNotFound("导入批次不存在");
  }
  return apiFetch<ImportBatch>(`/import-batches/${id}`);
}

// PATCH /import-batches/{batchId}/candidates/{candidateId}（携带 expectedVersion）
export function patchImportCandidate(
  batchId: string,
  candidateId: string,
  patch: ImportCandidatePatch,
): Promise<ImportCandidate> {
  if (isMockMode()) {
    const batch = importBatches.find((b) => b.id === batchId);
    if (!batch) return mockNotFound("导入批次不存在");
    const candidate = batch.candidates.find((c) => c.id === candidateId);
    if (!candidate) return mockNotFound("候选不存在");
    const next = clone(candidate);
    if (patch.date !== undefined && patch.date !== null) next.date = patch.date;
    if (patch.payee !== undefined && patch.payee !== null) next.payee = patch.payee;
    if (patch.amountCents !== undefined && patch.amountCents !== null) next.amountCents = patch.amountCents;
    if (patch.suggestedCategoryId !== undefined) next.suggestedCategoryId = patch.suggestedCategoryId;
    if (patch.targetAccountId !== undefined) next.targetAccountId = patch.targetAccountId;
    if (patch.fxRate !== undefined && patch.fxRate !== null) next.fxRate = String(patch.fxRate);
    if (patch.baseAmountCents !== undefined && patch.baseAmountCents !== null) {
      next.baseAmountCents = patch.baseAmountCents;
    }
    // 汇率与折算金额补全后视为通过复核（真实后端同理返回新的 validationStatus）
    if (next.validationStatus === "needs_fx_review" && next.fxRate != null && next.baseAmountCents != null) {
      next.validationStatus = "valid";
    }
    next.version += 1;
    return mockResolve(next);
  }
  return apiFetch<ImportCandidate>(`/import-batches/${batchId}/candidates/${candidateId}`, {
    method: "PATCH",
    body: patch,
  });
}

// POST /import-batches/{id}/confirm
export function confirmImportCandidates(batchId: string, candidateIds: string[]): Promise<ImportBatch> {
  if (isMockMode()) {
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
  }
  return apiFetch<ImportBatch>(`/import-batches/${batchId}/confirm`, {
    method: "POST",
    body: { candidateIds },
  });
}

// POST /import-batches/{id}/skip-duplicates
export function skipDuplicateCandidates(batchId: string, candidateIds: string[]): Promise<ImportBatch> {
  if (isMockMode()) {
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
  }
  return apiFetch<ImportBatch>(`/import-batches/${batchId}/skip-duplicates`, {
    method: "POST",
    body: { candidateIds },
  });
}

// POST /import-batches/{id}/undo
export function undoImportBatch(batchId: string): Promise<ImportBatch> {
  if (isMockMode()) {
    // 复刻后端 undo_batch 的关键守卫：状态门禁 + 必须有已入账交易 + 阻断项检查
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
  }
  return apiFetch<ImportBatch>(`/import-batches/${batchId}/undo`, { method: "POST" });
}

// ==================== 分摊 ====================

// GET /splits
export function listSplits(): Promise<Split[]> {
  if (isMockMode()) return mockResolve(splits);
  return apiFetch<Split[]>("/splits");
}

/** 与后端 _recompute_status 一致：任一 returned → returned；全部 accepted → settled；否则 open */
function recomputeSplitStatus(split: Split): void {
  if (split.shares.some((sh) => sh.status === "returned")) split.status = "returned";
  else if (split.shares.every((sh) => sh.status === "accepted")) split.status = "settled";
  else split.status = "open";
}

// POST /splits
export function createSplit(input: NewSplitInput): Promise<Split> {
  if (isMockMode()) {
    // 复刻后端 create_split 的关键规则（仅回显，不落库）
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
      // 标题/总额/币种与后端 present_split 一致：一律从关联交易派生
      title: txn.payee,
      totalCents: txn.amountCents,
      currency: txn.currency,
      categoryId: txn.categoryId,
      transactionDate: txn.date,
      createdBy: CURRENT_USER_ID,
      createdAt: new Date().toISOString(),
      status: "open",
      version: 1,
      // 发起人份额直接 accepted，其余 pending
      shares: input.shares.map((sh) => ({
        memberId: sh.memberId,
        shareCents: sh.shareCents,
        status: (sh.memberId === CURRENT_USER_ID ? "accepted" : "pending") as Split["shares"][number]["status"],
      })),
    };
    recomputeSplitStatus(split);
    return mockResolve(split);
  }
  return apiFetch<Split>("/splits", { method: "POST", body: input });
}

// POST /splits/{id}/respond（携带 expectedVersion；退回 accept=false 必须带 note）
export function respondToSplit(splitId: string, input: SplitResponseInput): Promise<Split> {
  if (isMockMode()) {
    // 复刻后端 respond_to_split 的关键守卫
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
  }
  return apiFetch<Split>(`/splits/${splitId}/respond`, { method: "POST", body: input });
}

// PATCH /splits/{id}/shares/{memberId}（携带 expectedVersion；关键变更版本 +1）
export function updateSplitShare(
  splitId: string,
  memberId: string,
  input: SplitShareUpdateInput,
): Promise<Split> {
  if (isMockMode()) {
    // 复刻后端 update_share 的关键规则：仅发起人可改、发起人份额自动平衡差额、
    // 发起人始终保持 accepted，其余份额回到 pending，split.status 重置为 open
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
        sh.shareCents -= delta; // 发起人份额吸收差额
        sh.status = "accepted";
      } else {
        sh.status = "pending"; // 关键变更后其他方需重新确认
      }
    }
    next.version += 1;
    next.status = "open";
    return mockResolve(next);
  }
  return apiFetch<Split>(`/splits/${splitId}/shares/${memberId}`, {
    method: "PATCH",
    body: input,
  });
}

// ==================== 成员 / 备份 / 锁账 / 报表 ====================

// GET /members
export function listMembers(): Promise<Member[]> {
  if (isMockMode()) return mockResolve(members);
  return apiFetch<Member[]>("/members");
}

// GET /backups
export function listBackups(): Promise<BackupRecord[]> {
  if (isMockMode()) return mockResolve(backups);
  return apiFetch<BackupRecord[]>("/backups");
}

// POST /backups
export function createBackup(): Promise<BackupRecord> {
  if (isMockMode()) {
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
  }
  return apiFetch<BackupRecord>("/backups", { method: "POST" });
}

// POST /backups/{id}/verify（恢复演练）
export function verifyBackup(id: string): Promise<BackupRecord> {
  if (isMockMode()) {
    const backup = backups.find((b) => b.id === id);
    if (!backup) return mockNotFound("备份不存在");
    return mockResolve<BackupRecord>({ ...clone(backup), lastVerifiedAt: new Date().toISOString() });
  }
  return apiFetch<BackupRecord>(`/backups/${id}/verify`, { method: "POST" });
}

// GET /backups/{id}/download → 加密备份文件流
export function downloadBackup(id: string): Promise<Blob> {
  if (isMockMode()) {
    const backup = backups.find((b) => b.id === id);
    if (!backup) return mockNotFound("备份不存在");
    return mockResolve(new Blob([`mock backup ${id}`], { type: "application/octet-stream" }));
  }
  return apiFetch<Blob>(`/backups/${id}/download`);
}

// GET /period-locks
export function listPeriodLocks(): Promise<PeriodLock[]> {
  if (isMockMode()) return mockResolve(periodLocks);
  return apiFetch<PeriodLock[]>("/period-locks");
}

// POST /period-locks
export function createPeriodLock(input: PeriodLockInput): Promise<PeriodLock> {
  if (isMockMode()) {
    return mockResolve<PeriodLock>({
      id: localId("pl"),
      startsOn: input.startsOn,
      endsOn: input.endsOn,
      reason: input.reason,
      lockedBy: CURRENT_USER_ID,
      createdAt: new Date().toISOString(),
    });
  }
  return apiFetch<PeriodLock>("/period-locks", { method: "POST", body: input });
}

// DELETE /period-locks/{id}（DELETE 携带 JSON body { reason }）
export function deletePeriodLock(id: string, reason: string): Promise<void> {
  if (isMockMode()) {
    if (!periodLocks.some((p) => p.id === id)) return mockNotFound("锁账记录不存在");
    return mockResolve(undefined);
  }
  return apiFetch<void>(`/period-locks/${id}`, { method: "DELETE", body: { reason } });
}

// GET /report-tiers
export function listReportTiers(): Promise<ReportTierMeta[]> {
  if (isMockMode()) return mockResolve(reportTiers);
  return apiFetch<ReportTierMeta[]>("/report-tiers");
}

export interface ReportRange {
  /** ISO 日期，含 */
  from: string;
  /** ISO 日期，含 */
  to: string;
}

// GET /reports/export?tier=&from=&to → XLSX 文件流（后端生成，前端原样下载，不在浏览器重算）
export function exportReport(tier: ReportTier, range: ReportRange): Promise<Blob> {
  if (isMockMode()) {
    // mock 无真实文件：返回占位 Blob，页面可照常走下载演示流程
    return mockResolve(new Blob([`mock report tier=${tier} ${range.from}~${range.to}`], { type: "text/plain" }));
  }
  return apiFetch<Blob>("/reports/export", {
    params: { tier, from: range.from, to: range.to },
  });
}

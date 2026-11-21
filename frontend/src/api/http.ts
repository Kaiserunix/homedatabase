/**
 * 纯 HTTP 传输适配层：仅负责将领域方法映射为 OpenAPI 契约请求（apiFetch），
 * 不包含任何内存 mock 状态、模拟延迟或本地账务演算，与 UI/Mock 完全解耦。
 */
import { apiFetch } from "./client";
import type {
  Account, AccountCreateInput, AccountGrant, AccountUpdateInput, AuthSession,
  BackupRecord, CashflowRadarInput, CashflowRadarResult, Category, CategoryCreateInput,
  CategoryUpdateInput, CfoAskInput, CfoAskResult, CfoCapabilities, ImportBatch,
  ImportCandidate, ImportCandidatePatch, ImportJob, Member, NewSplitInput,
  NewTransactionInput, Passkey, PeriodLock, PeriodLockInput, ReportTier, ReportTierMeta,
  Split, SplitResponseInput, SplitShareUpdateInput, Transaction, TransactionUpdateInput,
  TxnType, WebAuthnCeremony,
} from "@/data/types";

export interface TransactionFilter {
  type?: TxnType;
  accountId?: string;
  /** YYYY-MM，按上海账期月（ledgerMonthKey）匹配 */
  month?: string;
  /** 匹配 payee / note */
  keyword?: string;
}

export interface ReportRange {
  /** ISO 日期，含 */
  from: string;
  /** ISO 日期，含 */
  to: string;
}

export const httpApi = {
  // ==================== 认证 ====================
  login(username: string, password: string): Promise<AuthSession> {
    return apiFetch<AuthSession>("/auth/login", { method: "POST", body: { username, password } });
  },

  logout(): Promise<void> {
    return apiFetch<void>("/auth/logout", { method: "POST" });
  },

  fetchMe(): Promise<Member> {
    return apiFetch<Member>("/auth/me");
  },

  passkeyAuthenticationOptions(username: string): Promise<WebAuthnCeremony> {
    return apiFetch<WebAuthnCeremony>("/auth/passkeys/authentication/options", {
      method: "POST",
      body: { username },
    });
  },

  passkeyAuthenticationVerify(ceremonyId: string, credential: unknown): Promise<AuthSession> {
    return apiFetch<AuthSession>("/auth/passkeys/authentication/verify", {
      method: "POST",
      body: { ceremonyId, credential },
    });
  },

  passkeyRegistrationOptions(): Promise<WebAuthnCeremony> {
    return apiFetch<WebAuthnCeremony>("/auth/passkeys/registration/options", { method: "POST" });
  },

  passkeyRegistrationVerify(ceremonyId: string, name: string, credential: unknown): Promise<Passkey> {
    return apiFetch<Passkey>("/auth/passkeys/registration/verify", {
      method: "POST",
      body: { ceremonyId, name, credential },
    });
  },

  listPasskeys(): Promise<Passkey[]> {
    return apiFetch<Passkey[]>("/passkeys");
  },

  deletePasskey(id: string): Promise<void> {
    return apiFetch<void>(`/passkeys/${id}`, { method: "DELETE" });
  },

  // ==================== 账户 ====================
  listAccounts(): Promise<Account[]> {
    return apiFetch<Account[]>("/accounts");
  },

  createAccount(input: AccountCreateInput): Promise<Account> {
    return apiFetch<Account>("/accounts", { method: "POST", body: input });
  },

  updateAccount(id: string, input: AccountUpdateInput): Promise<Account> {
    return apiFetch<Account>(`/accounts/${id}`, { method: "PATCH", body: input });
  },

  listAccountGrants(accountId: string): Promise<AccountGrant[]> {
    return apiFetch<AccountGrant[]>(`/accounts/${accountId}/grants`);
  },

  setAccountGrant(
    accountId: string,
    memberId: string,
    permission: "view" | "manage",
  ): Promise<AccountGrant> {
    return apiFetch<AccountGrant>(`/accounts/${accountId}/grants/${memberId}`, {
      method: "PUT",
      body: { permission },
    });
  },

  deleteAccountGrant(accountId: string, memberId: string): Promise<void> {
    return apiFetch<void>(`/accounts/${accountId}/grants/${memberId}`, { method: "DELETE" });
  },

  // ==================== 分类 ====================
  listCategories(opts?: { includeInactive?: boolean }): Promise<Category[]> {
    return apiFetch<Category[]>("/categories", {
      params: { includeInactive: opts?.includeInactive === true ? true : undefined },
    });
  },

  createCategory(input: CategoryCreateInput): Promise<Category> {
    return apiFetch<Category>("/categories", { method: "POST", body: input });
  },

  updateCategory(id: string, input: CategoryUpdateInput): Promise<Category> {
    return apiFetch<Category>(`/categories/${id}`, { method: "PATCH", body: input });
  },

  // ==================== 交易 ====================
  listTransactions(filter: TransactionFilter = {}): Promise<Transaction[]> {
    return apiFetch<Transaction[]>("/transactions", { params: { ...filter } });
  },

  getTransaction(id: string): Promise<Transaction> {
    return apiFetch<Transaction>(`/transactions/${id}`);
  },

  createTransaction(input: NewTransactionInput): Promise<Transaction> {
    return apiFetch<Transaction>("/transactions", { method: "POST", body: input });
  },

  updateTransaction(id: string, input: TransactionUpdateInput): Promise<Transaction> {
    return apiFetch<Transaction>(`/transactions/${id}`, { method: "PATCH", body: input });
  },

  deleteTransaction(id: string, reason: string): Promise<void> {
    return apiFetch<void>(`/transactions/${id}`, { method: "DELETE", body: { reason } });
  },

  listRecycledTransactions(): Promise<Transaction[]> {
    return apiFetch<Transaction[]>("/transactions/recycle-bin");
  },

  restoreTransaction(id: string, expectedVersion: number): Promise<Transaction> {
    return apiFetch<Transaction>(`/transactions/${id}/restore`, {
      method: "POST",
      body: { expectedVersion },
    });
  },

  // ==================== 导入 ====================
  uploadImport(
    files: File[],
    source: string,
    targetAccountId?: string | null,
  ): Promise<ImportBatch> {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    form.append("source", source);
    if (targetAccountId) form.append("targetAccountId", targetAccountId);
    return apiFetch<ImportBatch>("/imports", { method: "POST", body: form });
  },

  getImportStatus(batchId: string): Promise<ImportJob> {
    return apiFetch<ImportJob>(`/imports/${batchId}/status`);
  },

  listImportBatches(): Promise<ImportBatch[]> {
    return apiFetch<ImportBatch[]>("/import-batches");
  },

  getImportBatch(id: string): Promise<ImportBatch> {
    return apiFetch<ImportBatch>(`/import-batches/${id}`);
  },

  patchImportCandidate(
    batchId: string,
    candidateId: string,
    patch: ImportCandidatePatch,
  ): Promise<ImportCandidate> {
    return apiFetch<ImportCandidate>(`/import-batches/${batchId}/candidates/${candidateId}`, {
      method: "PATCH",
      body: patch,
    });
  },

  confirmImportCandidates(batchId: string, candidateIds: string[]): Promise<ImportBatch> {
    return apiFetch<ImportBatch>(`/import-batches/${batchId}/confirm`, {
      method: "POST",
      body: { candidateIds },
    });
  },

  skipDuplicateCandidates(batchId: string, candidateIds: string[]): Promise<ImportBatch> {
    return apiFetch<ImportBatch>(`/import-batches/${batchId}/skip-duplicates`, {
      method: "POST",
      body: { candidateIds },
    });
  },

  undoImportBatch(batchId: string): Promise<ImportBatch> {
    return apiFetch<ImportBatch>(`/import-batches/${batchId}/undo`, { method: "POST" });
  },

  // ==================== 分摊 ====================
  listSplits(): Promise<Split[]> {
    return apiFetch<Split[]>("/splits");
  },

  createSplit(input: NewSplitInput): Promise<Split> {
    return apiFetch<Split>("/splits", { method: "POST", body: input });
  },

  respondToSplit(splitId: string, input: SplitResponseInput): Promise<Split> {
    return apiFetch<Split>(`/splits/${splitId}/respond`, { method: "POST", body: input });
  },

  updateSplitShare(
    splitId: string,
    memberId: string,
    input: SplitShareUpdateInput,
  ): Promise<Split> {
    return apiFetch<Split>(`/splits/${splitId}/shares/${memberId}`, {
      method: "PATCH",
      body: input,
    });
  },

  // ==================== 成员 / 备份 / 锁账 / 报表 ====================
  listMembers(): Promise<Member[]> {
    return apiFetch<Member[]>("/members");
  },

  listBackups(): Promise<BackupRecord[]> {
    return apiFetch<BackupRecord[]>("/backups");
  },

  createBackup(): Promise<BackupRecord> {
    return apiFetch<BackupRecord>("/backups", { method: "POST" });
  },

  verifyBackup(id: string): Promise<BackupRecord> {
    return apiFetch<BackupRecord>(`/backups/${id}/verify`, { method: "POST" });
  },

  downloadBackup(id: string): Promise<Blob> {
    return apiFetch<Blob>(`/backups/${id}/download`);
  },

  listPeriodLocks(): Promise<PeriodLock[]> {
    return apiFetch<PeriodLock[]>("/period-locks");
  },

  createPeriodLock(input: PeriodLockInput): Promise<PeriodLock> {
    return apiFetch<PeriodLock>("/period-locks", { method: "POST", body: input });
  },

  deletePeriodLock(id: string, reason: string): Promise<void> {
    return apiFetch<void>(`/period-locks/${id}`, { method: "DELETE", body: { reason } });
  },

  listReportTiers(): Promise<ReportTierMeta[]> {
    return apiFetch<ReportTierMeta[]>("/report-tiers");
  },

  exportReport(tier: ReportTier, range: ReportRange): Promise<Blob> {
    return apiFetch<Blob>("/reports/export", {
      params: { tier, from: range.from, to: range.to },
    });
  },

  // ==================== AI CFO ====================
  getCfoCapabilities(): Promise<CfoCapabilities> {
    return apiFetch<CfoCapabilities>("/cfo/capabilities");
  },

  askCfo(input: CfoAskInput): Promise<CfoAskResult> {
    return apiFetch<CfoAskResult>("/cfo/ask", { method: "POST", body: input });
  },

  getCashflowRadar(input: CashflowRadarInput = {}): Promise<CashflowRadarResult> {
    return apiFetch<CashflowRadarResult>("/cfo/cashflow-radar", { method: "POST", body: input });
  },
};

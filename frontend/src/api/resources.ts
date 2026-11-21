/**
 * 面向页面与领域 hook 的统一 API 资源门面（Facade）：
 *
 * 传输解耦：
 * - http（默认）：委派给 ./http（纯 OpenAPI HTTP 客户端，无 mock 依赖）；
 * - mock：仅当 VITE_USE_MOCKS=true 时委派给 ./mock-adapter（内存契约仿真层）。
 *
 * 页面与业务状态只依赖本门面与 ./useApi，不感知底层传输细节。
 */
import { httpApi, type ReportRange, type TransactionFilter } from "./http";
import { mockApi, setMockLatency } from "./mock-adapter";
import type {
  Account, AccountCreateInput, AccountGrant, AccountUpdateInput, AuthSession,
  BackupRecord, Category, CategoryCreateInput, CategoryUpdateInput, ImportBatch,
  ImportCandidate, ImportCandidatePatch, ImportJob, Member, NewSplitInput,
  NewTransactionInput, Passkey, PeriodLock, PeriodLockInput, ReportTier, ReportTierMeta,
  Split, SplitResponseInput, SplitShareUpdateInput, Transaction, TransactionUpdateInput,
  WebAuthnCeremony,
} from "@/data/types";

export type {
  AccountCreateInput, AccountUpdateInput, CategoryCreateInput, CategoryUpdateInput,
  ImportCandidatePatch, NewSplitInput, NewTransactionInput, PeriodLockInput,
  SplitResponseInput, SplitShareUpdateInput, TransactionUpdateInput,
} from "@/data/types";

export type { ReportRange, TransactionFilter };
export { setMockLatency };

export function isMockMode(): boolean {
  return import.meta.env.VITE_USE_MOCKS === "true";
}

const activeAdapter = () => (isMockMode() ? mockApi : httpApi);

// ==================== 认证 ====================
export const login = (username: string, password: string): Promise<AuthSession> =>
  activeAdapter().login(username, password);

export const logout = (): Promise<void> =>
  activeAdapter().logout();

export const fetchMe = (): Promise<Member> =>
  activeAdapter().fetchMe();

export const passkeyAuthenticationOptions = (username: string): Promise<WebAuthnCeremony> =>
  activeAdapter().passkeyAuthenticationOptions(username);

export const passkeyAuthenticationVerify = (ceremonyId: string, credential: unknown): Promise<AuthSession> =>
  activeAdapter().passkeyAuthenticationVerify(ceremonyId, credential);

export const passkeyRegistrationOptions = (): Promise<WebAuthnCeremony> =>
  activeAdapter().passkeyRegistrationOptions();

export const passkeyRegistrationVerify = (
  ceremonyId: string,
  name: string,
  credential: unknown,
): Promise<Passkey> =>
  activeAdapter().passkeyRegistrationVerify(ceremonyId, name, credential);

export const listPasskeys = (): Promise<Passkey[]> =>
  activeAdapter().listPasskeys();

export const deletePasskey = (id: string): Promise<void> =>
  activeAdapter().deletePasskey(id);

// ==================== 账户 ====================
export const listAccounts = (): Promise<Account[]> =>
  activeAdapter().listAccounts();

export const createAccount = (input: AccountCreateInput): Promise<Account> =>
  activeAdapter().createAccount(input);

export const updateAccount = (id: string, input: AccountUpdateInput): Promise<Account> =>
  activeAdapter().updateAccount(id, input);

export const listAccountGrants = (accountId: string): Promise<AccountGrant[]> =>
  activeAdapter().listAccountGrants(accountId);

export const setAccountGrant = (
  accountId: string,
  memberId: string,
  permission: "view" | "manage",
): Promise<AccountGrant> =>
  activeAdapter().setAccountGrant(accountId, memberId, permission);

export const deleteAccountGrant = (accountId: string, memberId: string): Promise<void> =>
  activeAdapter().deleteAccountGrant(accountId, memberId);

// ==================== 分类 ====================
export const listCategories = (opts?: { includeInactive?: boolean }): Promise<Category[]> =>
  activeAdapter().listCategories(opts);

export const createCategory = (input: CategoryCreateInput): Promise<Category> =>
  activeAdapter().createCategory(input);

export const updateCategory = (id: string, input: CategoryUpdateInput): Promise<Category> =>
  activeAdapter().updateCategory(id, input);

// ==================== 交易 ====================
export const listTransactions = (filter: TransactionFilter = {}): Promise<Transaction[]> =>
  activeAdapter().listTransactions(filter);

export const getTransaction = (id: string): Promise<Transaction> =>
  activeAdapter().getTransaction(id);

export const createTransaction = (input: NewTransactionInput): Promise<Transaction> =>
  activeAdapter().createTransaction(input);

export const updateTransaction = (id: string, input: TransactionUpdateInput): Promise<Transaction> =>
  activeAdapter().updateTransaction(id, input);

export const deleteTransaction = (id: string, reason: string): Promise<void> =>
  activeAdapter().deleteTransaction(id, reason);

export const listRecycledTransactions = (): Promise<Transaction[]> =>
  activeAdapter().listRecycledTransactions();

export const restoreTransaction = (id: string, expectedVersion: number): Promise<Transaction> =>
  activeAdapter().restoreTransaction(id, expectedVersion);

// ==================== 导入 ====================
export const uploadImport = (
  files: File[],
  source: string,
  targetAccountId?: string | null,
): Promise<ImportBatch> =>
  activeAdapter().uploadImport(files, source, targetAccountId);

export const getImportStatus = (batchId: string): Promise<ImportJob> =>
  activeAdapter().getImportStatus(batchId);

export const listImportBatches = (): Promise<ImportBatch[]> =>
  activeAdapter().listImportBatches();

export const getImportBatch = (id: string): Promise<ImportBatch> =>
  activeAdapter().getImportBatch(id);

export const patchImportCandidate = (
  batchId: string,
  candidateId: string,
  patch: ImportCandidatePatch,
): Promise<ImportCandidate> =>
  activeAdapter().patchImportCandidate(batchId, candidateId, patch);

export const confirmImportCandidates = (batchId: string, candidateIds: string[]): Promise<ImportBatch> =>
  activeAdapter().confirmImportCandidates(batchId, candidateIds);

export const skipDuplicateCandidates = (batchId: string, candidateIds: string[]): Promise<ImportBatch> =>
  activeAdapter().skipDuplicateCandidates(batchId, candidateIds);

export const undoImportBatch = (batchId: string): Promise<ImportBatch> =>
  activeAdapter().undoImportBatch(batchId);

// ==================== 分摊 ====================
export const listSplits = (): Promise<Split[]> =>
  activeAdapter().listSplits();

export const createSplit = (input: NewSplitInput): Promise<Split> =>
  activeAdapter().createSplit(input);

export const respondToSplit = (splitId: string, input: SplitResponseInput): Promise<Split> =>
  activeAdapter().respondToSplit(splitId, input);

export const updateSplitShare = (
  splitId: string,
  memberId: string,
  input: SplitShareUpdateInput,
): Promise<Split> =>
  activeAdapter().updateSplitShare(splitId, memberId, input);

// ==================== 成员 / 备份 / 锁账 / 报表 ====================
export const listMembers = (): Promise<Member[]> =>
  activeAdapter().listMembers();

export const listBackups = (): Promise<BackupRecord[]> =>
  activeAdapter().listBackups();

export const createBackup = (): Promise<BackupRecord> =>
  activeAdapter().createBackup();

export const verifyBackup = (id: string): Promise<BackupRecord> =>
  activeAdapter().verifyBackup(id);

export const downloadBackup = (id: string): Promise<Blob> =>
  activeAdapter().downloadBackup(id);

export const listPeriodLocks = (): Promise<PeriodLock[]> =>
  activeAdapter().listPeriodLocks();

export const createPeriodLock = (input: PeriodLockInput): Promise<PeriodLock> =>
  activeAdapter().createPeriodLock(input);

export const deletePeriodLock = (id: string, reason: string): Promise<void> =>
  activeAdapter().deletePeriodLock(id, reason);

export const listReportTiers = (): Promise<ReportTierMeta[]> =>
  activeAdapter().listReportTiers();

export const exportReport = (tier: ReportTier, range: ReportRange): Promise<Blob> =>
  activeAdapter().exportReport(tier, range);

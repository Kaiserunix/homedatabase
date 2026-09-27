/**
 * 领域模型：API 相关类型一律派生自 openapi-typescript 生成的 schema
 * （@/api/schema.gen，源自仓库根 openapi.yaml），前端不再手写重复定义。
 * 金额字段（*Cents）均为货币最小单位的整数。
 */
import type { components } from "@/api/schema.gen";

type S = components["schemas"];

// ---------- 输出模型 ----------
export type Member = S["MemberOut"];
export type Account = S["AccountOut"];
export type Category = S["CategoryOut"];
export type Transaction = S["TransactionOut"];
export type EntryLine = S["EntryLineOut"];
export type ImportCandidate = S["ImportCandidateOut"];
export type ImportBatch = S["ImportBatchOut"];
export type ImportJob = S["ImportJobOut"];
export type SourceFile = S["SourceFileOut"];
export type Split = S["SplitOut"];
export type SplitShare = S["SplitShareOut"];
export type BackupRecord = S["BackupRecordOut"];
export type Passkey = S["PasskeyOut"];
export type PeriodLock = S["PeriodLockOut"];
export type ReportTierMeta = S["ReportTierOut"];
export type AccountGrant = S["AccountGrantOut"];

// ---------- 输入模型（写操作负载） ----------
export type AccountCreateInput = S["AccountCreateInput"];
export type AccountUpdateInput = S["AccountUpdateInput"];
export type CategoryCreateInput = S["CategoryCreateInput"];
export type CategoryUpdateInput = S["CategoryUpdateInput"];
export type NewTransactionInput = S["NewTransactionInput"];
export type TransactionUpdateInput = S["TransactionUpdateInput"];
export type ImportCandidatePatch = S["ImportCandidatePatch"];
export type NewSplitInput = S["NewSplitInput"];
export type SplitResponseInput = S["SplitResponseInput"];
export type SplitShareUpdateInput = S["SplitShareUpdateInput"];
export type PeriodLockInput = S["PeriodLockInput"];

// ---------- 会话 / WebAuthn ----------
export type AuthSession = S["AuthOut"];
export type WebAuthnCeremony = S["WebAuthnCeremonyOut"];

// ---------- 便利联合类型（派生自上面的模型，保持页面既有导入名不变） ----------
export type TxnType = Transaction["type"];
export type ReportTier = ReportTierMeta["tier"];
export type BatchStatus = ImportBatch["status"];
export type CandidateStatus = ImportCandidate["status"];
export type SplitStatus = Split["status"];
export type SplitShareStatus = SplitShare["status"];
export type AccountKind = Account["kind"];
export type AccountScope = Account["scope"];
export type AccountPermission = Account["permission"];
export type MemberIdentity = Member["identity"];
export type CategoryKind = Category["kind"];
export type BackupStatus = BackupRecord["status"];
export type BackupLocationKind = BackupRecord["locations"][number]["kind"];
export type BackupLocationStatus = BackupRecord["locations"][number]["status"];
export type ImportJobStatus = ImportJob["status"];
export type SourceFileParseStatus = SourceFile["parseStatus"];

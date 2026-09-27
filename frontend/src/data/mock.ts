import type {
  Account, BackupRecord, Category, ImportBatch, ImportCandidate, Member, Passkey,
  PeriodLock, ReportTierMeta, SourceFile, Split, Transaction,
} from "./types";

/**
 * 开发用 mock 数据：仅在 VITE_USE_MOCKS=true 时被 resources.ts 使用，另供单元测试。
 * 全部 fixture 与后端 schema（@/api/schema.gen）同形；金额一律为分（Cents）。
 * 账户余额与旧原型一致，e2e 里引用的总资产 ¥75,298.73 等数字保持成立。
 */

// ================= 当前登录者（mock 固定为爸爸） =================
export const CURRENT_USER_ID = "m-dad";

// ================= 成员：固定三人，身份与管理员权限分开 =================
export const members: Member[] = [
  { id: "m-dad", name: "爸爸", identity: "dad", username: "dad", isAdmin: true, color: "bg-teal-600" },
  { id: "m-mom", name: "妈妈", identity: "mom", username: "mom", isAdmin: false, color: "bg-indigo-500" },
  { id: "m-son", name: "儿子", identity: "son", username: "son", isAdmin: false, color: "bg-amber-500" },
];

export const memberName = (id: string) => members.find((m) => m.id === id)?.name ?? id;

// ================= 账户（余额沿用旧原型，总资产口径不变） =================
export const accounts: Account[] = [
  { id: "a-cmb", name: "招商银行储蓄卡", kind: "asset", scope: "personal", ownerId: "m-dad", permission: "manage", currency: "CNY", balanceCents: 52_340_18, icon: "landmark", version: 1 },
  { id: "a-alipay", name: "支付宝余额", kind: "asset", scope: "personal", ownerId: "m-dad", permission: "manage", currency: "CNY", balanceCents: 3_208_55, icon: "wallet", version: 1 },
  { id: "a-cash", name: "现金", kind: "asset", scope: "personal", ownerId: "m-mom", permission: "manage", currency: "CNY", balanceCents: 1_150_00, icon: "banknote", version: 1 },
  { id: "a-usd", name: "美元账户", kind: "asset", scope: "personal", ownerId: "m-dad", permission: "manage", currency: "USD", balanceCents: 210_000, icon: "dollar-sign", version: 1 },
  { id: "a-family", name: "家庭公共账户", kind: "asset", scope: "family", ownerId: "m-dad", permission: "manage", currency: "CNY", balanceCents: 18_600_00, icon: "users", version: 1 },
  { id: "a-cc", name: "信用卡（招行）", kind: "liability", scope: "personal", ownerId: "m-dad", permission: "manage", currency: "CNY", balanceCents: 4_372_66, icon: "credit-card", version: 1 },
  { id: "a-mortgage", name: "房贷", kind: "liability", scope: "family", ownerId: "m-dad", permission: "view", currency: "CNY", balanceCents: 860_000_00, icon: "home", version: 1 },
];

// ================= 分类（两级：parentId 指向父分类；active=false 已停用） =================
export const categories: Category[] = [
  { id: "c-food", name: "餐饮", kind: "expense", icon: "utensils", parentId: null, active: true, version: 1 },
  { id: "c-food-takeout", name: "外卖", kind: "expense", icon: "bike", parentId: "c-food", active: true, version: 1 },
  { id: "c-grocery", name: "买菜超市", kind: "expense", icon: "shopping-cart", parentId: null, active: true, version: 1 },
  { id: "c-transport", name: "交通", kind: "expense", icon: "train-front", parentId: null, active: true, version: 1 },
  { id: "c-transport-taxi", name: "打车", kind: "expense", icon: "car-taxi-front", parentId: "c-transport", active: true, version: 1 },
  { id: "c-home", name: "居家", kind: "expense", icon: "sofa", parentId: null, active: true, version: 1 },
  { id: "c-medical", name: "医疗", kind: "expense", icon: "heart-pulse", parentId: null, active: true, version: 1 },
  { id: "c-fun", name: "娱乐", kind: "expense", icon: "gamepad-2", parentId: null, active: true, version: 1 },
  { id: "c-kids", name: "孩子", kind: "expense", icon: "baby", parentId: null, active: true, version: 1 },
  { id: "c-snack", name: "零食饮料", kind: "expense", icon: "cookie", parentId: "c-food", active: false, version: 2 },
  { id: "c-other", name: "其他", kind: "expense", icon: "shapes", parentId: null, active: true, version: 1 },
  { id: "c-salary", name: "工资", kind: "income", icon: "briefcase", parentId: null, active: true, version: 1 },
  { id: "c-bonus", name: "奖金", kind: "income", icon: "gift", parentId: null, active: true, version: 1 },
  { id: "c-interest", name: "理财收益", kind: "income", icon: "trending-up", parentId: null, active: true, version: 1 },
  { id: "c-other-in", name: "其他收入", kind: "income", icon: "shapes", parentId: null, active: true, version: 1 },
];

export const categoryName = (id: string | null) =>
  categories.find((c) => c.id === id)?.name ?? "未分类";
export const accountName = (id: string) =>
  accounts.find((a) => a.id === id)?.name ?? id;

// ================= 交易（含复式分录；金额为分，外币带原币分录金额） =================
/**
 * mock 交易时间：以上海挂钟书写，落盘为后端真实形态 UTC Z。
 * 页面/测试一律经 lib/ledger-date 按 Asia/Shanghai 解释，禁止 slice/startsWith。
 */
const d = (day: number, time = "12:30", month = "07") =>
  new Date(`2026-${month}-${String(day).padStart(2, "0")}T${time}:00+08:00`).toISOString();

type Line = Transaction["lines"][number];

/**
 * 账户分录：与后端 presenter 一致——debit/credit 为基础币（CNY）金额，
 * accountAmountCents/accountCurrency 记账户币实际变动（CNY 账户即等于基础币金额）。
 */
const accountLine = (
  accountId: string,
  accountCurrency: string,
  debitCents: number,
  creditCents: number,
  accountAmountCents: number,
): Line => ({ accountId, ledgerCode: null, debitCents, creditCents, accountAmountCents, accountCurrency });

/** 损益分录：accountId=null + ledgerCode（expense:/income: 前缀；不伪装成账户，不携带账户币金额） */
const ledgerLine = (ledgerCode: string, debitCents: number, creditCents: number): Line => ({
  accountId: null, ledgerCode, debitCents, creditCents, accountAmountCents: null, accountCurrency: null,
});

export const transactions: Transaction[] = [
  {
    id: "t-001", date: d(28, "09:12"), type: "income", payee: "某公司", note: "7月工资",
    categoryId: "c-salary", currency: "CNY", amountCents: 24_000_00, baseAmountCents: 24_000_00, baseCurrency: "CNY",
    source: "manual", createdBy: "m-dad", version: 1,
    lines: [
      accountLine("a-cmb", "CNY", 24_000_00, 0, 24_000_00),
      ledgerLine("income:c-salary", 0, 24_000_00),
    ],
  },
  {
    id: "t-002", date: d(29, "12:05"), type: "expense", payee: "盒马鲜生", note: "周末采购",
    categoryId: "c-grocery", currency: "CNY", amountCents: 326_40, baseAmountCents: 326_40, baseCurrency: "CNY",
    source: "import", batchId: "b-002", createdBy: "m-dad", version: 1,
    lines: [
      ledgerLine("expense:c-grocery", 326_40, 0),
      accountLine("a-alipay", "CNY", 0, 326_40, 326_40),
    ],
  },
  {
    id: "t-003", date: d(29, "19:40"), type: "expense", payee: "海底捞", note: "家庭聚餐（待分摊）",
    categoryId: "c-food", currency: "CNY", amountCents: 468_00, baseAmountCents: 468_00, baseCurrency: "CNY",
    source: "manual", splitId: "s-001", createdBy: "m-dad", version: 1,
    lines: [
      ledgerLine("expense:c-food", 468_00, 0),
      accountLine("a-cmb", "CNY", 0, 468_00, 468_00),
    ],
  },
  {
    id: "t-004", date: d(30, "08:15"), type: "expense", payee: "滴滴出行", note: "上班打车",
    categoryId: "c-transport-taxi", currency: "CNY", amountCents: 38_50, baseAmountCents: 38_50, baseCurrency: "CNY",
    source: "import", batchId: "b-002", createdBy: "m-dad", version: 1,
    lines: [
      ledgerLine("expense:c-transport-taxi", 38_50, 0),
      accountLine("a-alipay", "CNY", 0, 38_50, 38_50),
    ],
  },
  {
    id: "t-005", date: d(30, "10:00"), type: "transfer", payee: "转入家庭公共账户", note: "7月家用",
    categoryId: null, currency: "CNY", amountCents: 3_000_00, baseAmountCents: 3_000_00, baseCurrency: "CNY",
    source: "manual", createdBy: "m-mom", version: 1,
    lines: [
      accountLine("a-family", "CNY", 3_000_00, 0, 3_000_00),
      accountLine("a-cmb", "CNY", 0, 3_000_00, 3_000_00),
    ],
  },
  {
    id: "t-006", date: d(31, "15:22"), type: "expense", payee: "京东", note: "空气净化器滤芯",
    categoryId: "c-home", currency: "CNY", amountCents: 259_00, baseAmountCents: 259_00, baseCurrency: "CNY",
    source: "import", batchId: "b-002", createdBy: "m-dad", version: 1,
    lines: [
      ledgerLine("expense:c-home", 259_00, 0),
      accountLine("a-cc", "CNY", 0, 259_00, 259_00),
    ],
  },
  {
    id: "t-007", date: d(31, "16:03"), type: "refund", payee: "京东", note: "滤芯买错型号，退款",
    categoryId: "c-home", currency: "CNY", amountCents: 259_00, baseAmountCents: 259_00, baseCurrency: "CNY",
    source: "manual", refundOf: "t-006", createdBy: "m-dad", version: 1,
    lines: [
      accountLine("a-cc", "CNY", 259_00, 0, 259_00),
      ledgerLine("expense:c-home", 0, 259_00),
    ],
  },
  {
    // 外币交易：主币种 USD；借贷为基础币（CNY）金额，账户分录另记原币（USD）实际变动
    id: "t-008", date: d(1, "11:20"), type: "expense", payee: "Apple Store", note: "充电器（美元卡）",
    categoryId: "c-other", currency: "USD", fxRate: "7.16", amountCents: 29_00, baseAmountCents: 207_64, baseCurrency: "CNY",
    source: "manual", splitId: "s-003", createdBy: "m-dad", version: 1,
    lines: [
      ledgerLine("expense:c-other", 207_64, 0),
      accountLine("a-usd", "USD", 0, 207_64, 29_00),
    ],
  },
  {
    id: "t-009", date: d(2, "07:50"), type: "expense", payee: "地铁", note: "通勤",
    categoryId: "c-transport", currency: "CNY", amountCents: 6_00, baseAmountCents: 6_00, baseCurrency: "CNY",
    source: "import", batchId: "b-002", createdBy: "m-mom", version: 1,
    lines: [
      ledgerLine("expense:c-transport", 6_00, 0),
      accountLine("a-alipay", "CNY", 0, 6_00, 6_00),
    ],
  },
  {
    id: "t-010", date: d(2, "20:10"), type: "expense", payee: "永辉超市", note: "日用品",
    categoryId: "c-grocery", currency: "CNY", amountCents: 142_80, baseAmountCents: 142_80, baseCurrency: "CNY",
    source: "manual", splitId: "s-002", createdBy: "m-mom", version: 1,
    lines: [
      ledgerLine("expense:c-grocery", 142_80, 0),
      accountLine("a-family", "CNY", 0, 142_80, 142_80),
    ],
  },
  {
    // b-004 混合批次中已入账的候选（6 月，不影响 7/8 月账期统计）
    id: "t-011", date: d(15, "12:30", "06"), type: "expense", payee: "顺丰速运", note: "寄文件",
    categoryId: "c-other", currency: "CNY", amountCents: 23_00, baseAmountCents: 23_00, baseCurrency: "CNY",
    source: "import", batchId: "b-004", createdBy: "m-dad", version: 1,
    lines: [
      ledgerLine("expense:c-other", 23_00, 0),
      accountLine("a-alipay", "CNY", 0, 23_00, 23_00),
    ],
  },
];

// ================= 导入批次 =================
/** 候选快捷构造：mock 候选的公共默认字段 */
const candidate = (
  id: string, date: string, payee: string, amountCents: number,
  suggestedCategoryId: string | null, overrides?: Partial<ImportCandidate>,
): ImportCandidate => ({
  id, date, payee, amountCents, suggestedCategoryId,
  direction: "expense", currency: "CNY", status: "pending",
  duplicateOfTxnId: null, originalCategory: null, targetAccountId: "a-alipay",
  validationStatus: "valid", confidence: "high", version: 1,
  ...overrides,
});

const sourceFile = (
  id: string, filename: string, sizeBytes: number, recordCount: number, candidateCount: number,
  overrides?: Partial<SourceFile>,
): SourceFile => ({
  id, filename, sizeBytes, recordCount, candidateCount,
  mimeType: "text/csv", sha256: `mock-sha256-${id}`, parseStatus: "parsed",
  parseMessage: "", rejectedCount: 0, duplicateOfFileId: null,
  encrypted: true, expiresAt: null,
  ...overrides,
});

export const importBatches: ImportBatch[] = [
  {
    id: "b-003",
    filename: "微信支付账单_2026-07.csv",
    source: "微信支付",
    importedAt: "2026-08-02T13:14:00Z",
    status: "reviewing",
    ownerId: "m-dad",
    targetAccountId: "a-alipay",
    jobId: "job-b-003",
    progress: 100,
    message: "解析完成，待确认 7 条候选（2 条疑似重复，1 条待补汇率）",
    fileCount: 1,
    filesProcessed: 1,
    candidateCount: 7,
    needsOcrCount: 0,
    failedFileCount: 0,
    version: 1,
    files: [sourceFile("f-b003-1", "微信支付账单_2026-07.csv", 18_420, 6, 6)],
    candidates: [
      candidate("cd-1", d(25, "12:30"), "麦当劳", 45_50, "c-food", { originalCategory: "餐饮美食" }),
      candidate("cd-2", d(26, "09:05"), "地铁", 6_00, "c-transport", { originalCategory: "交通出行" }),
      candidate("cd-3", d(29, "12:05"), "盒马鲜生", 326_40, "c-grocery", { duplicateOfTxnId: "t-002", originalCategory: "商超购物", validationStatus: "exact_duplicate" }),
      candidate("cd-4", d(30, "08:15"), "滴滴出行", 38_50, "c-transport-taxi", { duplicateOfTxnId: "t-004", originalCategory: "交通出行", validationStatus: "exact_duplicate" }),
      candidate("cd-5", d(31, "13:45"), "瑞幸咖啡", 19_90, "c-food", { originalCategory: "餐饮美食", confidence: "medium" }),
      candidate("cd-6", d(1, "18:00"), "同事转账", 120_00, "c-other-in", { direction: "income", originalCategory: "转账", confidence: "low" }),
      // 外币候选：后端无法确定汇率，需人工补 fxRate 与 baseAmountCents 后才可确认
      candidate("cd-7", d(1, "21:40"), "Amazon US", 15_99, "c-other", {
        currency: "USD", originalCategory: "Shopping", confidence: "medium",
        validationStatus: "needs_fx_review", fxRate: null, baseAmountCents: null,
      }),
    ],
  },
  {
    // 真实混合批次：已确认并入账 1 条（t-011），另有 1 个文件仍需 OCR——后端允许整批撤销
    id: "b-004",
    filename: "支付宝账单_2026-06.csv + 小票照片.jpg",
    source: "支付宝",
    importedAt: "2026-07-30T02:20:00Z",
    status: "needs_ocr",
    ownerId: "m-dad",
    targetAccountId: "a-alipay",
    jobId: "job-b-004",
    progress: 100,
    message: "已入账 1 条；1 个文件需要 OCR/人工识别",
    fileCount: 2,
    filesProcessed: 1,
    candidateCount: 1,
    needsOcrCount: 1,
    failedFileCount: 0,
    version: 2,
    files: [
      sourceFile("f-b004-1", "支付宝账单_2026-06.csv", 8_104, 1, 1),
      sourceFile("f-b004-2", "小票照片.jpg", 204_310, 0, 0, {
        mimeType: "image/jpeg", parseStatus: "needs_ocr", parseMessage: "图片账单需人工识别",
      }),
    ],
    candidates: [
      candidate("cd-e", d(15, "12:30", "06"), "顺丰速运", 23_00, "c-other", { status: "confirmed", version: 2 }),
    ],
  },
  {
    id: "b-002",
    filename: "支付宝账单_2026-07.csv",
    source: "支付宝",
    importedAt: "2026-07-28T02:02:00Z",
    status: "posted",
    ownerId: "m-dad",
    targetAccountId: "a-alipay",
    jobId: "job-b-002",
    progress: 100,
    message: "已入账 4 条",
    fileCount: 1,
    filesProcessed: 1,
    candidateCount: 4,
    needsOcrCount: 0,
    failedFileCount: 0,
    version: 2,
    files: [sourceFile("f-b002-1", "支付宝账单_2026-07.csv", 12_108, 4, 4)],
    candidates: [
      candidate("cd-a", d(29, "12:05"), "盒马鲜生", 326_40, "c-grocery", { status: "confirmed", version: 2 }),
      candidate("cd-b", d(30, "08:15"), "滴滴出行", 38_50, "c-transport-taxi", { status: "confirmed", version: 2 }),
      candidate("cd-c", d(31, "15:22"), "京东", 259_00, "c-home", { status: "confirmed", version: 2 }),
      candidate("cd-d", d(2, "07:50"), "地铁", 6_00, "c-transport", { status: "confirmed", version: 2 }),
    ],
  },
  {
    // 纯 OCR 批次：无任何候选、无已入账交易——后端不允许撤销（409）
    id: "b-005",
    filename: "纸质小票照片.zip",
    source: "拍照导入",
    importedAt: "2026-07-05T01:00:00Z",
    status: "needs_ocr",
    ownerId: "m-dad",
    targetAccountId: null,
    jobId: "job-b-005",
    progress: 100,
    message: "2 个文件需要 OCR/人工识别，未产生候选",
    fileCount: 2,
    filesProcessed: 0,
    candidateCount: 0,
    needsOcrCount: 2,
    failedFileCount: 0,
    version: 1,
    files: [
      sourceFile("f-b005-1", "小票1.jpg", 188_204, 0, 0, {
        mimeType: "image/jpeg", parseStatus: "needs_ocr", parseMessage: "图片账单需人工识别",
      }),
      sourceFile("f-b005-2", "小票2.jpg", 201_558, 0, 0, {
        mimeType: "image/jpeg", parseStatus: "needs_ocr", parseMessage: "图片账单需人工识别",
      }),
    ],
    candidates: [],
  },
  {
    id: "b-001",
    filename: "招行信用卡_2026-06.xls",
    source: "招商银行",
    importedAt: "2026-07-03T01:00:00Z",
    status: "undone",
    ownerId: "m-dad",
    targetAccountId: "a-cc",
    jobId: "job-b-001",
    progress: 100,
    message: "整批已撤销",
    fileCount: 1,
    filesProcessed: 1,
    candidateCount: 0,
    needsOcrCount: 0,
    failedFileCount: 0,
    version: 3,
    files: [sourceFile("f-b001-1", "招行信用卡_2026-06.xls", 45_056, 5, 0, { mimeType: "application/vnd.ms-excel" })],
    candidates: [],
  },
];

// ================= 分摊（金额单位为原交易币，currency 与关联交易一致） =================
export const splits: Split[] = [
  {
    id: "s-001", txnId: "t-003", title: "海底捞", totalCents: 468_00, currency: "CNY",
    categoryId: "c-food", transactionDate: d(29, "19:40"),
    createdBy: "m-dad", createdAt: d(29, "20:05"), status: "open", version: 1,
    shares: [
      { memberId: "m-dad", shareCents: 156_00, status: "accepted" },
      { memberId: "m-mom", shareCents: 156_00, status: "pending" },
      { memberId: "m-son", shareCents: 156_00, status: "returned", note: "我不吃火锅，把我去掉吧" },
    ],
  },
  {
    id: "s-002", txnId: "t-010", title: "永辉超市", totalCents: 142_80, currency: "CNY",
    categoryId: "c-grocery", transactionDate: d(2, "20:10"),
    createdBy: "m-mom", createdAt: d(2, "21:00"), status: "open", version: 2,
    shares: [
      { memberId: "m-mom", shareCents: 71_40, status: "accepted" },
      { memberId: "m-dad", shareCents: 71_40, status: "pending" },
    ],
  },
  {
    // 外币分摊：总额与份额均为原交易币（USD），不做折算；标题与后端一致派生自 payee
    id: "s-003", txnId: "t-008", title: "Apple Store", totalCents: 29_00, currency: "USD",
    categoryId: "c-other", transactionDate: d(1, "11:20"),
    createdBy: "m-dad", createdAt: d(1, "12:00"), status: "open", version: 1,
    shares: [
      { memberId: "m-dad", shareCents: 14_50, status: "accepted" },
      { memberId: "m-mom", shareCents: 14_50, status: "pending" },
    ],
  },
];

// ================= 备份 =================
export const backups: BackupRecord[] = [
  {
    id: "bk-0731", createdAt: "2026-07-31T19:00:00Z", sizeMb: 42.6, encrypted: true,
    sha256: "9f2c4a71e0b3d5f688a1c2e4b7d9f0134567890abcdef0123456789abcdef01",
    locations: [
      { kind: "tencent", status: "ok" },
      { kind: "local", status: "ok" },
      { kind: "overseas", status: "ok" },
    ],
    lastVerifiedAt: "2026-07-15T06:00:00Z",
    status: "ready",
  },
  {
    id: "bk-0724", createdAt: "2026-07-24T19:00:00Z", sizeMb: 41.9, encrypted: true,
    sha256: "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809",
    locations: [
      { kind: "tencent", status: "ok" },
      { kind: "local", status: "ok" },
      { kind: "overseas", status: "syncing" },
    ],
    lastVerifiedAt: "2026-07-01T06:00:00Z",
    status: "ready",
  },
];

// ================= 安全 =================
export const passkeys: Passkey[] = [
  { id: "pk-1", name: "iPhone（面容 ID）", createdAt: "2026-05-02T02:00:00Z", lastUsedAt: "2026-08-02T13:10:00Z" },
  { id: "pk-2", name: "MacBook（触控 ID）", createdAt: "2026-05-02T02:20:00Z", lastUsedAt: "2026-08-01T01:32:00Z" },
];

// ================= 周期锁账 =================
export const periodLocks: PeriodLock[] = [
  {
    id: "pl-2026-06", startsOn: "2026-06-01", endsOn: "2026-06-30",
    reason: "月度核对完成", lockedBy: "m-dad", createdAt: "2026-07-02T12:00:00Z",
  },
];

// ================= 报表五层级 =================
export const reportTiers: ReportTierMeta[] = [
  { tier: 1, name: "阅读账单", audience: "给家人看", description: "像银行账单一样按月排版，只讲花了什么、剩了多少。" },
  { tier: 2, name: "交易明细", audience: "日常核对", description: "逐笔列出日期、商户、分类、账户、金额与备注。" },
  { tier: 3, name: "复式分录", audience: "懂记账的人", description: "每笔交易的借贷分录全量导出，借贷必平衡。" },
  { tier: 4, name: "核对汇总", audience: "对账", description: "按账户汇总期初、流入、流出、期末，并与明细勾稽。" },
  { tier: 5, name: "统计分析", audience: "复盘决策", description: "按分类/成员/月份的多维透视与趋势，供分析用。" },
];

// ================= 派生统计（mock 用近似值即可） =================
export const stats = {
  monthExpenseCents: 1_283_70,
  monthIncomeCents: 24_120_00,
  totalAssetCents: 75_298_73, // 不含外币折算差异，与账户余额口径一致
  // 负债为正数债务余额（后端口径：负债增加为 credit、减少为 debit）；净资产 = 资产 - 负债
  totalLiabilityCents: 864_372_66,
};

import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Pencil, Plus, Search, Trash2, Undo2 } from "lucide-react";
import { AdminHeader } from "../components/AdminHeader";
import { Amount, Badge, Button, Card, EmptyState } from "@/components/ui";
import { Dialog, DialogContent, DialogHeader } from "@/components/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { TxnDetailContent } from "@/components/TxnDetailContent";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { fmtDate, fmtMoney } from "@/lib/utils";
import { accountOptionSuffix, canWriteAccount } from "@/lib/accounts";
import { useApiQuery } from "@/api/useApi";
import {
  createTransaction, deleteTransaction, getTransaction, listAccounts, listCategories,
  listImportBatches, listMembers, listRecycledTransactions, listSplits, listTransactions,
  restoreTransaction, updateTransaction, type NewTransactionInput,
} from "@/api/resources";
import { ApiError } from "@/api/client";
import { validateTransaction, type TxnError, type TxnField } from "@/lib/validate-txn";
import { parseDecimalCents, parsePositiveCents } from "@/lib/decimal";
import type { Transaction } from "@/data/types";

const typeMeta: Record<Transaction["type"], { label: string; tone: "neutral" | "accent" | "warn" }> = {
  expense: { label: "支出", tone: "neutral" },
  income: { label: "收入", tone: "accent" },
  transfer: { label: "转账", tone: "neutral" },
  refund: { label: "退款", tone: "warn" },
};

const inputCls = "glass h-10 w-full rounded-xl px-3 text-sm text-ink outline-none placeholder:text-ink-faint";

/** 409 版本冲突提示：数据已被他人修改，强制刷新后请用户重新核对 */
const CONFLICT_COPY = "该记录刚被其他人修改，已为你加载最新内容，请重新核对后再操作。";

const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : "操作失败，请稍后重试");

function Field({ label, error, children }: { label: string; error?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-ink-faint">{label}</span>
      {children}
      {error && <span className="mt-1 block text-xs text-danger">{error}</span>}
    </label>
  );
}

/** 表格骨架：与下方交易表格列位大致对齐 */
function TxnTableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-3 py-1" aria-busy="true" aria-label="加载中">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-3.5 w-20 shrink-0" />
          <Skeleton className="h-5 w-12 shrink-0 rounded-full" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <Skeleton className="h-3.5 w-40" />
            <Skeleton className="h-3 w-56" />
          </div>
          <Skeleton className="h-3.5 w-16 shrink-0" />
          <Skeleton className="h-4 w-20 shrink-0" />
          <Skeleton className="h-5 w-12 shrink-0 rounded-full" />
        </div>
      ))}
    </div>
  );
}

export default function TxnsPage() {
  const [tab, setTab] = useState("all");
  const [accountFilter, setAccountFilter] = useState("all");
  const [keyword, setKeyword] = useState("");
  const [detail, setDetail] = useState<Transaction | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState({ type: "expense", payee: "", amount: "", baseAmount: "", categoryId: "none", accountId: "", toAccountId: "", refundOf: "", note: "", currency: "CNY" });
  const [formErrors, setFormErrors] = useState<TxnError[]>([]);
  // 外币账户必填的「折合人民币金额（CNY）」字段级报错（lib/validate-txn 由他人维护，不扩展其字段）
  const [baseAmountError, setBaseAmountError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // 基础数据全部来自 API；筛选仍在前端做（保持现有行为）
  const { data, loading, error, retry } = useApiQuery(
    async () => {
      const [txns, accts, cats, batches, memberList, splitList] = await Promise.all([
        listTransactions(),
        listAccounts(),
        listCategories(),
        listImportBatches(),
        listMembers(),
        listSplits(),
      ]);
      return { txns, accts, cats, batches, memberList, splitList };
    },
    [],
  );
  // 回收站独立取数，只在切到该 Tab 时请求
  const {
    data: recycled, loading: recycledLoading, error: recycledError, retry: retryRecycled,
  } = useApiQuery(
    () => (tab === "recycle" ? listRecycledTransactions() : Promise.resolve([])),
    [tab],
  );

  const txns = data?.txns ?? [];
  const accts = data?.accts ?? [];
  const cats = data?.cats ?? [];
  const batches = data?.batches ?? [];

  const categoryName = (id: string | null) =>
    id === null ? "未分类" : (cats.find((c) => c.id === id)?.name ?? "未分类");

  // 关键词防抖 200ms 后再参与列表 key，避免每敲一个字母都重播进场动画
  const [kwKey, setKwKey] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setKwKey(keyword), 200);
    return () => window.clearTimeout(t);
  }, [keyword]);

  const filtered = useMemo(
    () =>
      [...txns]
        .sort((a, b) => b.date.localeCompare(a.date))
        .filter((t) => tab === "all" || t.type === tab)
        .filter((t) => accountFilter === "all" || t.lines.some((l) => l.accountId === accountFilter))
        .filter((t) => !keyword || t.payee.includes(keyword) || t.note.includes(keyword)),
    [tab, accountFilter, keyword, txns],
  );

  const formCats = cats.filter(
    (c) => c.active && c.kind === (form.type === "income" ? "income" : "expense"),
  );
  // 退款必须关联的原支出交易候选（按日期倒序，与列表一致）
  const refundCandidates = useMemo(
    () => [...txns].filter((t) => t.type === "expense").sort((a, b) => b.date.localeCompare(a.date)),
    [txns],
  );

  const formFieldError = (field: TxnField) => formErrors.find((e) => e.field === field)?.message;

  // 表单所选账户决定账户币种；交易币种按下方的 txnCurrency 口径推导
  const formAccount = accts.find((a) => a.id === form.accountId);
  // 退款的原支出：币种 / 分类 / 金额单位全部继承原支出
  const refundOriginal =
    form.type === "refund" ? (txns.find((t) => t.id === form.refundOf) ?? null) : null;
  // 交易币种推导（绝不产生「第三币种账户 + 其他币种」组合）：
  // 转账 = 转出账户币种（无选择器）；退款 = 原支出币种；
  // 支出/收入：非 CNY 账户锁定为账户币种，CNY 账户可选 CNY/USD/EUR/JPY（代付外币，默认 CNY）
  const txnCurrency =
    form.type === "refund"
      ? (refundOriginal?.currency ?? null)
      : formAccount
        ? formAccount.currency === "CNY" && form.type !== "transfer"
          ? form.currency
          : formAccount.currency
        : null;
  // 交易币种非 CNY 时必须另填折合基础币（CNY）金额
  const needsBaseAmount = txnCurrency !== null && txnCurrency !== "CNY";
  // 仅 CNY 账户的支出/收入出现交易币种选择器；其余情形币种锁定
  const showCurrencySelect =
    (form.type === "expense" || form.type === "income") && formAccount?.currency === "CNY";

  const switchFormType = (type: string) => {
    setForm((f) => ({ ...f, type, categoryId: "none", refundOf: "", currency: "CNY" }));
    setFormErrors([]);
    setBaseAmountError(null);
    setSubmitError(null);
  };

  const saveTxn = async () => {
    const type = form.type as NewTransactionInput["type"];
    const errs = validateTransaction({
      type,
      payee: form.payee,
      amount: form.amount,
      categoryId: type === "transfer" || form.categoryId === "none" ? null : form.categoryId,
      accountId: form.accountId,
      toAccountId: type === "transfer" ? form.toAccountId : undefined,
      refundOf: type === "refund" ? form.refundOf : undefined,
    });
    setFormErrors(errs);

    // 转账前置拦截（不发请求，与手机端一致）：同账户、转出/转入跨币种
    if (type === "transfer" && form.accountId && form.toAccountId) {
      if (form.accountId === form.toAccountId) {
        // 覆盖共享校验的同账户文案，与后端/mock 口径一致
        const sameErr: TxnError = { field: "toAccount", message: "转账必须选择不同的转入账户" };
        const idx = errs.findIndex((e) => e.field === "toAccount");
        if (idx >= 0) errs[idx] = sameErr;
        else errs.push(sameErr);
      } else {
        const fromCur = accts.find((a) => a.id === form.accountId)?.currency;
        const toCur = accts.find((a) => a.id === form.toAccountId)?.currency;
        if (fromCur && toCur && fromCur !== toCur) {
          errs.push({ field: "toAccount", message: "转出与转入账户币种必须相同" });
        }
      }
      setFormErrors(errs);
    }
    if (errs.length > 0) return;

    // 校验已通过，这里再精确解析一次兜底：无效/超界绝不发出请求
    const parsedAmount = parseDecimalCents(form.amount);
    if (!parsedAmount.ok || parsedAmount.cents <= 0) {
      setFormErrors([{ field: "amount", message: "请输入有效金额（大于 0，最多两位小数）" }]);
      return;
    }

    // 外币交易：折合人民币金额必填且精确解析，缺失/非法在字段级报错并阻止请求（不吃 422）
    let baseAmountCents: number | undefined;
    if (needsBaseAmount) {
      const parsedBase = parsePositiveCents(form.baseAmount);
      if (!parsedBase.ok) {
        setBaseAmountError(form.baseAmount.trim() ? parsedBase.error : "请填写折合人民币金额");
        return;
      }
      baseAmountCents = parsedBase.cents;
    }

    const input: NewTransactionInput = {
      type,
      payee: form.payee.trim(),
      note: form.note.trim(),
      // 退款不提交 categoryId，分类由后端继承原支出（提交反而可能被 422）
      ...(type !== "refund"
        ? { categoryId: type === "transfer" || form.categoryId === "none" ? null : form.categoryId }
        : {}),
      accountId: form.accountId,
      ...(type === "transfer" ? { toAccountId: form.toAccountId } : {}),
      // 退款必须携带原支出交易 ID，缺 refundOf 的请求不允许发出
      ...(type === "refund" ? { refundOf: form.refundOf } : {}),
      amountCents: parsedAmount.cents,
      // 交易币种：退款=原支出币种；转账/非 CNY 账户=账户币种；CNY 账户收支=所选币种
      currency: txnCurrency ?? "CNY",
      // CNY 交易不提交 baseAmountCents（后端按 1:1 入账）；fxRate 不传
      ...(baseAmountCents !== undefined ? { baseAmountCents } : {}),
      date: new Date().toISOString(),
    };
    setSubmitting(true);
    setSubmitError(null);
    try {
      await createTransaction(input);
      setAddOpen(false);
      setForm({ type: "expense", payee: "", amount: "", baseAmount: "", categoryId: "none", accountId: "", toAccountId: "", refundOf: "", note: "", currency: "CNY" });
      setBaseAmountError(null);
      retry();
    } catch (e) {
      setSubmitError(errMsg(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <AdminHeader
        title="账目与分录"
        desc="所有交易均为复式记账，借贷必平衡"
        action={
          <Button onClick={() => setAddOpen(true)}>
            <Plus className="h-4 w-4" />
            记一笔
          </Button>
        }
      />

      {/* 筛选行 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="all">全部</TabsTrigger>
            <TabsTrigger value="expense">支出</TabsTrigger>
            <TabsTrigger value="income">收入</TabsTrigger>
            <TabsTrigger value="transfer">转账</TabsTrigger>
            <TabsTrigger value="refund">退款</TabsTrigger>
            <TabsTrigger value="recycle">回收站</TabsTrigger>
          </TabsList>
        </Tabs>
        {tab !== "recycle" && (
          <>
            <Select value={accountFilter} onValueChange={setAccountFilter}>
              <SelectTrigger className="w-44" />
              <SelectContent>
                <SelectItem value="all">全部账户</SelectItem>
                {accts.map((a) => (
                  <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
              <input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="搜商户 / 备注"
                className="glass h-10 w-56 rounded-xl pl-9 pr-3 text-sm text-ink outline-none placeholder:text-ink-faint"
              />
            </div>
          </>
        )}
      </div>

      {tab === "recycle" ? (
        <RecycleBin
          rows={recycled ?? []}
          loading={recycledLoading}
          error={recycledError}
          retry={retryRecycled}
          categoryName={categoryName}
          onRestored={() => {
            retryRecycled();
            retry();
          }}
        />
      ) : (
        /* 交易表格：骨架 / 错误 / 表格互斥渲染 */
        <Card className="p-4">
          {loading ? (
            <TxnTableSkeleton />
          ) : error ? (
            <ErrorState title="交易加载失败" desc={error.message} onRetry={retry} />
          ) : filtered.length === 0 ? (
            <EmptyState title="没有符合条件的交易" hint="调整筛选条件试试" />
          ) : (
            <table className="w-full text-sm">
              <thead className="text-xs text-ink-faint">
                <tr className="border-b border-slate-900/10">
                  <th className="py-2 text-left font-medium">日期</th>
                  <th className="py-2 text-left font-medium">类型</th>
                  <th className="py-2 text-left font-medium">商户</th>
                  <th className="py-2 text-left font-medium">分类</th>
                  <th className="py-2 text-right font-medium">金额</th>
                  <th className="py-2 text-left font-medium">来源</th>
                  <th className="py-2 text-right font-medium">操作</th>
                </tr>
              </thead>
              {/* 筛选条件变化时以 key 重挂载 tbody，行逐项重新进场提示内容已更新 */}
              <tbody key={`${tab}-${accountFilter}-${kwKey}`} className="stagger">
                {filtered.map((t, ti) => {
                  const meta = typeMeta[t.type];
                  const batch = t.batchId ? batches.find((b) => b.id === t.batchId) : null;
                  const kind = t.type === "expense" ? "expense" : t.type === "income" ? "income" : "neutral";
                  return (
                    <tr
                      key={t.id}
                      className="border-b border-slate-900/5 last:border-0 hover:bg-white/60"
                      style={{ "--stagger-i": ti } as CSSProperties}
                    >
                      <td className="py-2.5 pr-2 text-ink-soft">{fmtDate(t.date)}</td>
                      <td className="py-2.5 pr-2"><Badge tone={meta.tone}>{meta.label}</Badge></td>
                      <td className="py-2.5 pr-2">
                        <span className="block text-ink">{t.payee}</span>
                        {t.note && <span className="block text-xs text-ink-faint">{t.note}</span>}
                      </td>
                      <td className="py-2.5 pr-2 text-ink-soft">
                        {t.type === "transfer" ? "—" : categoryName(t.categoryId)}
                      </td>
                      <td className="py-2.5 pr-2 text-right">
                        <Amount cents={t.baseAmountCents} currency={t.baseCurrency} kind={kind} />
                        {t.currency !== t.baseCurrency && (
                          <span className="block text-xs text-ink-faint">
                            原币 <Amount cents={t.amountCents} currency={t.currency} />
                            {t.fxRate != null && ` · 汇率 ${t.fxRate}`}
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 pr-2">
                        <Badge tone={t.source === "import" ? "accent" : "neutral"}>
                          {t.source === "import" ? "导入" : "手动"}
                        </Badge>
                        {batch && (
                          <Link
                            to={`/imports/${batch.id}`}
                            className="block max-w-40 truncate text-xs text-ink-faint hover:text-accent"
                          >
                            {batch.filename}
                          </Link>
                        )}
                      </td>
                      <td className="py-2.5 text-right">
                        <Button variant="ghost" size="sm" onClick={() => setDetail(t)}>
                          详情
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {/* 交易详情（含编辑 / 删除） */}
      <TxnDetailDialog
        txn={detail}
        onClose={() => setDetail(null)}
        data={data}
        categoryName={categoryName}
        onChanged={(next) => {
          retry();
          setDetail(next ?? null);
        }}
      />

      {/* 记一笔 */}
      <Dialog open={addOpen} onOpenChange={(o) => { setAddOpen(o); if (o) { setFormErrors([]); setBaseAmountError(null); setSubmitError(null); } }}>
        <DialogContent>
          <DialogHeader title="记一笔" desc="账面名称必填，分类可为空；金额按最小货币单位提交" />
          <div className="space-y-3">
            <Field label="类型">
              <Select value={form.type} onValueChange={switchFormType}>
                <SelectTrigger />
                <SelectContent>
                  <SelectItem value="expense">支出</SelectItem>
                  <SelectItem value="income">收入</SelectItem>
                  <SelectItem value="transfer">转账</SelectItem>
                  <SelectItem value="refund">退款</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="账面名称（商户 / 对手方）" error={formFieldError("payee")}>
              <input
                value={form.payee}
                onChange={(e) => {
                  setForm((f) => ({ ...f, payee: e.target.value }));
                  setFormErrors((errs) => errs.filter((er) => er.field !== "payee"));
                }}
                placeholder="例如：永辉超市"
                className={inputCls}
              />
            </Field>
            <Field label={`金额（${txnCurrency ?? "元"}）`} error={formFieldError("amount")}>
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.amount}
                onChange={(e) => {
                  const v = e.target.value;
                  setForm((f) => ({ ...f, amount: v }));
                  setFormErrors((errs) => errs.filter((er) => er.field !== "amount"));
                }}
                placeholder="0.00"
                className={inputCls}
              />
            </Field>
            {needsBaseAmount && (
              <Field label="折合人民币金额（CNY）" error={baseAmountError ?? undefined}>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.baseAmount}
                  onChange={(e) => {
                    setForm((f) => ({ ...f, baseAmount: e.target.value }));
                    setBaseAmountError(null);
                  }}
                  placeholder="0.00"
                  className={inputCls}
                />
              </Field>
            )}
            {form.type === "transfer" ? (
              <>
                <Field label="从账户" error={formFieldError("account")}>
                  <Select value={form.accountId || undefined} onValueChange={(v) => { setForm((f) => ({ ...f, accountId: v })); setBaseAmountError(null); }}>
                    <SelectTrigger placeholder="选择转出账户" />
                    <SelectContent>
                      {accts.map((a) => (
                        <SelectItem key={a.id} value={a.id} disabled={!canWriteAccount(a)}>
                          {a.name}{accountOptionSuffix(a)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field label="到账户" error={formFieldError("toAccount")}>
                  <Select value={form.toAccountId || undefined} onValueChange={(v) => setForm((f) => ({ ...f, toAccountId: v }))}>
                    <SelectTrigger placeholder="选择转入账户" />
                    <SelectContent>
                      {accts.map((a) => (
                        <SelectItem key={a.id} value={a.id} disabled={!canWriteAccount(a)}>
                          {a.name}{accountOptionSuffix(a)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              </>
            ) : (
              <>
                {form.type === "refund" && (
                  <Field label="原支出交易（必选）" error={formFieldError("refundOf")}>
                    <Select
                      value={form.refundOf || undefined}
                      onValueChange={(v) => setForm((f) => ({ ...f, refundOf: v }))}
                    >
                      <SelectTrigger placeholder="选择被退款的原支出交易" />
                      <SelectContent>
                        {refundCandidates.map((t) => (
                          <SelectItem key={t.id} value={t.id}>
                            {t.payee} · {fmtDate(t.date)} · {fmtMoney(t.amountCents, t.currency)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                )}
                {form.type === "refund" ? (
                  // 退款分类必须继承原支出且不可改：只读展示，提交时不携带 categoryId
                  <Field label="分类（继承原支出，不可修改）">
                    <p className="glass flex h-10 w-full items-center rounded-xl px-3 text-sm text-ink-soft">
                      {refundOriginal ? categoryName(refundOriginal.categoryId) : "选择原支出后自动继承"}
                    </p>
                  </Field>
                ) : (
                  <Field label="分类（选填）">
                    <Select value={form.categoryId} onValueChange={(v) => setForm((f) => ({ ...f, categoryId: v }))}>
                      <SelectTrigger />
                      <SelectContent>
                        <SelectItem value="none">未分类</SelectItem>
                        {formCats.map((c) => (
                          <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                )}
                <Field label="账户" error={formFieldError("account")}>
                  <Select value={form.accountId || undefined} onValueChange={(v) => { setForm((f) => ({ ...f, accountId: v })); setBaseAmountError(null); }}>
                    <SelectTrigger placeholder="选择账户" />
                    <SelectContent>
                      {accts.map((a) => (
                        <SelectItem
                          key={a.id}
                          value={a.id}
                          // 退款：账户币种须为 CNY（代付）或与原支出币种一致，避免后端无法推导账户币金额的组合
                          disabled={
                            !canWriteAccount(a) ||
                            (form.type === "refund" &&
                              !!refundOriginal &&
                              a.currency !== "CNY" &&
                              a.currency !== refundOriginal.currency)
                          }
                        >
                          {a.name}{accountOptionSuffix(a)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                {showCurrencySelect && (
                  <Field label="交易币种">
                    <Select
                      value={form.currency}
                      onValueChange={(v) => {
                        setForm((f) => ({ ...f, currency: v }));
                        setBaseAmountError(null);
                      }}
                    >
                      <SelectTrigger />
                      <SelectContent>
                        <SelectItem value="CNY">CNY 人民币</SelectItem>
                        <SelectItem value="USD">USD 美元</SelectItem>
                        <SelectItem value="EUR">EUR 欧元</SelectItem>
                        <SelectItem value="JPY">JPY 日元</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                )}
              </>
            )}
            <Field label="备注">
              <input
                value={form.note}
                onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
                placeholder="选填"
                className={inputCls}
              />
            </Field>
            {submitError && <p role="alert" className="text-xs text-danger">{submitError}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="secondary" onClick={() => setAddOpen(false)}>取消</Button>
              <Button disabled={submitting} onClick={() => void saveTxn()}>{submitting ? "保存中…" : "保存"}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

type LoadedData = {
  txns: Transaction[];
  accts: Awaited<ReturnType<typeof listAccounts>>;
  cats: Awaited<ReturnType<typeof listCategories>>;
  batches: Awaited<ReturnType<typeof listImportBatches>>;
  memberList: Awaited<ReturnType<typeof listMembers>>;
  splitList: Awaited<ReturnType<typeof listSplits>>;
} | null;

/** 交易详情弹窗：查看 + 编辑（乐观锁）+ 删除（必填理由） */
function TxnDetailDialog({
  txn, onClose, data, categoryName, onChanged,
}: {
  txn: Transaction | null;
  onClose: () => void;
  data: LoadedData;
  categoryName: (id: string | null) => string;
  /** 编辑成功后回传最新交易；删除成功回传 null */
  onChanged: (next: Transaction | null) => void;
}) {
  const [mode, setMode] = useState<"view" | "edit" | "delete">("view");
  const [editForm, setEditForm] = useState({ payee: "", note: "", categoryId: "none" });
  const [deleteReason, setDeleteReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const lastIdRef = useRef<string | null>(null);

  // 仅在切换到另一笔交易时整体重置；同一笔的新版本只同步表单初值，
  // 这样 409 冲突刷新详情后提示文案不会被重置掉
  useEffect(() => {
    if (txn && txn.id !== lastIdRef.current) {
      lastIdRef.current = txn.id;
      setMode("view");
      setActionError(null);
      setDeleteReason("");
    }
    if (txn) {
      setEditForm({ payee: txn.payee, note: txn.note, categoryId: txn.categoryId ?? "none" });
    }
  }, [txn]);

  if (!txn) return null;

  const cats = data?.cats ?? [];
  const editCats = cats.filter(
    (c) => c.active && c.kind === (txn.type === "income" ? "income" : "expense"),
  );

  const saveEdit = async () => {
    if (!editForm.payee.trim()) {
      setActionError("请填写账面名称");
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      const next = await updateTransaction(txn.id, {
        payee: editForm.payee.trim(),
        note: editForm.note.trim(),
        categoryId: txn.type === "transfer" ? undefined : editForm.categoryId === "none" ? null : editForm.categoryId,
        expectedVersion: txn.version,
      });
      onChanged(next);
      setMode("view");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // 版本冲突：拉取最新详情并提示用户重新核对
        try {
          const fresh = await getTransaction(txn.id);
          onChanged(fresh);
        } catch {
          onChanged(null);
        }
        setMode("view");
        setActionError(CONFLICT_COPY);
      } else {
        setActionError(errMsg(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteReason.trim()) return;
    setBusy(true);
    setActionError(null);
    try {
      await deleteTransaction(txn.id, deleteReason.trim());
      onChanged(null);
    } catch (e) {
      setActionError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="w-[min(42rem,calc(100vw-2rem))]">
        <DialogHeader
          title="交易详情"
          desc={mode === "edit" ? "编辑账面名称 / 备注 / 分类" : mode === "delete" ? "删除后进入回收站，可恢复" : "复式记账 · 借贷必平衡"}
        />
        {mode === "view" && (
          <>
            <TxnDetailContent
              txn={txn}
              accounts={data?.accts ?? []}
              categories={data?.cats ?? []}
              members={data?.memberList ?? []}
              batches={data?.batches ?? []}
              splits={data?.splitList ?? []}
              transactions={data?.txns ?? []}
            />
            <div className="mt-4 flex items-center justify-end gap-2 border-t border-slate-900/5 pt-3">
              {actionError && (
                <p role="alert" className="mr-auto text-xs text-danger">{actionError}</p>
              )}
              <Button variant="secondary" size="sm" onClick={() => { setMode("edit"); setActionError(null); }}>
                <Pencil className="h-3.5 w-3.5" /> 编辑
              </Button>
              <Button variant="danger" size="sm" onClick={() => { setMode("delete"); setActionError(null); }}>
                <Trash2 className="h-3.5 w-3.5" /> 删除
              </Button>
            </div>
          </>
        )}
        {mode === "edit" && (
          <div className="space-y-3">
            <Field label="账面名称（商户 / 对手方）">
              <input
                value={editForm.payee}
                onChange={(e) => setEditForm((f) => ({ ...f, payee: e.target.value }))}
                className={inputCls}
              />
            </Field>
            {txn.type !== "transfer" && (
              <Field label="分类（选填）">
                <Select
                  value={editForm.categoryId}
                  onValueChange={(v) => setEditForm((f) => ({ ...f, categoryId: v }))}
                >
                  <SelectTrigger />
                  <SelectContent>
                    <SelectItem value="none">未分类</SelectItem>
                    {editCats.map((c) => (
                      <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}
            <Field label="备注">
              <input
                value={editForm.note}
                onChange={(e) => setEditForm((f) => ({ ...f, note: e.target.value }))}
                placeholder="选填"
                className={inputCls}
              />
            </Field>
            <p className="text-xs text-ink-faint">当前版本 v{txn.version}，保存时携带版本号防并发覆盖。</p>
            {actionError && <p role="alert" className="text-xs text-danger">{actionError}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="secondary" onClick={() => { setMode("view"); setActionError(null); }}>取消</Button>
              <Button disabled={busy} onClick={() => void saveEdit()}>{busy ? "保存中…" : "保存"}</Button>
            </div>
          </div>
        )}
        {mode === "delete" && (
          <div className="space-y-3">
            <p className="text-sm text-ink">
              确定删除「{txn.payee} · {fmtDate(txn.date)} · {categoryName(txn.categoryId)}」吗？
            </p>
            <Field label="删除理由（必填，写入审计日志）">
              <textarea
                value={deleteReason}
                onChange={(e) => setDeleteReason(e.target.value)}
                rows={2}
                placeholder="例如：重复录入"
                className="glass w-full rounded-xl px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-faint"
              />
            </Field>
            {actionError && <p role="alert" className="text-xs text-danger">{actionError}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="secondary" onClick={() => { setMode("view"); setActionError(null); }}>取消</Button>
              <Button variant="danger" disabled={busy || !deleteReason.trim()} onClick={() => void confirmDelete()}>
                {busy ? "删除中…" : "确认删除"}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** 回收站：已删除交易列表 + 恢复（携带版本号，409 强制刷新） */
function RecycleBin({
  rows, loading, error, retry, categoryName, onRestored,
}: {
  rows: Transaction[];
  loading: boolean;
  error: ApiError | null;
  retry: () => void;
  categoryName: (id: string | null) => string;
  onRestored: () => void;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const restore = async (t: Transaction) => {
    setBusyId(t.id);
    setActionError(null);
    try {
      await restoreTransaction(t.id, t.version);
      onRestored();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        retry();
        setActionError(CONFLICT_COPY);
      } else {
        setActionError(errMsg(e));
      }
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Card className="p-4">
      {loading ? (
        <TxnTableSkeleton rows={3} />
      ) : error ? (
        <ErrorState title="回收站加载失败" desc={error.message} onRetry={retry} />
      ) : rows.length === 0 ? (
        <EmptyState icon={<Trash2 className="h-8 w-8" />} title="回收站为空" hint="删除的交易会先进入回收站" />
      ) : (
        <>
          {actionError && <p role="alert" className="mb-3 text-xs text-danger">{actionError}</p>}
          <table className="w-full text-sm">
            <thead className="text-xs text-ink-faint">
              <tr className="border-b border-slate-900/10">
                <th className="py-2 text-left font-medium">日期</th>
                <th className="py-2 text-left font-medium">类型</th>
                <th className="py-2 text-left font-medium">商户</th>
                <th className="py-2 text-left font-medium">分类</th>
                <th className="py-2 text-right font-medium">金额</th>
                <th className="py-2 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => {
                const meta = typeMeta[t.type];
                return (
                  <tr key={t.id} className="border-b border-slate-900/5 last:border-0">
                    <td className="py-2.5 pr-2 text-ink-soft">{fmtDate(t.date)}</td>
                    <td className="py-2.5 pr-2"><Badge tone={meta.tone}>{meta.label}</Badge></td>
                    <td className="py-2.5 pr-2">
                      <span className="block text-ink">{t.payee}</span>
                      {t.note && <span className="block text-xs text-ink-faint">{t.note}</span>}
                    </td>
                    <td className="py-2.5 pr-2 text-ink-soft">
                      {t.type === "transfer" ? "—" : categoryName(t.categoryId)}
                    </td>
                    <td className="py-2.5 pr-2 text-right">
                      <Amount
                        cents={t.baseAmountCents}
                        currency={t.baseCurrency}
                        kind={t.type === "expense" ? "expense" : t.type === "income" ? "income" : "neutral"}
                      />
                    </td>
                    <td className="py-2.5 text-right">
                      <Button variant="secondary" size="sm" disabled={busyId === t.id} onClick={() => void restore(t)}>
                        <Undo2 className="h-3.5 w-3.5" /> {busyId === t.id ? "恢复中…" : "恢复"}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </Card>
  );
}

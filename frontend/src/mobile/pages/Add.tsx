import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Check, Shapes, X } from "lucide-react";
import { Button } from "@/components/ui";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { ErrorState } from "@/components/error";
import { PageSkeleton } from "@/components/skeleton";
import { dataIcon } from "@/components/icons";
import { cn } from "@/lib/utils";
import { useApiQuery } from "@/api/useApi";
import { createTransaction, listAccounts, listCategories, type NewTransactionInput } from "@/api/resources";
import { ApiError } from "@/api/client";
import { validateTransaction, type TxnField } from "@/lib/validate-txn";
import { parseDecimalCents, parsePositiveCents } from "@/lib/decimal";
import { accountOptionSuffix, canWriteAccount } from "@/lib/accounts";
import type { Account } from "@/data/types";

type Kind = "expense" | "income" | "transfer";
const kinds: { key: Kind; label: string }[] = [
  { key: "expense", label: "支出" },
  { key: "income", label: "收入" },
  { key: "transfer", label: "转账" },
];

const accountLabel = (a: Account) => `${a.name}${a.kind === "liability" ? "（负债）" : ""}`;

/** 账户下拉：仅可写入（write/manage）的账户可选；仅查看（view）的禁用并标注 */
function AccountSelect({
  value, onChange, accts, placeholder,
}: { value: string; onChange: (v: string) => void; accts: Account[]; placeholder?: string }) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger placeholder={placeholder} />
      <SelectContent>
        {accts.map((a) => (
          <SelectItem key={a.id} value={a.id} disabled={!canWriteAccount(a)}>
            {accountLabel(a)}
            {accountOptionSuffix(a)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** 本页字段在共享校验之外多了外币折合人民币（baseAmount），validate-txn.ts 是共享文件不能改，字段类型在本地扩展 */
type AddField = TxnField | "baseAmount";
interface AddError {
  field: AddField;
  message: string;
}

function FieldError({ errors, field }: { errors: AddError[]; field: AddField }) {
  const err = errors.find((e) => e.field === field);
  return err ? <p className="mt-1.5 text-xs text-danger">{err.message}</p> : null;
}

export default function AddPage() {
  const navigate = useNavigate();
  const { data, loading, error, retry } = useApiQuery(
    () =>
      Promise.all([listAccounts(), listCategories()]).then(([accts, cats]) => ({ accts, cats })),
    [],
  );

  const [kind, setKind] = useState<Kind>("expense");
  const [payee, setPayee] = useState("");
  const [amount, setAmount] = useState("");
  // 外币交易必填：折合人民币金额（CNY，元字符串），仅外币账户时显示/提交
  const [baseAmount, setBaseAmount] = useState("");
  // null = 不选分类（分类允许为空）
  const [catId, setCatId] = useState<string | null>(null);
  const [accountId, setAccountId] = useState("");
  const [fromId, setFromId] = useState("");
  const [toId, setToId] = useState("");
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<AddError[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const accts = useMemo(() => data?.accts ?? [], [data]);
  const writable = useMemo(() => accts.filter(canWriteAccount), [accts]);
  // 当前收支类型下的可用分类：仅启用（active）的
  const cats = useMemo(
    () =>
      (data?.cats ?? []).filter(
        (c) => c.active && c.kind === (kind === "income" ? "income" : "expense"),
      ),
    [data, kind],
  );

  // 交易币种 = 所选账户币种（转账以转出账户为准）；外币交易必须另填折合人民币金额
  const txnAccount = useMemo(
    () => accts.find((a) => a.id === (kind === "transfer" ? fromId : accountId)),
    [accts, kind, fromId, accountId],
  );
  const txnCurrency = txnAccount?.currency ?? "CNY";
  const isForeign = txnCurrency !== "CNY";

  // 数据到位后初始化账户默认值：只预选可写入（manage）的账户
  useEffect(() => {
    if (writable.length === 0) return;
    setAccountId((v) => v || writable[0].id);
    setFromId((v) => v || writable[0].id);
    setToId((v) => v || (writable[1]?.id ?? writable[0].id));
  }, [writable]);

  const switchKind = (k: Kind) => {
    setKind(k);
    setErrors([]);
    setSubmitError(null);
  };

  const onAmount = (v: string) => {
    if (/^\d*\.?\d{0,2}$/.test(v)) {
      setAmount(v);
      setErrors((errs) => errs.filter((e) => e.field !== "amount"));
    }
  };

  const onBaseAmount = (v: string) => {
    if (/^\d*\.?\d{0,2}$/.test(v)) {
      setBaseAmount(v);
      setErrors((errs) => errs.filter((e) => e.field !== "baseAmount"));
    }
  };

  const save = async () => {
    const errs: AddError[] = validateTransaction({
      type: kind,
      payee,
      amount,
      categoryId: kind === "transfer" ? null : catId,
      accountId: kind === "transfer" ? fromId : accountId,
      toAccountId: kind === "transfer" ? toId : undefined,
    });

    // 转账：后端要求转出/转入账户同币种且等于交易币种，前端先拦住不发请求
    if (kind === "transfer" && fromId && toId) {
      const fromCur = accts.find((a) => a.id === fromId)?.currency;
      const toCur = accts.find((a) => a.id === toId)?.currency;
      if (fromCur && toCur && fromCur !== toCur) {
        errs.push({ field: "toAccount", message: "转出与转入账户币种必须相同" });
      }
    }

    // 外币交易：折合人民币金额（CNY）必填，缺失/非法在此阻断，绝不让用户吃 422
    if (isForeign) {
      if (!baseAmount.trim()) {
        errs.push({ field: "baseAmount", message: "请输入折合人民币金额" });
      } else {
        const parsed = parsePositiveCents(baseAmount);
        if (!parsed.ok) {
          errs.push({
            field: "baseAmount",
            message: parsed.error.includes("两位小数")
              ? "折合人民币金额最多支持两位小数"
              : parsed.error === "金额必须大于 0"
                ? "折合人民币金额必须大于 0"
                : parsed.error,
          });
        }
      }
    }

    setErrors(errs);
    if (errs.length > 0) return;

    // 校验已通过，这里再精确解析一次兜底：无效/超界绝不发出请求
    const parsedAmount = parseDecimalCents(amount);
    if (!parsedAmount.ok || parsedAmount.cents <= 0) return;
    const parsedBase = isForeign ? parsePositiveCents(baseAmount) : null;
    if (isForeign && (!parsedBase || !parsedBase.ok)) return;

    const acctId = kind === "transfer" ? fromId : accountId;
    const input: NewTransactionInput = {
      type: kind,
      payee: payee.trim(),
      note: note.trim(),
      categoryId: kind === "transfer" ? null : catId,
      accountId: acctId,
      ...(kind === "transfer" ? { toAccountId: toId } : {}),
      amountCents: parsedAmount.cents,
      currency: accts.find((a) => a.id === acctId)?.currency ?? "CNY",
      date: new Date().toISOString(),
      // CNY 不提交 baseAmountCents；外币必填折合金额，本表单不填 fxRate（不传该字段）
      ...(isForeign && parsedBase && parsedBase.ok ? { baseAmountCents: parsedBase.cents } : {}),
    };

    setSubmitting(true);
    setSubmitError(null);
    try {
      await createTransaction(input);
      setSaved(true);
      setTimeout(() => navigate("/txns"), 800);
    } catch (e) {
      // 失败只展示真实错误，表单状态全部保留
      setSubmitError(e instanceof ApiError ? e.message : "保存失败，请稍后重试");
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-dvh flex-col px-4 pt-[calc(0.75rem+env(safe-area-inset-top))]">
        <div className="mb-4 flex items-center justify-between">
          <button
            onClick={() => navigate(-1)}
            className="rounded-full p-2 text-ink-soft hover:bg-slate-900/5"
            aria-label="关闭"
          >
            <X className="h-5 w-5" />
          </button>
          <span className="text-sm font-medium text-ink">记一笔</span>
          <span className="w-9" />
        </div>
        <PageSkeleton />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-dvh flex-col px-4 pt-[calc(0.75rem+env(safe-area-inset-top))]">
        <div className="mb-4 flex items-center justify-between">
          <button
            onClick={() => navigate(-1)}
            className="rounded-full p-2 text-ink-soft hover:bg-slate-900/5"
            aria-label="关闭"
          >
            <X className="h-5 w-5" />
          </button>
          <span className="text-sm font-medium text-ink">记一笔</span>
          <span className="w-9" />
        </div>
        <ErrorState title="账户与分类加载失败" desc={error.message} onRetry={retry} className="mt-10" />
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh flex-col px-4 pt-[calc(0.75rem+env(safe-area-inset-top))]">
      {/* 顶部关闭 */}
      <div className="mb-4 flex items-center justify-between">
        <button
          onClick={() => navigate(-1)}
          className="rounded-full p-2 text-ink-soft hover:bg-slate-900/5"
          aria-label="关闭"
        >
          <X className="h-5 w-5" />
        </button>
        <span className="text-sm font-medium text-ink">记一笔</span>
        <span className="w-9" />
      </div>

      {/* 类型切换 */}
      <div className="mb-6 grid grid-cols-3 gap-2">
        {kinds.map((k) => (
          <button
            key={k.key}
            onClick={() => switchKind(k.key)}
            className={cn(
              "glass h-11 rounded-xl text-sm font-medium transition",
              kind === k.key ? "bg-accent text-white shadow-sm" : "text-ink-soft",
            )}
          >
            {k.label}
          </button>
        ))}
      </div>

      {/* 金额：单位跟随所选账户币种，外币显示币种代码而非固定 ¥ */}
      <div className="mb-6">
        <div className="flex items-baseline justify-center gap-1">
          <span className="font-num text-2xl text-ink-faint">{isForeign ? txnCurrency : "¥"}</span>
          <input
            value={amount}
            onChange={(e) => onAmount(e.target.value)}
            inputMode="decimal"
            placeholder="0.00"
            autoFocus
            className="w-48 bg-transparent text-center font-num text-5xl font-semibold text-ink outline-none placeholder:text-ink-faint/40"
          />
        </div>
        {errors.some((e) => e.field === "amount") && (
          <p className="mt-2 text-center text-xs text-danger">
            {errors.find((e) => e.field === "amount")!.message}
          </p>
        )}
      </div>

      {/* 外币交易必填：折合人民币金额（CNY）；CNY 账户不显示也不提交 */}
      {isForeign && (
        <div className="mb-4">
          <p className="mb-1.5 text-xs font-medium text-ink-faint">折合人民币金额（CNY，必填）</p>
          <div className="glass flex h-10 items-center gap-2 rounded-xl px-3">
            <span className="font-num text-sm text-ink-faint">¥</span>
            <input
              value={baseAmount}
              onChange={(e) => onBaseAmount(e.target.value)}
              inputMode="decimal"
              placeholder="0.00"
              aria-label="折合人民币金额（CNY）"
              className="w-full bg-transparent font-num text-sm text-ink outline-none placeholder:text-ink-faint"
            />
          </div>
          <FieldError errors={errors} field="baseAmount" />
        </div>
      )}

      {/* 账面名称（必填） */}
      <div className="mb-4">
        <p className="mb-1.5 text-xs font-medium text-ink-faint">账面名称（必填）</p>
        <input
          value={payee}
          onChange={(e) => {
            setPayee(e.target.value);
            setErrors((errs) => errs.filter((err) => err.field !== "payee"));
          }}
          placeholder="例如：盒马鲜生、工资"
          className="glass h-10 w-full rounded-xl px-3 text-sm text-ink outline-none placeholder:text-ink-faint"
        />
        <FieldError errors={errors} field="payee" />
      </div>

      {/* 分类 / 账户 */}
      <div className="flex-1">
        {kind === "transfer" ? (
          <div className="space-y-3">
            <div>
              <p className="mb-1.5 text-xs font-medium text-ink-faint">从账户</p>
              <AccountSelect value={fromId} onChange={setFromId} accts={accts} />
              <FieldError errors={errors} field="account" />
            </div>
            <div>
              <p className="mb-1.5 text-xs font-medium text-ink-faint">到账户</p>
              <AccountSelect value={toId} onChange={setToId} accts={accts} />
              <FieldError errors={errors} field="toAccount" />
            </div>
          </div>
        ) : (
          <>
            <p className="mb-2 text-xs font-medium text-ink-faint">分类（可不选）</p>
            <div className="grid grid-cols-4 gap-2">
              <button
                onClick={() => setCatId(null)}
                className={cn(
                  "glass flex flex-col items-center gap-1.5 rounded-xl py-3 transition",
                  catId === null && "ring-2 ring-accent motion-safe:animate-pop",
                )}
              >
                <Shapes className={cn("h-5 w-5", catId === null ? "text-accent" : "text-ink-soft")} />
                <span className={cn("text-xs", catId === null ? "font-medium text-ink" : "text-ink-soft")}>
                  不分类
                </span>
              </button>
              {cats.map((c) => {
                const Icon = dataIcon(c.icon);
                const active = catId === c.id;
                return (
                  <button
                    key={c.id}
                    onClick={() => setCatId(active ? null : c.id)}
                    className={cn(
                      "glass flex flex-col items-center gap-1.5 rounded-xl py-3 transition",
                      active && "ring-2 ring-accent motion-safe:animate-pop",
                    )}
                  >
                    <Icon className={cn("h-5 w-5", active ? "text-accent" : "text-ink-soft")} />
                    <span className={cn("text-xs", active ? "font-medium text-ink" : "text-ink-soft")}>
                      {c.name}
                    </span>
                  </button>
                );
              })}
            </div>
            <p className="mb-1.5 mt-5 text-xs font-medium text-ink-faint">账户</p>
            <AccountSelect value={accountId} onChange={setAccountId} accts={accts} placeholder="选择账户" />
            <FieldError errors={errors} field="account" />
            {writable.length === 0 && (
              <p className="mt-1.5 text-xs text-danger">你没有可记账的账户（需要账户的写入权限）</p>
            )}
          </>
        )}

        <p className="mb-1.5 mt-5 text-xs font-medium text-ink-faint">备注（选填）</p>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="写点什么…"
          className="glass h-10 w-full rounded-xl px-3 text-sm text-ink outline-none placeholder:text-ink-faint"
        />
      </div>

      {/* 保存 */}
      <div className="sticky bottom-0 pb-[calc(1rem+env(safe-area-inset-bottom))] pt-3">
        {submitError && (
          <p className="mb-2 text-center text-xs text-danger">{submitError}</p>
        )}
        <Button
          size="lg"
          className="w-full"
          disabled={submitting || saved}
          onClick={save}
        >
          {saved ? (
            <>
              <Check className="h-4 w-4 motion-safe:animate-pop" />
              已保存
            </>
          ) : submitting ? (
            "保存中…"
          ) : (
            "保存"
          )}
        </Button>
      </div>
    </div>
  );
}

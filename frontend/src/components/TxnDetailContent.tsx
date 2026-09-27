import { Badge } from "./ui";
import { fmtDateTime, fmtMoney } from "@/lib/utils";
import { formatCentsGrouped } from "@/lib/decimal";
import { accountDisplayName } from "@/lib/privacy";
import type { Account, Category, EntryLine, ImportBatch, Member, Split, Transaction } from "@/data/types";

const typeMeta: Record<Transaction["type"], { label: string; tone: "neutral" | "accent" | "danger" | "warn" }> = {
  expense: { label: "支出", tone: "neutral" },
  income: { label: "收入", tone: "accent" },
  transfer: { label: "转账", tone: "neutral" },
  refund: { label: "退款", tone: "warn" },
};

/** id 兜底显示：查不到名字时给短 id，不裸露整串 */
const shortId = (id: string) => (id.length > 10 ? `${id.slice(0, 8)}…` : id);

/** 由可选数据 props 构造 id → 名称解析器；缺数据时退化为短 id */
function useResolvers(accounts: Account[], categories: Category[], members: Member[]) {
  const accountName = (id: string) => accountDisplayName(id, accounts, shortId);
  const categoryName = (id: string | null) =>
    id === null ? "未分类" : (categories.find((c) => c.id === id)?.name ?? shortId(id));
  const memberName = (id: string) => members.find((m) => m.id === id)?.name ?? shortId(id);
  return { accountName, categoryName, memberName };
}

const cny = (cents: number) => formatCentsGrouped(cents);

/** 交易详情（双端共用）：类型、来源关联、退款关联、分摊关联、复式分录。
 *  不直连 mock：账户/分类/成员/批次/分摊/交易列表全部由父级注入。 */
export function TxnDetailContent({
  txn,
  accounts = [],
  categories = [],
  members = [],
  batches = [],
  splits = [],
  transactions = [],
}: {
  txn: Transaction;
  accounts?: Account[];
  categories?: Category[];
  members?: Member[];
  batches?: ImportBatch[];
  splits?: Split[];
  transactions?: Transaction[];
}) {
  const meta = typeMeta[txn.type];
  const { accountName, categoryName, memberName } = useResolvers(accounts, categories, members);

  /** 分录科目显示名：损益行（accountId=null）按 ledgerCode 解析；账户行按账户名，
   *  private:counterparty 由 accountDisplayName 安全脱敏 */
  const entryLineName = (line: EntryLine): string => {
    if (line.accountId !== null) return accountName(line.accountId);
    const code = line.ledgerCode ?? "";
    const m = /^(expense|income):(.+)$/.exec(code);
    if (m) {
      const label = m[1] === "expense" ? "支出" : "收入";
      return `${label} · ${categoryName(m[2] === "uncategorized" ? null : m[2])}`;
    }
    // 解析不了的 ledgerCode 原样显示
    return code || "—";
  };

  const batch = txn.batchId ? batches.find((b) => b.id === txn.batchId) : null;
  const refundOf = txn.refundOf ? transactions.find((t) => t.id === txn.refundOf) : null;
  const split = txn.splitId ? splits.find((s) => s.id === txn.splitId) : null;

  const Line = ({ line }: { line: EntryLine }) => (
    <tr className="border-b border-slate-900/5 last:border-0">
      <td className="py-1.5 pr-2 text-ink-soft">
        {entryLineName(line)}
        {line.accountAmountCents !== null && line.accountCurrency !== null && (
          <span className="block text-[11px] text-ink-faint">
            账户币金额 {line.accountCurrency} {formatCentsGrouped(line.accountAmountCents)}
          </span>
        )}
      </td>
      <td className="py-1.5 pr-2 text-right font-num tabular-nums text-ink">
        {line.debitCents ? cny(line.debitCents) : ""}
      </td>
      <td className="py-1.5 text-right font-num tabular-nums text-ink">
        {line.creditCents ? cny(line.creditCents) : ""}
      </td>
    </tr>
  );

  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={meta.tone}>{meta.label}</Badge>
        {txn.currency !== txn.baseCurrency && (
          <Badge tone="neutral">
            {txn.fxRate != null ? `外币 ${txn.currency} · 汇率 ${txn.fxRate}` : `外币 ${txn.currency}`}
          </Badge>
        )}
        <Badge tone={txn.source === "import" ? "accent" : "neutral"}>
          {txn.source === "import" ? "导入" : "手动"}
        </Badge>
      </div>

      <div className="grid grid-cols-[4.5rem_1fr] gap-y-1.5">
        <span className="text-ink-faint">商户</span>
        <span className="text-ink">{txn.payee}</span>
        <span className="text-ink-faint">金额</span>
        <span className="text-ink">
          {fmtMoney(txn.amountCents, txn.currency)}
          {txn.currency !== txn.baseCurrency &&
            `（折 ${fmtMoney(txn.baseAmountCents, txn.baseCurrency)}）`}
        </span>
        <span className="text-ink-faint">分类</span>
        <span className="text-ink">{txn.type === "transfer" ? "转账（无分类）" : categoryName(txn.categoryId)}</span>
        <span className="text-ink-faint">时间</span>
        <span className="text-ink">{fmtDateTime(txn.date)}</span>
        <span className="text-ink-faint">备注</span>
        <span className="text-ink">{txn.note || "—"}</span>
        <span className="text-ink-faint">记录人</span>
        <span className="text-ink">{memberName(txn.createdBy)}</span>
        <span className="text-ink-faint">版本</span>
        <span className="text-ink font-num tabular-nums">v{txn.version}</span>
      </div>

      {(batch || refundOf || split) && (
        <div className="space-y-1.5 rounded-xl bg-slate-900/[0.03] p-3 text-xs">
          {batch && (
            <p className="text-ink-soft">
              来源批次：<span className="text-ink">{batch.filename}</span>（{batch.source}）
            </p>
          )}
          {txn.refundOf && (
            <p className="text-ink-soft">
              退款关联：
              {refundOf
                ? <>原交易「{refundOf.payee} · {fmtDateTime(refundOf.date)}」</>
                : <>原交易 {shortId(txn.refundOf)}</>}
            </p>
          )}
          {split && (
            <p className="text-ink-soft">
              分摊关联：「{split.title}」，发起人 {memberName(split.createdBy)}
            </p>
          )}
        </div>
      )}

      <div>
        <p className="mb-1 text-xs font-medium text-ink-faint">
          复式分录（借贷平衡，借贷金额为基础币 {txn.baseCurrency}）
        </p>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-900/10 text-ink-faint">
              <th className="py-1 text-left font-medium">科目</th>
              <th className="py-1 text-right font-medium">借方</th>
              <th className="py-1 text-right font-medium">贷方</th>
            </tr>
          </thead>
          <tbody>
            {txn.lines.map((l, i) => (
              <Line key={i} line={l} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

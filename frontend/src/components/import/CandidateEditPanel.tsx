import { Button } from "@/components/ui";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import type { Account, ImportCandidate } from "@/data/types";
import type { CandidateEditDraft } from "@/domain/imports";
import { accountOptionSuffix, canWriteAccount } from "@/lib/accounts";

const NO_ACCOUNT_SENTINEL = "__unset__";

export interface CandidateEditPanelProps {
  candidate: ImportCandidate;
  draft: CandidateEditDraft;
  accounts: readonly Account[];
  saving: boolean;
  error: string | null;
  onChange: (patch: Partial<CandidateEditDraft>) => void;
  onSave: () => void;
  onCancel: () => void;
}

/**
 * 纯展示组件：导入候选行内修正面板（日期时间 / 商户 / 原币金额 / 入账账户）。
 * 保存动作交由上层控制器携带 expectedVersion 走 PATCH 接口，不在组件内部直连 API。
 */
export function CandidateEditPanel({
  candidate,
  draft,
  accounts,
  saving,
  error,
  onChange,
  onSave,
  onCancel,
}: CandidateEditPanelProps) {
  return (
    <div className="space-y-3 rounded-xl bg-accent-soft/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium text-ink">
          修正候选字段（保存后生成新版本，当前 v{candidate.version}）
        </p>
        {candidate.currency !== "CNY" && (
          <p className="text-[11px] text-ink-faint">
            提示：修改外币原币金额后，如原折算金额不再匹配需重新补录汇率
          </p>
        )}
      </div>

      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-faint">交易时间（上海时间）</span>
          <input
            type="datetime-local"
            aria-label={`交易时间（${candidate.payee}）`}
            value={draft.date}
            disabled={saving}
            onChange={(e) => onChange({ date: e.target.value })}
            className="glass h-8 w-full rounded-xl px-2.5 text-xs text-ink outline-none"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-faint">商户 / 交易对方</span>
          <input
            type="text"
            aria-label={`商户名称（${candidate.payee}）`}
            value={draft.payee}
            disabled={saving}
            placeholder="商户或摘要"
            onChange={(e) => onChange({ payee: e.target.value })}
            className="glass h-8 w-full rounded-xl px-2.5 text-xs text-ink outline-none placeholder:text-ink-faint"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-faint">
            金额（{candidate.currency === "CNY" ? "元" : candidate.currency}）
          </span>
          <input
            type="text"
            inputMode="decimal"
            aria-label={`候选金额（${candidate.payee}）`}
            value={draft.amount}
            disabled={saving}
            placeholder="0.00"
            onChange={(e) => onChange({ amount: e.target.value })}
            className="glass h-8 w-full rounded-xl px-2.5 font-num text-xs text-ink outline-none placeholder:text-ink-faint"
          />
        </label>

        <div>
          <span className="mb-1 block text-[11px] text-ink-faint">入账账户</span>
          <Select
            value={draft.targetAccountId || NO_ACCOUNT_SENTINEL}
            onValueChange={(v) => onChange({ targetAccountId: v === NO_ACCOUNT_SENTINEL ? "" : v })}
            disabled={saving}
          >
            <SelectTrigger className="h-8 text-xs" placeholder="选择入账账户" />
            <SelectContent>
              {!draft.targetAccountId && (
                <SelectItem value={NO_ACCOUNT_SENTINEL}>暂不指定</SelectItem>
              )}
              {accounts.map((a) => (
                <SelectItem key={a.id} value={a.id} disabled={!canWriteAccount(a)}>
                  {a.name}{accountOptionSuffix(a)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" disabled={saving} onClick={onCancel}>
          取消修改
        </Button>
        <Button size="sm" disabled={saving} onClick={onSave}>
          {saving ? "保存中…" : "保存修改"}
        </Button>
      </div>
    </div>
  );
}

import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { KeyRound, Pencil, Plus, Trash2 } from "lucide-react";
import { AdminHeader } from "../components/AdminHeader";
import { Amount, Badge, Button, Card, EmptyState } from "@/components/ui";
import { Dialog, DialogContent, DialogHeader } from "@/components/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { CardGridSkeleton, Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { dataIcon } from "@/components/icons";
import { fmtDate } from "@/lib/utils";
import { sumCents } from "@/lib/decimal";
import { useApiQuery } from "@/api/useApi";
import {
  createAccount, deleteAccountGrant, listAccountGrants, listAccounts, listMembers,
  listTransactions, setAccountGrant, updateAccount,
} from "@/api/resources";
import { ApiError } from "@/api/client";
import { parseDecimalCents } from "@/lib/decimal";
import type { Account, AccountGrant, Member, Transaction } from "@/data/types";

const inputCls = "glass h-10 w-full rounded-xl px-3 text-sm text-ink outline-none placeholder:text-ink-faint";

const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : "操作失败，请稍后重试");

/** 新增/改名弹窗里可选的账户图标（对应 dataIcon 映射键） */
const ICON_OPTIONS: { value: string; label: string }[] = [
  { value: "wallet", label: "钱包" },
  { value: "banknote", label: "现金" },
  { value: "credit-card", label: "信用卡" },
  { value: "landmark", label: "银行" },
  { value: "home", label: "房贷/房产" },
  { value: "dollar-sign", label: "外币" },
];

function Field({ label, error, children }: { label: string; error?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-ink-faint">{label}</span>
      {children}
      {error && <span className="mt-1 block text-xs text-danger">{error}</span>}
    </label>
  );
}

/**
 * 该账户在某笔交易中的净变动（符号=余额/债务增加方向）；bigint 贯穿，超界不失真。
 * 资产账户 = debit − credit（debit 为余额增加）；负债账户余额为正数债务，= credit − debit（credit 为债务增加）。
 * 只认当前账户的真实账户分录（accountId 匹配；损益行 accountId=null 天然不命中）：
 * 分录带 accountAmountCents/accountCurrency 时以账户币金额为准、由借贷方向定正负，
 * 绝不把基础币金额贴上账户币标签；同一交易内该账户分录币种不一致直接抛错（不静默混加）。
 */
function accountDelta(t: Transaction, account: Account): { cents: bigint; currency: string } {
  const signed: number[] = [];
  let currency: string | null = null;
  // 负债余额是正数债务：credit=债务增加（正）；资产相反：debit=余额增加（正）
  const isLiability = account.kind === "liability";
  for (const l of t.lines) {
    if (l.accountId !== account.id) continue;
    const lineCurrency = l.accountCurrency ?? t.baseCurrency;
    if (currency === null) currency = lineCurrency;
    else if (currency !== lineCurrency) {
      throw new RangeError(`交易 ${t.id} 中账户分录币种不一致（${currency} vs ${lineCurrency}），无法汇总`);
    }
    if (l.accountAmountCents !== null) {
      // 原币金额为无符号面值，方向由借贷方向决定
      const increasing = isLiability ? l.creditCents > 0 : l.debitCents > 0;
      signed.push(increasing ? l.accountAmountCents : -l.accountAmountCents);
    } else {
      signed.push(isLiability ? l.creditCents - l.debitCents : l.debitCents - l.creditCents);
    }
  }
  return { cents: sumCents(signed), currency: currency ?? t.baseCurrency };
}

/** 汇总条骨架：三列统计位 */
function SummarySkeleton() {
  return (
    <Card className="mb-4 flex divide-x divide-slate-900/5 p-5" aria-busy="true" aria-label="加载中">
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex-1 space-y-2 px-5 first:pl-0 last:pr-0">
          <Skeleton className="h-3 w-14" />
          <Skeleton className="h-5 w-24" />
        </div>
      ))}
    </Card>
  );
}

export default function AccountsPage() {
  const [tab, setTab] = useState("all");
  const [selected, setSelected] = useState<Account | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Account | null>(null);
  const [grantsTarget, setGrantsTarget] = useState<Account | null>(null);
  // 409 写锁与冲突提示：true 期间禁止账户写操作，直到刷新最新列表成功
  const [writeLocked, setWriteLocked] = useState(false);
  const [conflictMsg, setConflictMsg] = useState<string | null>(null);
  // 本地账户列表：query 数据到位即同步；写操作以接口返回的 Account 原子替换，两者不互相遮蔽
  const [acctsLocal, setAcctsLocal] = useState<Account[] | null>(null);
  // 账户列表 + 详情弹窗用的近期交易 + 成员名解析；汇总由账户列表前端聚合
  const { data, loading, error, retry } = useApiQuery(
    async () => {
      const [accts, txns, memberList] = await Promise.all([
        listAccounts(),
        listTransactions(),
        listMembers(),
      ]);
      return { accts, txns, memberList };
    },
    [],
  );

  useEffect(() => {
    if (data) setAcctsLocal(data.accts);
  }, [data]);

  const accts = acctsLocal ?? data?.accts ?? [];
  const txns = data?.txns ?? [];
  const memberList = data?.memberList ?? [];

  const memberName = (id: string | null) =>
    id === null ? "—" : (memberList.find((m) => m.id === id)?.name ?? "未知成员");

  const filtered = accts.filter((a) =>
    tab === "all" ? true : tab === "family" ? a.scope === "family" : a.kind === tab,
  );

  // 前端聚合口径（只计 CNY 账户，外币不折算、另行展示）；BigInt 安全求和
  const totalAsset = sumCents(accts.filter((a) => a.kind === "asset" && a.currency === "CNY").map((a) => a.balanceCents));
  // 负债余额为正数债务：负债合计直接展示正数，净资产 = 资产 − 负债（只减一次）
  const totalLiability = sumCents(accts.filter((a) => a.kind === "liability" && a.currency === "CNY").map((a) => a.balanceCents));
  const summary = [
    { label: "资产合计", cents: totalAsset },
    { label: "负债合计", cents: totalLiability },
    { label: "净资产", cents: totalAsset - totalLiability }, // bigint − bigint：合法大数合计不溢出
  ];

  // 列表刷新后同步详情弹窗里的账户（版本号、余额可能已变）
  const selectedFresh = selected ? (accts.find((a) => a.id === selected.id) ?? selected) : null;
  const selectedTxns = selectedFresh
    ? txns
        .filter((t) => t.lines.some((l) => l.accountId === selectedFresh.id))
        .sort((a, b) => b.date.localeCompare(a.date))
        .slice(0, 5)
    : [];

  /**
   * 409 冲突：同步关闭改名弹窗并丢弃旧快照/表单、进入写锁，再 try 拉取最新账户。
   * 仅 GET 成功替换完整列表后才解锁并提示「已刷新」；失败诚实提示刷新失败、
   * 维持写锁，由「重试刷新」重走本函数。捕获一切异常，绝不向上抛。
   */
  const refreshAfterConflict = async () => {
    setEditTarget(null);
    setWriteLocked(true);
    try {
      setAcctsLocal(await listAccounts());
      setWriteLocked(false);
      setConflictMsg("该记录刚被其他人修改，已为你刷新最新数据；如仍需修改，请重新打开核对后再保存。");
    } catch {
      setConflictMsg("该记录刚被其他人修改，但刷新最新数据失败。请点击「重试刷新」，成功后再继续操作。");
    }
  };

  /** 写接口返回的 Account 是立即生效的权威对象：原子替换列表与详情快照（版本号随之更新） */
  const applyAccount = (next: Account) => {
    setAcctsLocal((list) => (list ?? []).map((a) => (a.id === next.id ? next : a)));
    setSelected((sel) => (sel?.id === next.id ? next : sel));
  };

  /** 创建成功：以后端返回的 Account 直接入列（不再依赖 fire-and-forget 刷新） */
  const appendAccount = (created: Account) => {
    setAcctsLocal((list) => [...(list ?? []), created]);
  };

  return (
    <div>
      <AdminHeader
        title="账户"
        desc="区分资产/负债与个人私有/家庭共享；私有账户按后端授权过滤"
        action={
          <Button disabled={writeLocked} onClick={() => setAddOpen(true)}>
            <Plus className="h-4 w-4" />
            新增账户
          </Button>
        }
      />

      {conflictMsg && (
        <p role="alert" className="mb-4 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
          {conflictMsg}
        </p>
      )}
      {writeLocked && (
        <div className="mb-4">
          <Button variant="secondary" size="sm" onClick={() => void refreshAfterConflict()}>
            重试刷新
          </Button>
        </div>
      )}

      {loading ? (
        <>
          <SummarySkeleton />
          <CardGridSkeleton count={6} className="grid-cols-2 gap-4 xl:grid-cols-3" />
        </>
      ) : error ? (
        <Card>
          <ErrorState title="账户加载失败" desc={error.message} onRetry={retry} />
        </Card>
      ) : (
        <>
          {/* 汇总条 */}
          <Card className="mb-4 flex divide-x divide-slate-900/5 p-5">
            {summary.map(({ label, cents }) => (
              <div key={label} className="flex-1 px-5 first:pl-0 last:pr-0">
                <p className="text-xs text-ink-faint">{label}</p>
                <p className="mt-1 text-lg">
                  <Amount cents={cents} />
                </p>
              </div>
            ))}
          </Card>

          <Tabs value={tab} onValueChange={setTab} className="mb-4">
            <TabsList>
              <TabsTrigger value="all">全部</TabsTrigger>
              <TabsTrigger value="asset">资产</TabsTrigger>
              <TabsTrigger value="liability">负债</TabsTrigger>
              <TabsTrigger value="family">家庭共享</TabsTrigger>
            </TabsList>
          </Tabs>

          {/* 账户网格 */}
          {filtered.length === 0 ? (
            <Card>
              <EmptyState title="该分类下暂无账户" />
            </Card>
          ) : (
            // Tab 切换时以 key 重挂载网格，卡片逐项重新进场提示内容已更新
            <div key={tab} className="stagger grid grid-cols-2 gap-4 xl:grid-cols-3">
              {filtered.map((a, ai) => {
                const Icon = dataIcon(a.icon);
                return (
                  <Card
                    key={a.id}
                    className="cursor-pointer p-5 transition hover:bg-white/80"
                    style={{ "--stagger-i": ai } as CSSProperties}
                    onClick={() => setSelected(a)}
                  >
                    <div className="flex items-center gap-3">
                      <span
                        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${
                          a.kind === "liability" ? "bg-warn/10 text-warn" : "bg-accent-soft text-accent-ink"
                        }`}
                      >
                        <Icon className="h-5 w-5" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-ink">{a.name}</p>
                        <div className="mt-1 flex flex-wrap gap-1">
                          <Badge tone={a.kind === "liability" ? "warn" : "neutral"}>
                            {a.kind === "asset" ? "资产" : "负债"}
                          </Badge>
                          <Badge tone={a.scope === "family" ? "accent" : "neutral"}>
                            {a.scope === "family" ? "家庭共享" : "私有"}
                          </Badge>
                          <Badge tone={a.permission === "manage" ? "ok" : "neutral"}>
                            {a.permission === "manage" ? "可管理" : "仅查看"}
                          </Badge>
                          {a.currency !== "CNY" && <Badge tone="neutral">{a.currency}</Badge>}
                        </div>
                      </div>
                    </div>
                    <p className="mt-3 text-xl">
                      <Amount cents={a.balanceCents} currency={a.currency} />
                    </p>
                    {a.scope === "personal" && (
                      <p className="mt-1 text-xs text-ink-faint">{memberName(a.ownerId)} · 私有账户</p>
                    )}
                  </Card>
                );
              })}
            </div>
          )}
        </>
      )}

      {/* 账户详情 */}
      <Dialog open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent>
          {selectedFresh && (
            <>
              <DialogHeader title={selectedFresh.name} desc="账户信息与近期交易" />
              <div className="mb-4 grid grid-cols-[4.5rem_1fr] gap-y-1.5 text-sm">
                <span className="text-ink-faint">类型</span>
                <span className="text-ink">{selectedFresh.kind === "asset" ? "资产" : "负债"}</span>
                <span className="text-ink-faint">范围</span>
                <span className="text-ink">
                  {selectedFresh.scope === "family"
                    ? "家庭共享"
                    : `个人私有（${memberName(selectedFresh.ownerId)}）`}
                </span>
                <span className="text-ink-faint">我的权限</span>
                <span className="text-ink">
                  {selectedFresh.permission === "manage" ? "可管理" : "仅查看"}
                </span>
                <span className="text-ink-faint">币种</span>
                <span className="text-ink">{selectedFresh.currency}</span>
                <span className="text-ink-faint">当前余额</span>
                <span className="text-ink">
                  <Amount cents={selectedFresh.balanceCents} currency={selectedFresh.currency} />
                </span>
              </div>
              {selectedFresh.permission === "manage" && (
                <div className="mb-4 flex gap-2">
                  <Button variant="secondary" size="sm" disabled={writeLocked} onClick={() => setEditTarget(selectedFresh)}>
                    <Pencil className="h-3.5 w-3.5" /> 改名 / 图标
                  </Button>
                  {selectedFresh.scope === "personal" && (
                    <Button variant="secondary" size="sm" disabled={writeLocked} onClick={() => setGrantsTarget(selectedFresh)}>
                      <KeyRound className="h-3.5 w-3.5" /> 管理授权
                    </Button>
                  )}
                </div>
              )}
              <p className="mb-1 text-xs font-medium text-ink-faint">近期交易</p>
              {selectedTxns.length === 0 ? (
                <p className="py-4 text-center text-xs text-ink-faint">暂无交易记录</p>
              ) : (
                <div>
                  {selectedTxns.map((t) => {
                    const delta = accountDelta(t, selectedFresh);
                    return (
                    <div
                      key={t.id}
                      className="flex items-center justify-between border-b border-slate-900/5 py-2 last:border-0"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm text-ink">{t.payee}</p>
                        <p className="text-xs text-ink-faint">{fmtDate(t.date)}</p>
                      </div>
                      <Amount cents={delta.cents} currency={delta.currency} signed />
                    </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* 新增账户 */}
      <CreateAccountDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        onCreated={appendAccount}
        locked={writeLocked}
      />

      {/* 改名 / 图标（乐观锁；成功以返回 Account 原子更新，409 由父层关弹窗 + 写锁 + 刷新） */}
      <RenameAccountDialog
        account={editTarget}
        onClose={() => setEditTarget(null)}
        onChanged={applyAccount}
        onConflict={refreshAfterConflict}
        locked={writeLocked}
      />

      {/* 私有账户授权 */}
      <GrantsDialog
        account={grantsTarget}
        members={memberList}
        onClose={() => setGrantsTarget(null)}
        locked={writeLocked}
      />
    </div>
  );
}

/** 新增账户：名称/类型/范围/币种/图标/期初余额，提交到 createAccount */
function CreateAccountDialog({
  open, onOpenChange, onCreated, locked,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 创建成功：回传后端返回的权威 Account */
  onCreated: (created: Account) => void;
  /** 409 写锁：锁定期间禁止提交 */
  locked: boolean;
}) {
  const [form, setForm] = useState({
    name: "", kind: "asset", scope: "personal", currency: "CNY", icon: "wallet", balance: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (locked) return;
    if (!form.name.trim()) {
      setError("请填写账户名称");
      return;
    }
    // 期初余额允许正负（负债口径），但必须精确解析：无效/超界绝不发出请求
    const balanceInput = form.balance.trim();
    const parsedBalance = balanceInput ? parseDecimalCents(balanceInput) : null;
    if (balanceInput && (!parsedBalance || !parsedBalance.ok)) {
      setError(parsedBalance && !parsedBalance.ok ? parsedBalance.error : "期初余额请输入有效金额（最多两位小数）");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await createAccount({
        name: form.name.trim(),
        kind: form.kind as "asset" | "liability",
        scope: form.scope as "personal" | "family",
        currency: form.currency,
        icon: form.icon,
        openingBalanceCents: parsedBalance?.ok ? parsedBalance.cents : 0,
      });
      onOpenChange(false);
      setForm({ name: "", kind: "asset", scope: "personal", currency: "CNY", icon: "wallet", balance: "" });
      onCreated(created);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (o) setError(null); }}>
      <DialogContent>
        <DialogHeader title="新增账户" desc="保存后立即生效；期初余额按最小货币单位提交" />
        <div className="space-y-3">
          <Field label="名称">
            <input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="例如：招商银行储蓄卡"
              className={inputCls}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="类型">
              <Select value={form.kind} onValueChange={(v) => setForm((f) => ({ ...f, kind: v }))}>
                <SelectTrigger />
                <SelectContent>
                  <SelectItem value="asset">资产</SelectItem>
                  <SelectItem value="liability">负债</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="范围">
              <Select value={form.scope} onValueChange={(v) => setForm((f) => ({ ...f, scope: v }))}>
                <SelectTrigger />
                <SelectContent>
                  <SelectItem value="personal">个人私有</SelectItem>
                  <SelectItem value="family">家庭共享</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="币种">
              <Select value={form.currency} onValueChange={(v) => setForm((f) => ({ ...f, currency: v }))}>
                <SelectTrigger />
                <SelectContent>
                  <SelectItem value="CNY">CNY 人民币</SelectItem>
                  <SelectItem value="USD">USD 美元</SelectItem>
                  <SelectItem value="JPY">JPY 日元</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="图标">
              <Select value={form.icon} onValueChange={(v) => setForm((f) => ({ ...f, icon: v }))}>
                <SelectTrigger />
                <SelectContent>
                  {ICON_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
          <Field label="期初余额（元）">
            <input
              type="number"
              step="0.01"
              value={form.balance}
              onChange={(e) => setForm((f) => ({ ...f, balance: e.target.value }))}
              placeholder="0.00"
              className={inputCls}
            />
          </Field>
          {error && <p role="alert" className="text-xs text-danger">{error}</p>}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="secondary" onClick={() => onOpenChange(false)}>取消</Button>
            <Button disabled={busy || locked} onClick={() => void save()}>{busy ? "保存中…" : "保存"}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** 改名 / 换图标：携带 expectedVersion；409 由父层关闭弹窗 + 写锁 + 刷新 */
function RenameAccountDialog({
  account, onClose, onChanged, onConflict, locked,
}: {
  account: Account | null;
  onClose: () => void;
  /** 改名成功：回传接口返回的权威 Account（含最新版本号） */
  onChanged: (next: Account) => void;
  /** 409：父层立即关闭弹窗、进入写锁并刷新最新列表（自身不抛异常） */
  onConflict: () => Promise<void>;
  locked: boolean;
}) {
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("wallet");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (account) {
      setName(account.name);
      setIcon(account.icon);
      setError(null);
    }
  }, [account]);

  if (!account) return null;

  const save = async () => {
    if (locked) return;
    if (!name.trim()) {
      setError("请填写账户名称");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // 以接口返回的 Account 作为立即生效的权威对象（版本号随之更新）
      const next = await updateAccount(account.id, {
        name: name.trim(),
        icon,
        expectedVersion: account.version,
      });
      onClose();
      onChanged(next);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // 父层同步关闭本弹窗、进入写锁并按刷新成败解锁/提示；自身不抛异常
        await onConflict();
      } else {
        setError(errMsg(e));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader title="改名 / 图标" desc={`当前版本 v${account.version}，保存携带版本号防并发覆盖`} />
        <div className="space-y-3">
          <Field label="名称">
            <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} />
          </Field>
          <Field label="图标">
            <Select value={icon} onValueChange={setIcon}>
              <SelectTrigger />
              <SelectContent>
                {ICON_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {error && <p role="alert" className="text-xs text-danger">{error}</p>}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="secondary" onClick={onClose}>取消</Button>
            <Button disabled={busy || locked} onClick={() => void save()}>{busy ? "保存中…" : "保存"}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** 私有账户授权：列出已授权成员，可授予 view/manage 或撤销 */
function GrantsDialog({
  account, members, onClose, locked,
}: {
  account: Account | null;
  members: Member[];
  onClose: () => void;
  /** 409 写锁：锁定期间禁止授权写操作 */
  locked: boolean;
}) {
  const [grants, setGrants] = useState<AccountGrant[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [grantee, setGrantee] = useState("");
  const [permission, setPermission] = useState<"view" | "manage">("view");
  const [busy, setBusy] = useState(false);

  const load = async (accountId: string) => {
    setLoadError(null);
    try {
      setGrants(await listAccountGrants(accountId));
    } catch (e) {
      setLoadError(errMsg(e));
    }
  };

  useEffect(() => {
    setGrants(null);
    setActionError(null);
    setGrantee("");
    setPermission("view");
    if (account) void load(account.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account]);

  if (!account) return null;

  const memberName = (id: string) => members.find((m) => m.id === id)?.name ?? id;
  // 可授权对象：除账户所有人以外的家庭成员
  const candidates = members.filter((m) => m.id !== account.ownerId);

  const run = async (fn: () => Promise<unknown>) => {
    if (locked) return;
    setBusy(true);
    setActionError(null);
    try {
      await fn();
      await load(account.id);
    } catch (e) {
      setActionError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader title="管理授权" desc={`「${account.name}」的查看 / 管理授权`} />
        <div className="space-y-3">
          {loadError ? (
            <ErrorState title="授权列表加载失败" desc={loadError} onRetry={() => void load(account.id)} />
          ) : grants === null ? (
            <div className="space-y-2 py-2" aria-busy="true" aria-label="加载中">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-4 w-1/2" />
            </div>
          ) : grants.length === 0 ? (
            <p className="py-2 text-xs text-ink-faint">暂无授权，只有账户所有人可见。</p>
          ) : (
            <div className="space-y-2">
              {grants.map((g) => (
                <div key={g.memberId} className="flex items-center gap-3">
                  <span className="flex-1 text-sm text-ink">{memberName(g.memberId)}</span>
                  <Badge tone={g.permission === "manage" ? "ok" : "neutral"}>
                    {g.permission === "manage" ? "可管理" : "仅查看"}
                  </Badge>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy || locked}
                    aria-label={`撤销 ${memberName(g.memberId)} 的授权`}
                    onClick={() => void run(() => deleteAccountGrant(account.id, g.memberId))}
                  >
                    <Trash2 className="h-4 w-4 text-ink-faint" />
                  </Button>
                </div>
              ))}
            </div>
          )}

          <div className="border-t border-slate-900/5 pt-3">
            <p className="mb-1.5 text-xs text-ink-faint">新增 / 调整授权</p>
            <div className="flex items-center gap-2">
              <Select value={grantee || undefined} onValueChange={setGrantee}>
                <SelectTrigger className="flex-1" placeholder="选择成员" />
                <SelectContent>
                  {candidates.map((m) => (
                    <SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={permission} onValueChange={(v) => setPermission(v as "view" | "manage")}>
                <SelectTrigger className="w-28" />
                <SelectContent>
                  <SelectItem value="view">仅查看</SelectItem>
                  <SelectItem value="manage">可管理</SelectItem>
                </SelectContent>
              </Select>
              <Button
                size="sm"
                disabled={busy || locked || !grantee}
                onClick={() => void run(() => setAccountGrant(account.id, grantee, permission))}
              >
                授权
              </Button>
            </div>
          </div>

          {actionError && <p role="alert" className="text-xs text-danger">{actionError}</p>}
          <div className="flex justify-end pt-1">
            <Button variant="secondary" onClick={onClose}>完成</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

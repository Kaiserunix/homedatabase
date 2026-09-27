import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, CheckCircle2, FileQuestion } from "lucide-react";
import { Amount, Badge, Button, Card, EmptyState } from "@/components/ui";
import { Checkbox } from "@/components/checkbox";
import { Dialog, DialogContent, DialogHeader } from "@/components/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { useApiQuery } from "@/api/useApi";
import {
  confirmImportCandidates, getImportBatch, listAccounts, listCategories, patchImportCandidate,
  skipDuplicateCandidates, undoImportBatch,
} from "@/api/resources";
import { ApiError } from "@/api/client";
import type { BatchStatus, CandidateStatus, ImportBatch, ImportCandidate } from "@/data/types";
import { fmtDate, fmtDateTime } from "@/lib/utils";
import { isConfirmable, isDuplicate, needsFxReview, parseFxInput } from "@/lib/fx-review";
import { accountOptionSuffix, canWriteAccount } from "@/lib/accounts";

/** 批次状态：全部 8 种如实展示；needs_ocr 是需要人工介入的警告，绝非成功 */
const statusMeta: Record<BatchStatus, { tone: "neutral" | "accent" | "warn" | "ok" | "danger"; label: string }> = {
  queued: { tone: "neutral", label: "排队中" },
  processing: { tone: "accent", label: "解析中" },
  reviewing: { tone: "warn", label: "待确认" },
  needs_ocr: { tone: "warn", label: "需要人工识别" },
  rejected: { tone: "danger", label: "已拒绝" },
  failed: { tone: "danger", label: "解析失败" },
  posted: { tone: "ok", label: "已入账" },
  undone: { tone: "neutral", label: "已撤销" },
};

const rowStatusMeta: Record<CandidateStatus, { tone: "neutral" | "ok"; label: string }> = {
  pending: { tone: "neutral", label: "待确认" },
  confirmed: { tone: "ok", label: "已入账" },
  skipped: { tone: "neutral", label: "已跳过" },
};

const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : "操作失败，请稍后重试");

/**
 * 撤销入口按候选实际状态判定，不只看 batch.status：
 * 有 confirmed 候选的 posted/needs_ocr 批次可整批撤销（含 needs_ocr 混合批次）；
 * 纯 needs_ocr（无 confirmed 候选）、undone 与进行中状态不提供撤销。
 */
const canUndoBatch = (b: ImportBatch) =>
  (b.status === "posted" || b.status === "needs_ocr") &&
  b.candidates.some((c) => c.status === "confirmed");

/** 409 版本冲突提示：候选已被他人改动，刷新后请重新核对 */
const CONFLICT_COPY = "数据刚被其他人修改，已为你刷新最新候选，请重新核对后再操作。";

/** 详情骨架：头部信息条 + 候选表格行 */
function ImportDetailSkeleton() {
  return (
    <div aria-busy="true" aria-label="加载中">
      <div className="mb-6 space-y-2">
        <Skeleton className="h-6 w-56" />
        <Skeleton className="h-3.5 w-72" />
      </div>
      <Card className="space-y-3 p-4">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="flex items-center gap-4">
            <Skeleton className="h-4 w-4 shrink-0 rounded" />
            <Skeleton className="h-3.5 w-20 shrink-0" />
            <Skeleton className="h-3.5 w-32 shrink-0" />
            <Skeleton className="h-5 w-10 shrink-0 rounded-full" />
            <div className="flex-1" />
            <Skeleton className="h-4 w-16 shrink-0" />
            <Skeleton className="h-8 w-32 shrink-0 rounded-xl" />
            <Skeleton className="h-5 w-14 shrink-0 rounded-full" />
          </div>
        ))}
      </Card>
    </div>
  );
}

export default function ImportDetailPage() {
  const { batchId } = useParams();
  // 数据只来自 getImportBatch；404（批次不存在）映射为 null → EmptyState，其他错误走 ErrorState
  const { data, loading, error, retry } = useApiQuery(
    () =>
      getImportBatch(batchId ?? "").catch((e: unknown) =>
        e instanceof ApiError && e.status === 404 ? null : Promise.reject(e),
      ),
    [batchId],
  );
  const { data: cats } = useApiQuery(() => listCategories(), []);
  const { data: accts } = useApiQuery(() => listAccounts(), []);

  // 批次本地副本：PATCH/确认/跳过的响应直接替换其中的候选（版本号随之更新）
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  useEffect(() => {
    setBatch(data);
  }, [data]);

  // 勾选状态：默认勾选「非重复且待确认」的候选；refetch 后保留用户已做的勾选改动
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const [patchingId, setPatchingId] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  // 409 审阅锁：true 期间禁止 confirm/skip，直到刷新最新批次成功
  const [staleReview, setStaleReview] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [postedCount, setPostedCount] = useState(0);
  // 待补汇率候选的人工输入（fxRate 字符串 + 折合人民币元字符串）与行内错误
  const [fxInputs, setFxInputs] = useState<Record<string, { fxRate: string; baseAmount: string }>>({});
  const [fxErrors, setFxErrors] = useState<Record<string, string>>({});
  // 整批撤销（posted / needs_ocr 混合批次，含已入账候选时可用）
  const [undoOpen, setUndoOpen] = useState(false);
  const [undoing, setUndoing] = useState(false);
  const [undoError, setUndoError] = useState<string | null>(null);

  const backLink = (
    <Link to="/imports" className="mb-3 inline-flex items-center gap-1.5 text-sm text-ink-soft hover:text-ink">
      <ArrowLeft className="h-4 w-4" /> 返回导入列表
    </Link>
  );

  if (loading) {
    return (
      <div>
        {backLink}
        <ImportDetailSkeleton />
      </div>
    );
  }

  if (error) {
    return (
      <div>
        {backLink}
        <Card>
          <ErrorState title="导入批次加载失败" desc={error.message} onRetry={retry} />
        </Card>
      </div>
    );
  }

  if (!batch) {
    return (
      <Card>
        <EmptyState
          icon={<FileQuestion className="h-8 w-8" />}
          title="未找到该导入批次"
          hint="链接可能有误，或批次已被移除"
          action={
            <Link
              to="/imports"
              className="glass inline-flex h-8 items-center rounded-xl px-3 text-xs font-medium text-ink transition hover:bg-white/90"
            >
              返回导入列表
            </Link>
          }
        />
      </Card>
    );
  }

  // 待补汇率/重复/其他非 valid 状态的候选不可勾选；疑似重复默认不勾选（与后端确认口径一致）
  const isChecked = (c: ImportBatch["candidates"][number]) =>
    isConfirmable(c) ? (checks[c.id] ?? !isDuplicate(c)) : false;

  const pending = batch.candidates.filter((c) => c.status === "pending");
  // 全选/表头勾选只作用于可确认候选（pending 且 validationStatus=valid）
  const confirmable = pending.filter(isConfirmable);
  const selected = confirmable.filter((c) => isChecked(c));
  const dupSkipped = batch.candidates.filter(
    (c) => isDuplicate(c) && (c.status === "skipped" || !isChecked(c)),
  ).length;
  const hasPendingDup = pending.some((c) => isDuplicate(c));
  const headerChecked =
    confirmable.length === 0
      ? false
      : confirmable.every((c) => isChecked(c))
        ? true
        : confirmable.some((c) => isChecked(c))
          ? "indeterminate"
          : false;

  const setChecked = (id: string, checked: boolean) =>
    setChecks((m) => ({ ...m, [id]: checked }));

  const setAllPending = (checked: boolean) =>
    setChecks((m) => {
      const next = { ...m };
      for (const c of confirmable) next[c.id] = checked;
      return next;
    });

  const invertPending = () =>
    setChecks((m) => {
      const next = { ...m };
      for (const c of confirmable) next[c.id] = !isChecked(c);
      return next;
    });

  /** 用响应里的候选替换本地副本中的同 id 候选（版本号随响应更新） */
  const replaceCandidate = (next: ImportBatch["candidates"][number]) =>
    setBatch((b) =>
      b ? { ...b, candidates: b.candidates.map((c) => (c.id === next.id ? next : c)) } : b,
    );

  /**
   * 409 冲突：立即清空旧勾选并锁定写操作，await 拉取最新批次。
   * 仅刷新成功才显示「已刷新」并解锁；GET 失败则明示失败、保持锁定，
   * 由「重试刷新」按钮重走本函数——绝不在失败时谎报已刷新或放行旧选择。
   */
  const refreshAfterConflict = async () => {
    if (!batch) return;
    setChecks({});
    setStaleReview(true);
    try {
      const fresh = await getImportBatch(batch.id);
      setBatch(fresh);
      setStaleReview(false);
      setActionError(CONFLICT_COPY);
    } catch {
      setActionError("数据刚被其他人修改，但刷新最新内容失败。请点击「重试刷新」，成功后再重新勾选确认。");
    }
  };

  /** 修改建议分类：先 PATCH（携带 expectedVersion），成功后以响应为准；失败保留原状并报错 */
  const changeCategory = async (candId: string, categoryId: string) => {
    const cand = batch.candidates.find((c) => c.id === candId);
    if (!cand) return;
    setPatchingId(candId);
    setActionError(null);
    try {
      const next = await patchImportCandidate(batch.id, candId, {
        suggestedCategoryId: categoryId === "none" ? null : categoryId,
        expectedVersion: cand.version,
      });
      replaceCandidate(next);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict();
      } else {
        setActionError(errMsg(e));
      }
    } finally {
      setPatchingId(null);
    }
  };

  /** 上传时未指定目标账户的候选：行内补选可写账户，PATCH（携带 expectedVersion）后以返回候选更新 */
  const changeAccount = async (candId: string, accountId: string) => {
    const cand = batch.candidates.find((c) => c.id === candId);
    if (!cand) return;
    setPatchingId(candId);
    setActionError(null);
    try {
      const next = await patchImportCandidate(batch.id, candId, {
        targetAccountId: accountId,
        expectedVersion: cand.version,
      });
      replaceCandidate(next);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict();
      } else {
        setActionError(errMsg(e));
      }
    } finally {
      setPatchingId(null);
    }
  };

  /** 待补汇率候选：人工填写 fxRate 与折合人民币金额，PATCH（携带 expectedVersion）后以返回候选为准 */
  const saveFx = async (cand: ImportCandidate) => {
    const input = fxInputs[cand.id] ?? { fxRate: "", baseAmount: "" };
    const parsed = parseFxInput(input.fxRate, input.baseAmount);
    if (!parsed.ok) {
      setFxErrors((m) => ({ ...m, [cand.id]: parsed.error }));
      return;
    }
    setPatchingId(cand.id);
    setFxErrors((m) => {
      const next = { ...m };
      delete next[cand.id];
      return next;
    });
    setActionError(null);
    try {
      const next = await patchImportCandidate(batch.id, cand.id, {
        fxRate: parsed.fxRate,
        baseAmountCents: parsed.baseAmountCents,
        expectedVersion: cand.version,
      });
      replaceCandidate(next);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict();
      } else {
        // 失败保留页面状态：候选仍按待补汇率展示，错误如实显示
        setFxErrors((m) => ({ ...m, [cand.id]: errMsg(e) }));
      }
    } finally {
      setPatchingId(null);
    }
  };

  /** 确认/跳过共用的错误处理：任何失败保留页面状态；409 额外刷新批次并重建勾选 */
  const handleWriteError = async (e: unknown) => {
    if (e instanceof ApiError && e.status === 409) {
      await refreshAfterConflict();
    } else {
      setActionError(errMsg(e));
    }
  };

  const skipDups = async () => {
    const ids = pending.filter((c) => isDuplicate(c)).map((c) => c.id);
    // 候选 PATCH 在途或 409 后未成功刷新时，禁止整批动作（不只靠 DOM disabled）
    if (ids.length === 0 || acting || patchingId !== null || staleReview) return;
    setActing(true);
    setActionError(null);
    try {
      setBatch(await skipDuplicateCandidates(batch.id, ids));
    } catch (e) {
      await handleWriteError(e);
    } finally {
      setActing(false);
    }
  };

  const confirmSelected = async () => {
    // 只提交当前最新批次里仍为 pending 且被勾选的候选 ID；
    // 缺目标账户的候选禁止确认：在前端拦住，不把必然后端 422 的请求发出去
    const missing = selected.filter((c) => !c.targetAccountId);
    if (missing.length > 0) {
      setActionError(`有 ${missing.length} 条已勾选候选尚未选择目标账户，请先在对应行补选账户再确认。`);
      return;
    }
    const ids = selected.map((c) => c.id);
    // 候选 PATCH 在途或 409 后未成功刷新时，禁止整批动作（不只靠 DOM disabled）
    if (ids.length === 0 || acting || patchingId !== null || staleReview) return;
    setActing(true);
    setActionError(null);
    try {
      const next = await confirmImportCandidates(batch.id, ids);
      setBatch(next);
      setPostedCount(next.candidates.filter((c) => ids.includes(c.id) && c.status === "confirmed").length);
    } catch (e) {
      await handleWriteError(e);
    } finally {
      setActing(false);
    }
  };

  /** 整批撤销：失败保留弹窗并如实报错；成功后刷新批次（状态变为 undone，入口随之消失） */
  const confirmUndo = async () => {
    if (undoing) return;
    setUndoing(true);
    setUndoError(null);
    try {
      setBatch(await undoImportBatch(batch.id));
      setUndoOpen(false);
    } catch (e) {
      setUndoError(errMsg(e));
    } finally {
      setUndoing(false);
    }
  };

  const meta = statusMeta[batch.status];
  const categories = cats ?? [];
  const accounts = accts ?? [];
  const accountName = (id: string | null) =>
    id ? (accounts.find((a) => a.id === id)?.name ?? id) : null;
  // 已勾选但缺目标账户的候选数：>0 时禁止确认（前端拦截，不等后端 422）
  const missingAccount = selected.filter((c) => !c.targetAccountId).length;

  return (
    <div>
      {backLink}
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-ink">{batch.filename}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-ink-soft">
          <Badge>{batch.source}</Badge>
          <span>导入于 {fmtDateTime(batch.importedAt)}</span>
          <Badge tone={meta.tone}>{meta.label}</Badge>
          <span className="text-ink-faint">共 {batch.candidates.length} 条候选</span>
          {canUndoBatch(batch) && (
            <Button
              size="sm"
              variant="danger"
              onClick={() => {
                setUndoOpen(true);
                setUndoError(null);
              }}
            >
              整批撤销
            </Button>
          )}
        </div>
        {batch.message && <p className="mt-1 text-xs text-ink-faint">{batch.message}</p>}
      </div>

      {postedCount > 0 && (
        <Card strong className="mb-4 flex items-center gap-2 p-3">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-accent" />
          <p className="flex-1 text-sm text-ink">已入账 {postedCount} 笔交易，可在「账目与分录」中查看。</p>
          <Button size="sm" variant="ghost" onClick={() => setPostedCount(0)}>
            知道了
          </Button>
        </Card>
      )}

      {actionError && (
        <p role="alert" className="mb-4 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
          {actionError}
        </p>
      )}

      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-ink-faint">
              <th className="w-10 px-4 py-3">
                <Checkbox
                  checked={headerChecked}
                  onCheckedChange={(v) => setAllPending(v === true)}
                  aria-label="全选"
                />
              </th>
              <th className="px-2 py-3 font-medium">日期</th>
              <th className="px-2 py-3 font-medium">商户</th>
              <th className="px-2 py-3 font-medium">方向</th>
              <th className="px-2 py-3 text-right font-medium">金额</th>
              <th className="px-2 py-3 font-medium">入账账户</th>
              <th className="px-2 py-3 font-medium">建议分类</th>
              <th className="px-2 py-3 font-medium">去重</th>
              <th className="px-4 py-3 font-medium">状态</th>
            </tr>
          </thead>
          <tbody>
            {batch.candidates.map((r) => (
              <Fragment key={r.id}>
              <tr className="border-t border-slate-900/5">
                <td className="px-4 py-2.5">
                  <Checkbox
                    checked={isChecked(r)}
                    disabled={!isConfirmable(r)}
                    onCheckedChange={(v) => setChecked(r.id, v === true)}
                    aria-label={`选择 ${r.payee}`}
                  />
                </td>
                <td className="px-2 py-2.5 text-ink-soft">{fmtDate(r.date)}</td>
                <td className="px-2 py-2.5 font-medium text-ink">{r.payee}</td>
                <td className="px-2 py-2.5">
                  <Badge tone={r.direction === "expense" ? "neutral" : "accent"}>
                    {r.direction === "expense" ? "支出" : "收入"}
                  </Badge>
                </td>
                <td className="px-2 py-2.5 text-right">
                  <Amount cents={r.amountCents} kind={r.direction} currency={r.currency} />
                </td>
                <td className="px-2 py-2.5">
                  {r.targetAccountId ? (
                    <span className="text-ink-soft">{accountName(r.targetAccountId)}</span>
                  ) : r.status === "pending" ? (
                    <Select
                      value=""
                      onValueChange={(v) => void changeAccount(r.id, v)}
                      disabled={patchingId === r.id || acting}
                    >
                      <SelectTrigger className="h-8 w-36 text-xs" placeholder="选择账户（必选）" />
                      <SelectContent>
                        {accounts.map((a) => (
                          <SelectItem key={a.id} value={a.id} disabled={!canWriteAccount(a)}>
                            {a.name}{accountOptionSuffix(a)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <span className="text-ink-faint">—</span>
                  )}
                </td>
                <td className="px-2 py-2.5">
                  <Select
                    value={r.suggestedCategoryId ?? "none"}
                    onValueChange={(v) => void changeCategory(r.id, v)}
                    disabled={r.status !== "pending" || patchingId === r.id || acting}
                  >
                    <SelectTrigger className="h-8 w-32 text-xs" />
                    <SelectContent>
                      <SelectItem value="none">未分类</SelectItem>
                      {categories
                        .filter((c) => c.active && c.kind === r.direction)
                        .map((c) => (
                          <SelectItem key={c.id} value={c.id}>
                            {c.name}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </td>
                <td className="px-2 py-2.5">
                  {isDuplicate(r) ? (
                    <div className="space-y-0.5">
                      <Badge tone="warn">疑似重复</Badge>
                      <p className="text-[11px] text-ink-faint">
                        {r.duplicateOfTxnId ? "与已有交易相同" : "与来源记录重复"}
                      </p>
                    </div>
                  ) : (
                    <span className="text-ink-faint">—</span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone={rowStatusMeta[r.status].tone}>{rowStatusMeta[r.status].label}</Badge>
                    {needsFxReview(r) && <Badge tone="warn">待补汇率</Badge>}
                    {r.status === "pending" &&
                      !isConfirmable(r) &&
                      !needsFxReview(r) &&
                      !isDuplicate(r) && <Badge tone="warn">{r.validationStatus}</Badge>}
                    {r.status === "pending" && !r.targetAccountId && <Badge tone="warn">待选账户</Badge>}
                  </div>
                </td>
              </tr>
              {needsFxReview(r) && (
                <tr className="border-t border-slate-900/5 bg-warn/5">
                  <td colSpan={9} className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="w-full text-xs text-warn">
                        外币 {r.currency} 候选缺少汇率与折合人民币金额，补全并保存前不能确认入账。
                      </p>
                      <input
                        aria-label={`汇率（${r.payee}）`}
                        inputMode="decimal"
                        placeholder="汇率，如 7.16"
                        value={fxInputs[r.id]?.fxRate ?? ""}
                        onChange={(e) =>
                          setFxInputs((m) => ({
                            ...m,
                            [r.id]: { fxRate: e.target.value, baseAmount: m[r.id]?.baseAmount ?? "" },
                          }))
                        }
                        className="glass h-8 w-32 rounded-xl px-2 text-xs text-ink outline-none placeholder:text-ink-faint"
                      />
                      <input
                        aria-label={`折合人民币（元）（${r.payee}）`}
                        inputMode="decimal"
                        placeholder="折合人民币（元）"
                        value={fxInputs[r.id]?.baseAmount ?? ""}
                        onChange={(e) =>
                          setFxInputs((m) => ({
                            ...m,
                            [r.id]: { fxRate: m[r.id]?.fxRate ?? "", baseAmount: e.target.value },
                          }))
                        }
                        className="glass h-8 w-36 rounded-xl px-2 text-xs text-ink outline-none placeholder:text-ink-faint"
                      />
                      <Button
                        size="sm"
                        disabled={patchingId === r.id || acting}
                        onClick={() => void saveFx(r)}
                      >
                        {patchingId === r.id ? "保存中…" : "保存汇率"}
                      </Button>
                      {fxErrors[r.id] && (
                        <p role="alert" className="w-full text-xs text-danger">
                          {fxErrors[r.id]}
                        </p>
                      )}
                    </div>
                  </td>
                </tr>
              )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </Card>

      <div className="glass-strong sticky bottom-4 z-10 mt-4 flex items-center justify-between rounded-2xl p-3">
        <div className="flex items-center gap-2">
          <p className="text-sm text-ink-soft">
            已选{" "}
            <span key={selected.length} className="inline-block font-medium text-ink motion-safe:animate-value-pop">
              {selected.length}
            </span>{" "}
            / 共 {batch.candidates.length}，跳过重复 {dupSkipped} 条
          </p>
          <Button size="sm" variant="ghost" onClick={() => setAllPending(true)}>
            全选
          </Button>
          <Button size="sm" variant="ghost" onClick={invertPending}>
            反选
          </Button>
        </div>
        <div className="flex items-center gap-2">
          {staleReview && (
            <Button variant="secondary" size="sm" onClick={() => void refreshAfterConflict()}>
              重试刷新
            </Button>
          )}
          {missingAccount > 0 && !staleReview && (
            <p role="alert" className="text-xs text-warn">
              {missingAccount} 条已勾选候选缺账户，先补选再确认
            </p>
          )}
          <Button
            variant="secondary"
            onClick={() => void skipDups()}
            disabled={!hasPendingDup || acting || patchingId !== null || staleReview}
          >
            跳过并标记重复
          </Button>
          <Button
            onClick={() => void confirmSelected()}
            disabled={selected.length === 0 || missingAccount > 0 || acting || patchingId !== null || staleReview}
          >
            {acting ? "提交中…" : "确认入账"}
          </Button>
        </div>
      </div>

      <Dialog open={undoOpen} onOpenChange={(open) => !open && setUndoOpen(false)}>
        <DialogContent>
          <DialogHeader title="整批撤销" desc="撤销后如需入账，须重新导入该文件" />
          <div className="space-y-4">
            <p className="text-xs text-ink-soft">
              该批次已入账的 {batch.candidates.filter((c) => c.status === "confirmed").length}{" "}
              笔交易将被移除，操作会记录审计日志。
            </p>
            {undoError && <p role="alert" className="text-xs text-danger">{undoError}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setUndoOpen(false)}>取消</Button>
              <Button variant="danger" disabled={undoing} onClick={() => void confirmUndo()}>
                {undoing ? "撤销中…" : "确认撤销"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

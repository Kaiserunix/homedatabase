import { Fragment } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, CheckCircle2, FileQuestion, Pencil } from "lucide-react";
import { Amount, Badge, Button, Card, EmptyState } from "@/components/ui";
import { Checkbox } from "@/components/checkbox";
import { Dialog, DialogContent, DialogHeader } from "@/components/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/tabs";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { CandidateStatusBadges } from "@/components/import/CandidateBadges";
import { CandidateEditPanel } from "@/components/import/CandidateEditPanel";
import { SourceFilesCard } from "@/components/import/SourceFilesCard";
import { useApiQuery } from "@/api/useApi";
import { getImportBatch, listAccounts, listCategories, listTransactions } from "@/api/resources";
import { ApiError } from "@/api/client";
import {
  ADMIN_BATCH_STATUS_META, type CandidateFilter, canUndoBatch,
  formatImportSource, inferCandidateCategoryHint,
} from "@/domain/imports";
import { useImportBatchReview } from "@/hooks/useImportBatchReview";
import { fmtDate, fmtDateTime } from "@/lib/utils";
import { isConfirmable, isDuplicate, needsFxReview } from "@/lib/fx-review";
import { accountOptionSuffix, canWriteAccount } from "@/lib/accounts";

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
  const { data, loading, error, retry } = useApiQuery(
    () =>
      getImportBatch(batchId ?? "").catch((e: unknown) =>
        e instanceof ApiError && e.status === 404 ? null : Promise.reject(e),
      ),
    [batchId],
  );
  const { data: cats } = useApiQuery(() => listCategories(), []);
  const { data: accts } = useApiQuery(() => listAccounts(), []);
  // 可选拉取历史交易用于同名商户分类建议（失败静默降级为空列表，绝不阻塞导入详情渲染）
  const { data: historyTxns } = useApiQuery(
    () => listTransactions().catch(() => []),
    [],
  );

  const review = useImportBatchReview(data);
  const { batch, summary } = review;

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

  if (!batch || !summary) {
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

  const meta = ADMIN_BATCH_STATUS_META[batch.status];
  const categories = cats ?? [];
  const accounts = accts ?? [];
  const accountName = (id: string | null) =>
    id ? (accounts.find((a) => a.id === id)?.name ?? id) : null;
  const processedCount = batch.candidates.length - summary.pending.length;

  return (
    <div>
      {backLink}
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-ink">{batch.filename}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-ink-soft">
          <Badge>{formatImportSource(batch.source)}</Badge>
          <span>导入于 {fmtDateTime(batch.importedAt)}</span>
          <Badge tone={meta.tone}>{meta.label}</Badge>
          <span className="text-ink-faint">共 {batch.candidates.length} 条候选</span>
          {summary.lowConfidenceCount > 0 && (
            <Badge tone="warn">低置信度 {summary.lowConfidenceCount} 条</Badge>
          )}
          {canUndoBatch(batch) && (
            <Button
              size="sm"
              variant="danger"
              onClick={() => review.setUndoOpen(true)}
            >
              整批撤销
            </Button>
          )}
        </div>
        {batch.message && <p className="mt-1 text-xs text-ink-faint">{batch.message}</p>}
      </div>

      {/* 源文件加密归档与逐文件解析诊断 */}
      <SourceFilesCard files={batch.files} />

      {review.postedCount > 0 && (
        <Card strong className="mb-4 flex items-center gap-2 p-3">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-accent" />
          <p className="flex-1 text-sm text-ink">
            已入账 {review.postedCount} 笔交易，可在「账目与分录」中查看。
          </p>
          <Button size="sm" variant="ghost" onClick={review.dismissPostedBanner}>
            知道了
          </Button>
        </Card>
      )}

      {review.actionError && (
        <p role="alert" className="mb-4 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
          {review.actionError}
        </p>
      )}

      {batch.candidates.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <Tabs value={review.filter} onValueChange={(v) => review.setFilter(v as CandidateFilter)}>
            <TabsList>
              <TabsTrigger value="all">全部（{batch.candidates.length}）</TabsTrigger>
              <TabsTrigger value="pending">待确认（{summary.pending.length}）</TabsTrigger>
              <TabsTrigger value="attention">需关注（{summary.attentionCount}）</TabsTrigger>
              <TabsTrigger value="processed">已处理（{processedCount}）</TabsTrigger>
            </TabsList>
          </Tabs>
          <p className="text-xs text-ink-faint">
            待确认行点击「修改」可更正日期、商户、金额与入账账户
          </p>
        </div>
      )}

      <Card className="overflow-hidden">
        {batch.candidates.length === 0 ? (
          <EmptyState
            title="当前批次没有候选账目"
            hint={
              batch.status === "needs_ocr"
                ? "源文件正等待 OCR/人工识别，识别生成候选后可在此复核"
                : "请查看上方源文件诊断了解未生成候选的原因"
            }
          />
        ) : review.visibleCandidates.length === 0 ? (
          <EmptyState title="当前筛选下没有候选记录" hint="切换上方筛选标签查看其他候选" />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-ink-faint">
                <th className="w-10 px-4 py-3">
                  <Checkbox
                    checked={summary.headerChecked}
                    onCheckedChange={(v) => review.setAllPending(v === true)}
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
                <th className="px-2 py-3 font-medium">状态</th>
                <th className="px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {review.visibleCandidates.map((r) => {
                const isEditing = review.editingId === r.id && review.editDraft !== null;
                const isConflict = review.conflictCandidateIds.includes(r.id);
                return (
                  <Fragment key={r.id}>
                    <tr className="border-t border-slate-900/5">
                      <td className="px-4 py-2.5">
                        <Checkbox
                          checked={review.isChecked(r)}
                          disabled={!isConfirmable(r)}
                          onCheckedChange={(v) => review.setChecked(r.id, v === true)}
                          aria-label={`选择 ${r.payee}`}
                        />
                      </td>
                      <td className="px-2 py-2.5 text-ink-soft">{fmtDate(r.date)}</td>
                      <td className="px-2 py-2.5">
                        <span className="block font-medium text-ink">{r.payee}</span>
                        {r.originalCategory && (
                          <span className="block text-[11px] text-ink-faint">
                            原分类：{r.originalCategory}
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-2.5">
                        <Badge tone={r.direction === "expense" ? "neutral" : "accent"}>
                          {r.direction === "expense" ? "支出" : "收入"}
                        </Badge>
                      </td>
                      <td className="px-2 py-2.5 text-right">
                        <Amount cents={r.amountCents} kind={r.direction} currency={r.currency} />
                        {r.currency !== "CNY" && r.baseAmountCents != null && (
                          <span className="block text-[11px] text-ink-faint">
                            折 <Amount cents={r.baseAmountCents} currency="CNY" />
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-2.5">
                        {r.targetAccountId ? (
                          <span className="text-ink-soft">{accountName(r.targetAccountId)}</span>
                        ) : r.status === "pending" ? (
                          <Select
                            value=""
                            onValueChange={(v) => void review.changeAccount(r.id, v)}
                            disabled={review.patchingId === r.id || review.acting}
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
                          onValueChange={(v) => void review.changeCategory(r.id, v)}
                          disabled={r.status !== "pending" || review.patchingId === r.id || review.acting}
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
                        {(() => {
                          const hint = inferCandidateCategoryHint(r, categories, historyTxns ?? []);
                          if (!hint) return null;
                          return (
                            <button
                              type="button"
                              disabled={review.patchingId === r.id || review.acting}
                              onClick={() => void review.changeCategory(r.id, hint.categoryId)}
                              className="mt-1 block text-left text-[11px] text-accent hover:underline disabled:opacity-50"
                            >
                              建议：{hint.categoryName}（{hint.reason}）
                            </button>
                          );
                        })()}
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
                      <td className="px-2 py-2.5">
                        <CandidateStatusBadges
                          candidate={r}
                          isConflictHighlighted={isConflict}
                        />
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        {r.status === "pending" && batch.status === "reviewing" ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`修改 ${r.payee}`}
                            disabled={review.patchingId !== null || review.acting || review.staleReview}
                            onClick={() => (isEditing ? review.cancelEdit() : review.openEdit(r))}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                            {isEditing ? "收起" : "修改"}
                          </Button>
                        ) : (
                          <span className="text-xs text-ink-faint">—</span>
                        )}
                      </td>
                    </tr>

                    {isEditing && review.editDraft && (
                      <tr className="border-t border-slate-900/5 bg-accent-soft/15">
                        <td colSpan={10} className="px-4 py-3">
                          <CandidateEditPanel
                            candidate={r}
                            draft={review.editDraft}
                            accounts={accounts}
                            saving={review.patchingId === r.id}
                            error={review.editError}
                            onChange={review.updateEditDraft}
                            onSave={() => void review.saveEdit()}
                            onCancel={review.cancelEdit}
                          />
                        </td>
                      </tr>
                    )}

                    {needsFxReview(r) && (
                      <tr className="border-t border-slate-900/5 bg-warn/5">
                        <td colSpan={10} className="px-4 py-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="w-full text-xs text-warn">
                              外币 {r.currency} 候选缺少汇率与折合人民币金额，补全并保存前不能确认入账。
                            </p>
                            <input
                              aria-label={`汇率（${r.payee}）`}
                              inputMode="decimal"
                              placeholder="汇率，如 7.16"
                              value={review.fxInputs[r.id]?.fxRate ?? ""}
                              onChange={(e) => review.setFxField(r.id, "fxRate", e.target.value)}
                              className="glass h-8 w-32 rounded-xl px-2 text-xs text-ink outline-none placeholder:text-ink-faint"
                            />
                            <input
                              aria-label={`折合人民币（元）（${r.payee}）`}
                              inputMode="decimal"
                              placeholder="折合人民币（元）"
                              value={review.fxInputs[r.id]?.baseAmount ?? ""}
                              onChange={(e) => review.setFxField(r.id, "baseAmount", e.target.value)}
                              className="glass h-8 w-36 rounded-xl px-2 text-xs text-ink outline-none placeholder:text-ink-faint"
                            />
                            <Button
                              size="sm"
                              disabled={review.patchingId === r.id || review.acting}
                              onClick={() => void review.saveFx(r)}
                            >
                              {review.patchingId === r.id ? "保存中…" : "保存汇率"}
                            </Button>
                            {review.fxErrors[r.id] && (
                              <p role="alert" className="w-full text-xs text-danger">
                                {review.fxErrors[r.id]}
                              </p>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      {batch.candidates.length > 0 && (
        <div className="glass-strong sticky bottom-4 z-10 mt-4 flex flex-wrap items-center justify-between gap-2 rounded-2xl p-3">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm text-ink-soft">
              已选{" "}
              <span key={summary.selected.length} className="inline-block font-medium text-ink motion-safe:animate-value-pop">
                {summary.selected.length}
              </span>{" "}
              / 共 {batch.candidates.length}，跳过重复 {summary.dupSkippedCount} 条
            </p>
            <Button size="sm" variant="ghost" onClick={() => review.setAllPending(true)}>
              全选
            </Button>
            <Button size="sm" variant="ghost" onClick={review.invertPending}>
              反选
            </Button>
            {summary.selected.some((c) => c.direction === "expense") && categories.some((c) => c.active && c.kind === "expense") && (
              <Select
                value=""
                onValueChange={(v) => void review.batchAssignCategory(v === "none" ? null : v, "expense")}
                disabled={review.acting || review.patchingId !== null || review.staleReview}
              >
                <SelectTrigger className="h-8 w-36 text-xs" placeholder="批量设支出分类" aria-label="批量设支出分类" />
                <SelectContent>
                  <SelectItem value="none">清空为未分类</SelectItem>
                  {categories
                    .filter((c) => c.active && c.kind === "expense")
                    .map((c) => (
                      <SelectItem key={c.id} value={c.id}>
                        {c.name}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <div className="flex items-center gap-2">
            {review.staleReview && (
              <Button variant="secondary" size="sm" onClick={() => void review.refreshAfterConflict()}>
                重试刷新
              </Button>
            )}
            {summary.missingAccountCount > 0 && !review.staleReview && (
              <p role="alert" className="text-xs text-warn">
                {summary.missingAccountCount} 条已勾选候选缺账户，先补选再确认
              </p>
            )}
            <Button
              variant="secondary"
              onClick={() => void review.skipDups()}
              disabled={!summary.hasPendingDup || review.acting || review.patchingId !== null || review.staleReview}
            >
              跳过并标记重复
            </Button>
            <Button
              onClick={() => void review.confirmSelected()}
              disabled={
                summary.selected.length === 0 ||
                summary.missingAccountCount > 0 ||
                review.acting ||
                review.patchingId !== null ||
                review.staleReview
              }
            >
              {review.acting ? "提交中…" : "确认入账"}
            </Button>
          </div>
        </div>
      )}

      <Dialog open={review.undoOpen} onOpenChange={review.setUndoOpen}>
        <DialogContent>
          <DialogHeader title="整批撤销" desc="撤销后如需入账，须重新导入该文件" />
          <div className="space-y-4">
            <p className="text-xs text-ink-soft">
              该批次已入账的 {batch.candidates.filter((c) => c.status === "confirmed").length}{" "}
              笔交易将被移除，操作会记录审计日志。
            </p>
            {review.undoError && <p role="alert" className="text-xs text-danger">{review.undoError}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => review.setUndoOpen(false)}>取消</Button>
              <Button variant="danger" disabled={review.undoing} onClick={() => void review.confirmUndo()}>
                {review.undoing ? "撤销中…" : "确认撤销"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

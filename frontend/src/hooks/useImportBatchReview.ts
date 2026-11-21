/**
 * 单批次导入复核控制器 Hook：
 * 将候选勾选、乐观锁（expectedVersion）PATCH、409 冲突刷新锁、外币汇率补录、
 * 行内字段修正（日期/商户/金额/账户）与确认入账/跳过重复状态机完全从页面 JSX 剥离。
 */
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "@/api/client";
import {
  confirmImportCandidates, getImportBatch, patchImportCandidate,
  skipDuplicateCandidates, undoImportBatch,
} from "@/api/resources";
import type { ImportBatch, ImportCandidate } from "@/data/types";
import {
  type CandidateEditDraft, type CandidateFilter, createCandidateEditDraft,
  extractConflictIds, filterBatchCandidates, isCandidateChecked,
  selectBatchReviewSummary, validateCandidateEdit,
} from "@/domain/imports";
import { parseFxInput } from "@/lib/fx-review";

const DEFAULT_CONFLICT_COPY = "数据刚被其他人修改，已为你刷新最新候选，请重新核对后再操作。";
const DEFAULT_REFRESH_FAIL_COPY = "数据刚被其他人修改，但刷新最新内容失败。请点击「重试刷新」，成功后再重新勾选确认。";

const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : "操作失败，请稍后重试");

export interface UseImportBatchReviewOptions {
  conflictCopy?: string;
  refreshFailCopy?: string;
  onBatchMutated?: (next: ImportBatch) => void;
}

export function useImportBatchReview(
  initialBatch: ImportBatch | null,
  options: UseImportBatchReviewOptions = {},
) {
  const conflictCopy = options.conflictCopy ?? DEFAULT_CONFLICT_COPY;
  const refreshFailCopy = options.refreshFailCopy ?? DEFAULT_REFRESH_FAIL_COPY;

  const [batch, setBatch] = useState<ImportBatch | null>(initialBatch);
  useEffect(() => {
    setBatch(initialBatch);
  }, [initialBatch]);

  const [filter, setFilter] = useState<CandidateFilter>("all");
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const [patchingId, setPatchingId] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [staleReview, setStaleReview] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [conflictCandidateIds, setConflictCandidateIds] = useState<string[]>([]);
  const [postedCount, setPostedCount] = useState(0);

  // 外币汇率补录输入状态
  const [fxInputs, setFxInputs] = useState<Record<string, { fxRate: string; baseAmount: string }>>({});
  const [fxErrors, setFxErrors] = useState<Record<string, string>>({});

  // 候选行内编辑状态（日期 / 商户 / 金额 / 入账账户）
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<CandidateEditDraft | null>(null);
  const [editError, setEditError] = useState<string | null>(null);

  // 整批撤销弹窗状态
  const [undoOpen, setUndoOpen] = useState(false);
  const [undoing, setUndoing] = useState(false);
  const [undoError, setUndoError] = useState<string | null>(null);

  const summary = useMemo(
    () => (batch ? selectBatchReviewSummary(batch, checks) : null),
    [batch, checks],
  );

  const visibleCandidates = useMemo(
    () => (batch ? filterBatchCandidates(batch.candidates, filter) : []),
    [batch, filter],
  );

  const isChecked = (c: ImportCandidate) => isCandidateChecked(c, checks);

  const setChecked = (id: string, checked: boolean) =>
    setChecks((m) => ({ ...m, [id]: checked }));

  const setAllPending = (checked: boolean) => {
    if (!summary) return;
    setChecks((m) => {
      const next = { ...m };
      for (const c of summary.confirmable) next[c.id] = checked;
      return next;
    });
  };

  const invertPending = () => {
    if (!summary) return;
    setChecks((m) => {
      const next = { ...m };
      for (const c of summary.confirmable) next[c.id] = !isCandidateChecked(c, m);
      return next;
    });
  };

  const applyBatch = (next: ImportBatch) => {
    setBatch(next);
    options.onBatchMutated?.(next);
  };

  const replaceCandidate = (next: ImportCandidate) => {
    setBatch((b) => {
      if (!b) return b;
      const updated = {
        ...b,
        candidates: b.candidates.map((c) => (c.id === next.id ? next : c)),
      };
      options.onBatchMutated?.(updated);
      return updated;
    });
  };

  /**
   * 409 冲突：立即清空旧勾选、关闭行内编辑并锁定写操作，await 拉取最新批次。
   * 仅刷新成功才解锁；GET 失败则诚实报错并保持锁定。
   */
  const refreshAfterConflict = async (customConflictIds?: string[]) => {
    if (!batch) return;
    setChecks({});
    setEditingId(null);
    setEditDraft(null);
    setEditError(null);
    setStaleReview(true);
    setConflictCandidateIds(customConflictIds ?? []);
    try {
      const fresh = await getImportBatch(batch.id);
      applyBatch(fresh);
      setStaleReview(false);
      setActionError(conflictCopy);
    } catch {
      setActionError(refreshFailCopy);
    }
  };

  const handleWriteError = async (e: unknown) => {
    if (e instanceof ApiError && e.status === 409) {
      const { candidateIds } = extractConflictIds(e);
      await refreshAfterConflict(candidateIds);
    } else {
      setActionError(errMsg(e));
    }
  };

  const changeCategory = async (candId: string, categoryId: string) => {
    if (!batch) return;
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
      await handleWriteError(e);
    } finally {
      setPatchingId(null);
    }
  };

  /** 对已勾选的同收支方向待确认候选批量设置建议分类（逐条携带 expectedVersion，遇 409 立即停止并刷新） */
  const batchAssignCategory = async (
    categoryId: string | null,
    direction: "expense" | "income",
  ) => {
    if (!batch || !summary || acting || patchingId !== null || staleReview) return;
    const targets = summary.selected.filter(
      (c) => c.direction === direction && c.suggestedCategoryId !== categoryId,
    );
    if (targets.length === 0) return;
    setActing(true);
    setActionError(null);
    try {
      let currentBatch = batch;
      for (const item of targets) {
        const latest = currentBatch.candidates.find((c) => c.id === item.id) ?? item;
        const updatedCand = await patchImportCandidate(currentBatch.id, latest.id, {
          suggestedCategoryId: categoryId,
          expectedVersion: latest.version,
        });
        currentBatch = {
          ...currentBatch,
          candidates: currentBatch.candidates.map((c) => (c.id === updatedCand.id ? updatedCand : c)),
        };
        applyBatch(currentBatch);
      }
    } catch (e) {
      await handleWriteError(e);
    } finally {
      setActing(false);
    }
  };

  const changeAccount = async (candId: string, accountId: string) => {
    if (!batch) return;
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
      await handleWriteError(e);
    } finally {
      setPatchingId(null);
    }
  };

  const openEdit = (cand: ImportCandidate) => {
    setEditingId(cand.id);
    setEditDraft(createCandidateEditDraft(cand));
    setEditError(null);
    setActionError(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditDraft(null);
    setEditError(null);
  };

  const updateEditDraft = (patch: Partial<CandidateEditDraft>) => {
    setEditDraft((prev) => (prev ? { ...prev, ...patch } : prev));
    setEditError(null);
  };

  const saveEdit = async () => {
    if (!batch || !editingId || !editDraft) return;
    const cand = batch.candidates.find((c) => c.id === editingId);
    if (!cand) return;

    const validated = validateCandidateEdit(editDraft, cand);
    if (!validated.ok) {
      setEditError(validated.error);
      return;
    }
    if (Object.keys(validated.patch).length === 0) {
      cancelEdit();
      return;
    }

    setPatchingId(cand.id);
    setEditError(null);
    setActionError(null);
    try {
      const next = await patchImportCandidate(batch.id, cand.id, {
        ...validated.patch,
        expectedVersion: cand.version,
      });
      replaceCandidate(next);
      cancelEdit();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await handleWriteError(e);
      } else {
        setEditError(errMsg(e));
      }
    } finally {
      setPatchingId(null);
    }
  };

  const setFxField = (candId: string, field: "fxRate" | "baseAmount", value: string) => {
    setFxInputs((m) => ({
      ...m,
      [candId]: {
        fxRate: field === "fxRate" ? value : (m[candId]?.fxRate ?? ""),
        baseAmount: field === "baseAmount" ? value : (m[candId]?.baseAmount ?? ""),
      },
    }));
  };

  const saveFx = async (cand: ImportCandidate) => {
    if (!batch) return;
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
        await handleWriteError(e);
      } else {
        setFxErrors((m) => ({ ...m, [cand.id]: errMsg(e) }));
      }
    } finally {
      setPatchingId(null);
    }
  };

  const skipDups = async () => {
    if (!batch || !summary) return;
    const ids = summary.pendingDupIds;
    if (ids.length === 0 || acting || patchingId !== null || staleReview) return;
    setActing(true);
    setActionError(null);
    try {
      applyBatch(await skipDuplicateCandidates(batch.id, ids));
    } catch (e) {
      await handleWriteError(e);
    } finally {
      setActing(false);
    }
  };

  const confirmSelected = async () => {
    if (!batch || !summary) return;
    if (summary.missingAccountCount > 0) {
      setActionError(
        `有 ${summary.missingAccountCount} 条已勾选候选尚未选择目标账户，请先在对应行补选账户再确认。`,
      );
      return;
    }
    const ids = summary.selected.map((c) => c.id);
    if (ids.length === 0 || acting || patchingId !== null || staleReview) return;
    setActing(true);
    setActionError(null);
    try {
      const next = await confirmImportCandidates(batch.id, ids);
      applyBatch(next);
      setConflictCandidateIds([]);
      setPostedCount(next.candidates.filter((c) => ids.includes(c.id) && c.status === "confirmed").length);
    } catch (e) {
      await handleWriteError(e);
    } finally {
      setActing(false);
    }
  };

  const confirmUndo = async () => {
    if (!batch || undoing) return;
    setUndoing(true);
    setUndoError(null);
    try {
      applyBatch(await undoImportBatch(batch.id));
      setUndoOpen(false);
    } catch (e) {
      const { transactionIds } = extractConflictIds(e);
      const base = errMsg(e);
      setUndoError(
        transactionIds.length > 0 ? `${base}（阻断账目数：${transactionIds.length}）` : base,
      );
    } finally {
      setUndoing(false);
    }
  };

  return {
    batch,
    summary,
    filter,
    setFilter,
    visibleCandidates,
    isChecked,
    setChecked,
    setAllPending,
    invertPending,
    patchingId,
    acting,
    staleReview,
    actionError,
    conflictCandidateIds,
    postedCount,
    dismissPostedBanner: () => setPostedCount(0),
    fxInputs,
    fxErrors,
    setFxField,
    saveFx,
    editingId,
    editDraft,
    editError,
    openEdit,
    cancelEdit,
    updateEditDraft,
    saveEdit,
    changeCategory,
    batchAssignCategory,
    changeAccount,
    skipDups,
    confirmSelected,
    refreshAfterConflict: () => refreshAfterConflict(),
    undoOpen,
    setUndoOpen: (open: boolean) => {
      setUndoOpen(open);
      if (open) setUndoError(null);
    },
    undoing,
    undoError,
    confirmUndo,
  };
}

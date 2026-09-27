import { useEffect, useRef, useState, type CSSProperties } from "react";
import { ChevronDown, ChevronUp, FileUp } from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import { Amount, Badge, Button, Card, EmptyState, SectionTitle } from "@/components/ui";
import { Checkbox } from "@/components/checkbox";
import { Dialog, DialogClose, DialogContent, DialogHeader } from "@/components/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { useApiQuery } from "@/api/useApi";
import {
  confirmImportCandidates, getImportBatch, getImportStatus, listAccounts, listCategories,
  listImportBatches, patchImportCandidate, skipDuplicateCandidates, undoImportBatch, uploadImport,
} from "@/api/resources";
import { ApiError } from "@/api/client";
import type { BatchStatus, Category, ImportBatch, ImportCandidate, ImportJob } from "@/data/types";
import { fmtDate, fmtDateTime, cn } from "@/lib/utils";
import { isConfirmable, isDuplicate, needsFxReview, parseFxInput } from "@/lib/fx-review";
import { accountOptionSuffix, canWriteAccount } from "@/lib/accounts";
import { IMPORT_FILE_ACCEPT, IMPORT_FILE_HINT } from "@/lib/import-files";

/** 上传来源选项：值随表单提交给后端，历史批次可能存有旧的中文来源，展示时统一映射 */
const sources = [
  { value: "wechat", label: "微信支付" },
  { value: "alipay", label: "支付宝" },
  { value: "cmb", label: "招商银行" },
  { value: "csv", label: "手动CSV" },
];
const sourceLabel = (s: string) => sources.find((x) => x.value === s)?.label ?? s;

/** 批次/任务状态展示：needs_ocr 一律是警告样式，绝不显示为成功 */
const statusMeta: Record<BatchStatus, { label: string; tone: "neutral" | "accent" | "danger" | "warn" | "ok" }> = {
  queued: { label: "排队中", tone: "neutral" },
  processing: { label: "解析中", tone: "accent" },
  reviewing: { label: "待确认", tone: "accent" },
  needs_ocr: { label: "需要 OCR/人工识别", tone: "warn" },
  rejected: { label: "无法解析", tone: "danger" },
  failed: { label: "导入失败", tone: "danger" },
  posted: { label: "已入账", tone: "ok" },
  undone: { label: "已撤销", tone: "neutral" },
};

/** 终态：到达后停止轮询 */
const TERMINAL: BatchStatus[] = ["reviewing", "needs_ocr", "rejected", "failed", "posted", "undone"];

/**
 * 撤销入口按候选实际状态判定，不只看 batch.status：
 * 有 confirmed 候选的 posted/needs_ocr 批次可整批撤销（含 needs_ocr 混合批次）；
 * 纯 needs_ocr（无 confirmed 候选）、undone 与进行中状态不提供撤销。
 */
const canUndoBatch = (b: ImportBatch) =>
  (b.status === "posted" || b.status === "needs_ocr") &&
  b.candidates.some((c) => c.status === "confirmed");

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** 批次卡列表骨架：文件名 + 元信息行 + 底部操作条，2 张 */
function BatchListSkeleton() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="加载中">
      {[0, 1].map((i) => (
        <Card key={i} className="space-y-3 p-4">
          <div className="space-y-2">
            <Skeleton className="h-3.5 w-1/2" />
            <Skeleton className="h-3 w-2/5" />
            <Skeleton className="h-3 w-1/3" />
          </div>
          <div className="flex gap-2 border-t border-slate-900/5 pt-3">
            <Skeleton className="h-7 w-14" />
            <Skeleton className="h-7 w-14" />
            <Skeleton className="h-7 w-20" />
            <Skeleton className="ml-auto h-7 w-24" />
          </div>
        </Card>
      ))}
    </div>
  );
}

export default function ImportPage() {
  const { data: batchesData, loading, error, retry } = useApiQuery(() => listImportBatches(), []);
  // 目标账户选项 + 候选分类编辑选项
  const { data: meta } = useApiQuery(
    () => Promise.all([listAccounts(), listCategories()]).then(([accts, cats]) => ({ accts, cats })),
    [],
  );

  // 上传表单状态
  const fileRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [source, setSource] = useState("wechat");
  const [targetAccountId, setTargetAccountId] = useState("");
  // 上传 + 轮询状态
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [jobTerminal, setJobTerminal] = useState<BatchStatus | null>(null);
  const [flowError, setFlowError] = useState<string | null>(null);
  // 候选审阅状态
  const [checkedMap, setCheckedMap] = useState<Record<string, boolean>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [updated, setUpdated] = useState<Record<string, ImportBatch>>({});
  const [batchError, setBatchError] = useState<Record<string, string>>({});
  const [batchBusy, setBatchBusy] = useState<Record<string, boolean>>({});
  const [editingCand, setEditingCand] = useState<Record<string, boolean>>({});
  // 409 审阅锁：某批次为 true 期间禁止其 confirm/skip，直到刷新最新批次成功
  const [staleBatches, setStaleBatches] = useState<Record<string, boolean>>({});
  // 待补汇率候选的人工输入（fxRate 字符串 + 折合人民币元字符串）与行内错误
  const [fxInputs, setFxInputs] = useState<Record<string, { fxRate: string; baseAmount: string }>>({});
  const [fxErrors, setFxErrors] = useState<Record<string, string>>({});
  const [undoTarget, setUndoTarget] = useState<ImportBatch | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const accts = meta?.accts ?? [];
  const cats = meta?.cats ?? [];
  const activeCats = (direction: "expense" | "income"): Category[] =>
    cats.filter((c) => c.active && c.kind === direction);

  const batches = (batchesData ?? []).map((b) => updated[b.id] ?? b);
  const reviewing = batches.filter((b) => b.status === "reviewing");
  const history = batches.filter((b) => b.status !== "reviewing");

  // 勾选默认随数据到位：疑似重复默认不勾选，用户改动进 checkedMap；
  // 仅 pending 且 validationStatus=valid 的候选可勾选（与后端确认口径一致）
  const isChecked = (c: ImportCandidate) =>
    isConfirmable(c) ? (checkedMap[c.id] ?? !isDuplicate(c)) : false;
  const setMany = (entries: [string, boolean][]) =>
    setCheckedMap((prev) => ({ ...prev, ...Object.fromEntries(entries) }));
  const setAll = (cands: ImportCandidate[], v: boolean) =>
    setMany(cands.filter(isConfirmable).map((c) => [c.id, v]));

  const applyBatch = (next: ImportBatch) => setUpdated((m) => ({ ...m, [next.id]: next }));
  const setBatchFlag = (id: string, v: boolean) =>
    setBatchBusy((m) => {
      const next = { ...m };
      if (v) next[id] = true;
      else delete next[id];
      return next;
    });
  const setErr = (id: string, msg: string | null) =>
    setBatchError((m) => {
      const next = { ...m };
      if (msg === null) delete next[id];
      else next[id] = msg;
      return next;
    });

  const errText = (e: unknown) =>
    e instanceof ApiError ? e.message : "操作失败，请稍后重试";

  // 任一候选 PATCH 在途：整批 confirm/skip 都必须等待
  const anyEditing = Object.keys(editingCand).length > 0;

  const setStale = (id: string, v: boolean) =>
    setStaleBatches((m) => {
      const next = { ...m };
      if (v) next[id] = true;
      else delete next[id];
      return next;
    });

  /**
   * 409 冲突：立即清空该批次旧勾选并锁定写操作，await 拉取最新批次。
   * 仅刷新成功才显示「已刷新」并解锁；GET 失败则明示失败、保持锁定，
   * 由「重试刷新」重走本函数——绝不在失败时谎报已刷新或放行旧选择。
   */
  const refreshAfterConflict = async (batchId: string) => {
    const cur = batches.find((b) => b.id === batchId);
    setCheckedMap((m) => {
      const next = { ...m };
      for (const c of cur?.candidates ?? []) delete next[c.id];
      return next;
    });
    setStale(batchId, true);
    try {
      const fresh = await getImportBatch(batchId);
      applyBatch(fresh);
      setCheckedMap((m) => {
        const next = { ...m };
        for (const c of fresh.candidates) delete next[c.id];
        return next;
      });
      setStale(batchId, false);
      setErr(batchId, "数据已被家人修改，已刷新为最新内容，请重新确认。");
    } catch {
      setErr(batchId, "数据已被家人修改，但刷新失败。请点击「重试刷新」，成功后再重新勾选确认。");
    }
  };

  /** 真实上传：POST /imports → 每 ~1.5s 轮询任务状态直到终态 → 刷新批次 */
  const startUpload = async () => {
    if (files.length === 0 || busy) return;
    setBusy(true);
    setFlowError(null);
    setJob(null);
    setJobTerminal(null);
    try {
      const batch = await uploadImport(
        files,
        source,
        targetAccountId && targetAccountId !== "__none__" ? targetAccountId : null,
      );
      // 轮询直到终态（reviewing/needs_ocr/rejected/failed/posted/undone）
      for (;;) {
        if (!alive.current) return;
        const j = await getImportStatus(batch.id);
        if (!alive.current) return;
        setJob(j);
        if ((TERMINAL as string[]).includes(j.status)) {
          setJobTerminal(j.status);
          if (j.status === "reviewing") {
            applyBatch(await getImportBatch(batch.id));
          }
          retry(); // 刷新批次列表
          break;
        }
        await sleep(1500);
      }
      setFiles([]);
    } catch (e) {
      if (alive.current) setFlowError(errText(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  /** 候选分类修改：先 PATCH（带 expectedVersion），用返回的新版本候选覆盖后再允许确认 */
  const changeCategory = async (b: ImportBatch, c: ImportCandidate, suggestedCategoryId: string | null) => {
    setEditingCand((m) => ({ ...m, [c.id]: true }));
    setErr(b.id, null);
    try {
      const next = await patchImportCandidate(b.id, c.id, {
        suggestedCategoryId,
        expectedVersion: c.version,
      });
      const cur = updated[b.id] ?? b;
      applyBatch({
        ...cur,
        candidates: cur.candidates.map((x) => (x.id === c.id ? next : x)),
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict(b.id);
      } else {
        setErr(b.id, errText(e));
      }
    } finally {
      setEditingCand((m) => {
        const next = { ...m };
        delete next[c.id];
        return next;
      });
    }
  };

  /** 上传时未指定目标账户的候选：行内补选可写账户，PATCH（携带 expectedVersion）后以返回候选更新 */
  const changeAccount = async (b: ImportBatch, c: ImportCandidate, accountId: string) => {
    setEditingCand((m) => ({ ...m, [c.id]: true }));
    setErr(b.id, null);
    try {
      const next = await patchImportCandidate(b.id, c.id, {
        targetAccountId: accountId,
        expectedVersion: c.version,
      });
      const cur = updated[b.id] ?? b;
      applyBatch({
        ...cur,
        candidates: cur.candidates.map((x) => (x.id === c.id ? next : x)),
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict(b.id);
      } else {
        setErr(b.id, errText(e));
      }
    } finally {
      setEditingCand((m) => {
        const next = { ...m };
        delete next[c.id];
        return next;
      });
    }
  };

  /** 待补汇率候选：人工填写 fxRate 与折合人民币金额，PATCH（携带 expectedVersion）后以返回候选为准 */
  const saveFx = async (b: ImportBatch, c: ImportCandidate) => {
    const input = fxInputs[c.id] ?? { fxRate: "", baseAmount: "" };
    const parsed = parseFxInput(input.fxRate, input.baseAmount);
    if (!parsed.ok) {
      setFxErrors((m) => ({ ...m, [c.id]: parsed.error }));
      return;
    }
    setEditingCand((m) => ({ ...m, [c.id]: true }));
    setFxErrors((m) => {
      const next = { ...m };
      delete next[c.id];
      return next;
    });
    setErr(b.id, null);
    try {
      const next = await patchImportCandidate(b.id, c.id, {
        fxRate: parsed.fxRate,
        baseAmountCents: parsed.baseAmountCents,
        expectedVersion: c.version,
      });
      const cur = updated[b.id] ?? b;
      applyBatch({
        ...cur,
        candidates: cur.candidates.map((x) => (x.id === c.id ? next : x)),
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict(b.id);
      } else {
        // 失败保留页面状态：候选仍按待补汇率展示，错误如实显示
        setFxErrors((m) => ({ ...m, [c.id]: errText(e) }));
      }
    } finally {
      setEditingCand((m) => {
        const next = { ...m };
        delete next[c.id];
        return next;
      });
    }
  };

  const confirmBatch = async (b: ImportBatch) => {
    // 候选 PATCH 在途或 409 后未成功刷新时，禁止整批动作（不只靠 DOM disabled）
    if (anyEditing || staleBatches[b.id]) return;
    // 缺目标账户的候选禁止确认：在前端拦住，不把必然后端 422 的请求发出去
    const missing = b.candidates.filter((c) => isChecked(c) && !c.targetAccountId);
    if (missing.length > 0) {
      setErr(b.id, `有 ${missing.length} 条候选尚未选择目标账户，请先在行内补选账户再确认。`);
      return;
    }
    const ids = b.candidates
      .filter((c) => isConfirmable(c) && isChecked(c))
      .map((c) => c.id);
    if (ids.length === 0) return;
    setBatchFlag(b.id, true);
    setErr(b.id, null);
    try {
      applyBatch(await confirmImportCandidates(b.id, ids));
      retry();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict(b.id);
      } else {
        setErr(b.id, errText(e));
      }
    } finally {
      setBatchFlag(b.id, false);
    }
  };

  const skipDups = async (b: ImportBatch) => {
    // 候选 PATCH 在途或 409 后未成功刷新时，禁止整批动作（不只靠 DOM disabled）
    if (anyEditing || staleBatches[b.id]) return;
    const dupIds = b.candidates
      .filter((c) => c.status === "pending" && isDuplicate(c))
      .map((c) => c.id);
    if (dupIds.length === 0) return;
    setBatchFlag(b.id, true);
    setErr(b.id, null);
    try {
      applyBatch(await skipDuplicateCandidates(b.id, dupIds));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict(b.id);
      } else {
        setErr(b.id, errText(e));
      }
    } finally {
      setBatchFlag(b.id, false);
    }
  };

  const undoBatch = async (b: ImportBatch) => {
    setBatchFlag(b.id, true);
    setErr(b.id, null);
    try {
      applyBatch(await undoImportBatch(b.id));
      retry();
    } catch (e) {
      setErr(b.id, errText(e));
    } finally {
      setBatchFlag(b.id, false);
    }
  };

  return (
    <div className="px-4">
      <PageHeader back title="导入账单" />

      {/* 上传卡片：多文件 + 来源 + 目标账户 */}
      <Card className="flex flex-col items-center gap-2 border-dashed p-6 text-center">
        <FileUp className="h-8 w-8 text-ink-faint" />
        <p className="text-sm font-medium text-ink">选择微信 / 支付宝 / 银行账单文件</p>
        <p className="text-xs text-ink-faint">支持 {IMPORT_FILE_HINT}，可多选；文件上传后由服务端解析</p>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept={IMPORT_FILE_ACCEPT}
          className="hidden"
          onChange={(e) => {
            setFiles(Array.from(e.target.files ?? []));
            setFlowError(null);
            e.target.value = "";
          }}
        />
        <div className="mt-2 grid w-full grid-cols-2 gap-2 text-left">
          <div>
            <p className="mb-1 text-xs font-medium text-ink-faint">来源</p>
            <Select value={source} onValueChange={setSource}>
              <SelectTrigger />
              <SelectContent>
                {sources.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <p className="mb-1 text-xs font-medium text-ink-faint">目标账户（选填）</p>
            <Select value={targetAccountId} onValueChange={setTargetAccountId}>
              <SelectTrigger placeholder="不指定" />
              <SelectContent>
                <SelectItem value="__none__">不指定</SelectItem>
                {accts.map((a) => (
                  <SelectItem key={a.id} value={a.id} disabled={!canWriteAccount(a)}>
                    {a.name}{accountOptionSuffix(a)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        {files.length > 0 && (
          <p className="mt-1 max-w-full truncate text-xs text-ink-soft">
            已选 {files.length} 个文件：{files.map((f) => f.name).join("、")}
          </p>
        )}
        <div className="mt-2 flex gap-2">
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => fileRef.current?.click()}>
            选择文件
          </Button>
          <Button size="sm" disabled={files.length === 0 || busy} onClick={() => void startUpload()}>
            {busy ? "上传解析中…" : "开始导入"}
          </Button>
        </div>
        {flowError && (
          <p role="alert" className="mt-2 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
            {flowError}
          </p>
        )}
      </Card>

      {/* 导入进度：轮询期间与终态提示 */}
      {job && (
        <Card className="mt-4 p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-medium text-ink">导入进度</p>
            <Badge tone={statusMeta[job.status].tone}>{statusMeta[job.status].label}</Badge>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-900/5">
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${job.progress}%` }}
            />
          </div>
          <p className="mt-2 text-xs text-ink-faint">
            {job.message || "处理中…"}（{job.filesProcessed}/{job.fileCount} 个文件）
          </p>
          {jobTerminal === "needs_ocr" && (
            <p className="mt-2 rounded-xl bg-warn/10 px-3 py-2 text-xs text-warn">
              有 {job.needsOcrCount} 个文件需要 OCR/人工识别，暂不能自动入账。
            </p>
          )}
          {jobTerminal === "reviewing" && (
            <p className="mt-2 text-xs text-ink-soft">
              解析出 {job.candidateCount} 条候选账目，请在下方逐条确认。
            </p>
          )}
          {(jobTerminal === "rejected" || jobTerminal === "failed") && (
            <p className="mt-2 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
              本次导入未能解析账目{job.message ? `：${job.message}` : "。"}
            </p>
          )}
        </Card>
      )}

      {/* 批次列表：骨架 / 错误 / 内容互斥渲染 */}
      {loading ? (
        <>
          <SectionTitle title="待确认批次" className="mt-6" />
          <BatchListSkeleton />
        </>
      ) : error ? (
        <ErrorState
          title="导入批次加载失败"
          desc={error.message}
          onRetry={retry}
          className="mt-6"
        />
      ) : (
      <>
      <SectionTitle title="待确认批次" className="mt-6" />
      {reviewing.length === 0 && <EmptyState title="没有待确认的批次" hint="上传账单文件后会出现在这里" />}
      <div className="stagger space-y-3">
        {reviewing.map((b, bi) => {
          const pendingCands = b.candidates.filter((c) => c.status === "pending");
          const dups = pendingCands.filter((c) => isDuplicate(c)).length;
          const selected = pendingCands.filter((c) => isChecked(c)).length;
          const missingAccount = pendingCands.filter((c) => isChecked(c) && !c.targetAccountId).length;
          const open = expanded[b.id] ?? true;
          return (
            <Card key={b.id} className="p-4" style={{ "--stagger-i": bi } as CSSProperties}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-ink">{b.filename}</p>
                  <p className="mt-0.5 text-xs text-ink-faint">
                    {sourceLabel(b.source)} · 导入于 {fmtDateTime(b.importedAt)}
                  </p>
                  <p className="mt-1 text-xs text-ink-soft">
                    共 {pendingCands.length} 条 / 疑似重复 {dups} 条
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setExpanded((e) => ({ ...e, [b.id]: !open }))}
                >
                  {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                  {open ? "收起" : "展开"}
                </Button>
              </div>

              {/* 高度动画展开/收起：grid-template-rows 0fr→1fr，内层裁剪溢出 */}
              <div
                className={cn(
                  "grid motion-safe:transition-[grid-template-rows] motion-safe:duration-[280ms] motion-safe:ease-out",
                  open ? "[grid-template-rows:1fr]" : "[grid-template-rows:0fr]",
                )}
              >
                <div className="min-h-0 overflow-hidden">
                  <div className="stagger mt-2 divide-y divide-slate-900/5 border-t border-slate-900/5">
                    {b.candidates.map((c, ci) => (
                      <div
                        key={c.id}
                        className="py-2.5"
                        style={{ "--stagger-i": ci } as CSSProperties}
                      >
                        <label className="flex items-center gap-3">
                          <Checkbox
                            checked={c.status === "pending" ? isChecked(c) : false}
                            disabled={!isConfirmable(c)}
                            onCheckedChange={(v) => setMany([[c.id, v === true]])}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-2">
                              <span className="truncate text-sm text-ink">{c.payee}</span>
                              {isDuplicate(c) && <Badge tone="warn">疑似重复</Badge>}
                              {needsFxReview(c) && <Badge tone="warn">待补汇率</Badge>}
                              {c.status === "pending" &&
                                !isConfirmable(c) &&
                                !needsFxReview(c) &&
                                !isDuplicate(c) && <Badge tone="warn">{c.validationStatus}</Badge>}
                              {c.status === "pending" && !c.targetAccountId && <Badge tone="warn">待选账户</Badge>}
                              {c.status === "confirmed" && <Badge tone="ok">已入账</Badge>}
                              {c.status === "skipped" && <Badge tone="neutral">已跳过</Badge>}
                            </span>
                            <span className="text-xs text-ink-faint">{fmtDate(c.date)}</span>
                          </span>
                          <Amount
                            cents={c.amountCents}
                            kind={c.direction}
                            currency={c.currency}
                            className="shrink-0 text-sm"
                          />
                        </label>
                        {needsFxReview(c) && (
                          <div className="ml-8 mt-2 space-y-2 rounded-xl bg-warn/10 p-3">
                            <p className="text-xs text-warn">
                              外币 {c.currency} 候选缺少汇率与折合人民币金额，补全并保存前不能确认入账。
                            </p>
                            <div className="flex items-center gap-2">
                              <input
                                aria-label={`汇率（${c.payee}）`}
                                inputMode="decimal"
                                placeholder="汇率，如 7.16"
                                value={fxInputs[c.id]?.fxRate ?? ""}
                                onChange={(e) =>
                                  setFxInputs((m) => ({
                                    ...m,
                                    [c.id]: { fxRate: e.target.value, baseAmount: m[c.id]?.baseAmount ?? "" },
                                  }))
                                }
                                className="glass h-8 min-w-0 flex-1 rounded-xl px-2 text-xs text-ink outline-none placeholder:text-ink-faint"
                              />
                              <input
                                aria-label={`折合人民币（元）（${c.payee}）`}
                                inputMode="decimal"
                                placeholder="折合人民币（元）"
                                value={fxInputs[c.id]?.baseAmount ?? ""}
                                onChange={(e) =>
                                  setFxInputs((m) => ({
                                    ...m,
                                    [c.id]: { fxRate: m[c.id]?.fxRate ?? "", baseAmount: e.target.value },
                                  }))
                                }
                                className="glass h-8 min-w-0 flex-1 rounded-xl px-2 text-xs text-ink outline-none placeholder:text-ink-faint"
                              />
                              <Button
                                size="sm"
                                className="shrink-0"
                                disabled={!!editingCand[c.id]}
                                onClick={() => void saveFx(b, c)}
                              >
                                {editingCand[c.id] ? "保存中…" : "保存"}
                              </Button>
                            </div>
                            {fxErrors[c.id] && (
                              <p role="alert" className="text-xs text-danger">
                                {fxErrors[c.id]}
                              </p>
                            )}
                          </div>
                        )}
                        {c.status === "pending" && !c.targetAccountId && (
                          <div className="ml-8 mt-1.5 flex items-center gap-2">
                            <span className="text-xs text-ink-faint">账户</span>
                            <Select
                              value=""
                              onValueChange={(v) => void changeAccount(b, c, v)}
                              disabled={!!editingCand[c.id]}
                            >
                              <SelectTrigger className="h-8 flex-1 text-xs" placeholder="选择入账账户（必选）" />
                              <SelectContent>
                                {accts.map((a) => (
                                  <SelectItem key={a.id} value={a.id} disabled={!canWriteAccount(a)}>
                                    {a.name}{accountOptionSuffix(a)}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                            {editingCand[c.id] && (
                              <span className="shrink-0 text-xs text-ink-faint">保存中…</span>
                            )}
                          </div>
                        )}
                        {c.status === "pending" && (
                          <div className="ml-8 mt-1.5 flex items-center gap-2">
                            <span className="text-xs text-ink-faint">分类</span>
                            <Select
                              value={c.suggestedCategoryId ?? ""}
                              onValueChange={(v) =>
                                void changeCategory(b, c, v === "__none__" ? null : v)
                              }
                              disabled={!!editingCand[c.id]}
                            >
                              <SelectTrigger className="h-8 flex-1 text-xs" placeholder="未分类" />
                              <SelectContent>
                                <SelectItem value="__none__">未分类</SelectItem>
                                {activeCats(c.direction).map((cat) => (
                                  <SelectItem key={cat.id} value={cat.id}>
                                    {cat.name}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                            {editingCand[c.id] && (
                              <span className="shrink-0 text-xs text-ink-faint">保存中…</span>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {batchError[b.id] && (
                <p role="alert" className="mt-2 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
                  {batchError[b.id]}
                </p>
              )}

              <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-900/5 pt-3">
                {staleBatches[b.id] && (
                  <Button
                    variant="secondary"
                    size="sm"
                    className="w-full"
                    onClick={() => void refreshAfterConflict(b.id)}
                  >
                    重试刷新
                  </Button>
                )}
                {missingAccount > 0 && !staleBatches[b.id] && (
                  <p role="alert" className="w-full text-xs text-warn">
                    有 {missingAccount} 条已勾选候选尚未选择目标账户，请先在行内补选账户，否则不能确认入账。
                  </p>
                )}
                <Button variant="ghost" size="sm" onClick={() => setAll(pendingCands, true)}>
                  全选
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setAll(pendingCands, false)}>
                  全不选
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void skipDups(b)}
                  disabled={dups === 0 || !!batchBusy[b.id] || anyEditing || !!staleBatches[b.id]}
                >
                  跳过重复
                </Button>
                <Button
                  size="sm"
                  className="ml-auto"
                  disabled={selected === 0 || missingAccount > 0 || !!batchBusy[b.id] || anyEditing || !!staleBatches[b.id]}
                  onClick={() => void confirmBatch(b)}
                >
                  {batchBusy[b.id] ? "提交中…" : (
                    <>
                      确认入账（
                      <span key={selected} className="inline-block motion-safe:animate-value-pop">
                        {selected}
                      </span>
                      ）
                    </>
                  )}
                </Button>
              </div>
            </Card>
          );
        })}
      </div>

      <SectionTitle title="历史批次" className="mt-6" />
      {history.length === 0 && <EmptyState title="还没有历史批次" />}
      <div className="stagger space-y-3">
        {history.map((b, bi) => {
          const meta = statusMeta[b.status];
          return (
            <Card key={b.id} className="p-4" style={{ "--stagger-i": bi } as CSSProperties}>
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-ink">{b.filename}</p>
                  <p className="mt-0.5 text-xs text-ink-faint">
                    {sourceLabel(b.source)} · {fmtDateTime(b.importedAt)} · {b.candidates.length} 条
                  </p>
                  {b.status === "needs_ocr" && (
                    <p className="mt-1 text-xs text-warn">
                      {b.needsOcrCount > 0 ? `${b.needsOcrCount} 个文件` : "文件"}需要 OCR/人工识别，未自动入账。
                    </p>
                  )}
                  {(b.status === "rejected" || b.status === "failed") && b.message && (
                    <p className="mt-1 text-xs text-danger">{b.message}</p>
                  )}
                  {(b.status === "queued" || b.status === "processing") && (
                    <p className="mt-1 text-xs text-ink-faint">{b.message || "正在处理，稍后刷新查看。"}</p>
                  )}
                </div>
                <Badge tone={meta.tone}>{meta.label}</Badge>
              </div>
              {batchError[b.id] && (
                <p role="alert" className="mt-2 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
                  {batchError[b.id]}
                </p>
              )}
              {canUndoBatch(b) && (
                <Button
                  variant="danger"
                  size="sm"
                  className="mt-3"
                  disabled={!!batchBusy[b.id]}
                  onClick={() => setUndoTarget(b)}
                >
                  整批撤销
                </Button>
              )}
            </Card>
          );
        })}
      </div>
      </>
      )}

      <Dialog open={!!undoTarget} onOpenChange={(o) => !o && setUndoTarget(null)}>
        <DialogContent sheet="bottom">
          <DialogHeader
            title="整批撤销"
            desc={
              undoTarget
                ? `「${undoTarget.filename}」已入账的 ${undoTarget.candidates.filter((c) => c.status === "confirmed").length} 条交易将被移除，之后可重新导入。`
                : undefined
            }
          />
          <div className="flex gap-2">
            <DialogClose asChild>
              <Button variant="secondary" className="flex-1">
                取消
              </Button>
            </DialogClose>
            <Button
              variant="danger"
              className="flex-1"
              onClick={() => {
                if (undoTarget) void undoBatch(undoTarget);
                setUndoTarget(null);
              }}
            >
              确认撤销
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

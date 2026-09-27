import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, FileText, FileUp, Info, Upload } from "lucide-react";
import { AdminHeader } from "@/admin/components/AdminHeader";
import { Badge, Button, Card } from "@/components/ui";
import { Dialog, DialogContent, DialogHeader, DialogTrigger } from "@/components/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { useApiQuery } from "@/api/useApi";
import {
  getImportStatus, listAccounts, listImportBatches, undoImportBatch, uploadImport,
} from "@/api/resources";
import { ApiError } from "@/api/client";
import type { BatchStatus, ImportBatch, ImportJob } from "@/data/types";
import { fmtDateTime } from "@/lib/utils";
import { accountOptionSuffix, canWriteAccount } from "@/lib/accounts";
import { IMPORT_FILE_ACCEPT, IMPORT_FILE_HINT } from "@/lib/import-files";
import { isDuplicate } from "@/lib/fx-review";

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

/** 轮询到此即停止（其余状态还会继续变化，无需再刷） */
const TERMINAL: BatchStatus[] = ["reviewing", "needs_ocr", "rejected", "failed", "posted", "undone"];

const SOURCE_OPTIONS = ["微信支付", "支付宝", "招商银行", "手动CSV"];
const DEFAULT_SOURCE = "手动CSV";

/** 目标账户「不指定」哨兵：可选中账户后随时恢复为逐条补选 */
const NO_ACCOUNT = "__none__";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : "操作失败，请稍后重试");

/**
 * 撤销入口按候选实际状态判定，不只看 batch.status：
 * 有 confirmed 候选的 posted/needs_ocr 批次可整批撤销（含 needs_ocr 混合批次）；
 * 纯 needs_ocr（无 confirmed 候选）、undone 与进行中状态不提供撤销。
 */
const canUndoBatch = (b: ImportBatch) =>
  (b.status === "posted" || b.status === "needs_ocr") &&
  b.candidates.some((c) => c.status === "confirmed");

/** 表格骨架：与下方批次表格列位大致对齐 */
function BatchTableSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 px-4 py-3" aria-busy="true" aria-label="加载中">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-3.5 w-44 shrink-0" />
          <Skeleton className="h-5 w-16 shrink-0 rounded-full" />
          <Skeleton className="h-3.5 w-28 shrink-0" />
          <Skeleton className="h-3.5 w-8 shrink-0" />
          <Skeleton className="h-3.5 w-8 shrink-0" />
          <div className="flex-1" />
          <Skeleton className="h-5 w-14 shrink-0 rounded-full" />
          <Skeleton className="h-8 w-16 shrink-0 rounded-xl" />
        </div>
      ))}
    </div>
  );
}

export default function ImportsPage() {
  const [undoId, setUndoId] = useState<string | null>(null);
  const [undoError, setUndoError] = useState<string | null>(null);
  const [undoing, setUndoing] = useState(false);
  const { data: batches, loading, error, retry } = useApiQuery(() => listImportBatches(), []);

  const allBatches = batches ?? [];
  const undoBatch = allBatches.find((b) => b.id === undoId) ?? null;

  const confirmUndo = async () => {
    if (!undoBatch) return;
    setUndoing(true);
    setUndoError(null);
    try {
      await undoImportBatch(undoBatch.id);
      setUndoId(null);
      retry();
    } catch (e) {
      // 失败保留页面状态，如实展示错误，不做本地假成功
      setUndoError(errMsg(e));
    } finally {
      setUndoing(false);
    }
  };

  return (
    <div>
      <AdminHeader
        title="导入与确认"
        desc="文件导入只生成候选账目，确认后才入账"
        action={<UploadDialog onDone={retry} />}
      />

      {/* 批次表格：骨架 / 错误 / 表格互斥渲染 */}
      <Card className="overflow-hidden">
        {loading ? (
          <BatchTableSkeleton />
        ) : error ? (
          <ErrorState title="导入批次加载失败" desc={error.message} onRetry={retry} />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-ink-faint">
                <th className="px-4 py-3 font-medium">文件名</th>
                <th className="px-4 py-3 font-medium">来源</th>
                <th className="px-4 py-3 font-medium">导入时间</th>
                <th className="px-4 py-3 font-medium">候选数</th>
                <th className="px-4 py-3 font-medium">疑似重复</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {allBatches.map((b) => {
                const meta = statusMeta[b.status];
                const dup = b.candidates.filter((c) => isDuplicate(c)).length;
                return (
                  <tr key={b.id} className="border-t border-slate-900/5">
                    <td className="px-4 py-3">
                      <span className="font-medium text-ink">{b.filename}</span>
                      {b.message && (b.status === "failed" || b.status === "rejected" || b.status === "needs_ocr") && (
                        <span className="block max-w-64 truncate text-xs text-ink-faint">{b.message}</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <Badge>{b.source}</Badge>
                    </td>
                    <td className="px-4 py-3 text-ink-soft">{fmtDateTime(b.importedAt)}</td>
                    <td className="px-4 py-3 text-ink-soft">{b.candidateCount}</td>
                    <td className="px-4 py-3">
                      {dup > 0 ? <span className="text-warn">{dup}</span> : <span className="text-ink-faint">0</span>}
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={meta.tone}>{meta.label}</Badge>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {b.status === "reviewing" && (
                        <Link
                          to={`/imports/${b.id}`}
                          className="glass inline-flex h-8 items-center rounded-xl px-3 text-xs font-medium text-ink transition hover:bg-white/90"
                        >
                          去确认
                        </Link>
                      )}
                      {canUndoBatch(b) && (
                        <Button size="sm" variant="danger" onClick={() => { setUndoId(b.id); setUndoError(null); }}>
                          整批撤销
                        </Button>
                      )}
                      {b.status === "undone" && (
                        <Button size="sm" variant="ghost" disabled>
                          已撤销
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      <Card className="mt-4 p-4">
        <div className="flex gap-2.5">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-ink-faint" />
          <div className="space-y-1 text-xs text-ink-soft">
            <p>去重规则：同金额 + 同日期 + 同商户视为疑似重复，确认时可跳过。</p>
            <p>PDF / 图片账单需要 OCR，识别完成前批次会显示「需要人工识别」，不代表导入成功。</p>
            <p>原始账单文件不进入备份，只保留解析出的结构化数据。</p>
          </div>
        </div>
      </Card>

      <Dialog open={undoId !== null} onOpenChange={(open) => !open && setUndoId(null)}>
        <DialogContent>
          <DialogHeader title="整批撤销" desc="撤销后如需入账，须重新导入该文件" />
          {undoBatch && (
            <div className="space-y-4">
              <div className="flex gap-2.5 rounded-xl bg-danger/5 p-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
                <p className="text-xs text-ink-soft">
                  该批次入账的 {undoBatch.candidates.filter((c) => c.status === "confirmed").length}{" "}
                  笔交易将被移除，操作会记录审计日志。
                </p>
              </div>
              {undoError && <p role="alert" className="text-xs text-danger">{undoError}</p>}
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setUndoId(null)}>取消</Button>
                <Button variant="danger" disabled={undoing} onClick={() => void confirmUndo()}>
                  {undoing ? "撤销中…" : "确认撤销"}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** 上传弹窗：多文件 + 来源 + 目标账户 → multipart 上传 → 轮询任务状态直到终态 */
function UploadDialog({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [source, setSource] = useState(DEFAULT_SOURCE);
  const [targetAccountId, setTargetAccountId] = useState("");
  const [stage, setStage] = useState<"idle" | "working" | "done">("idle");
  const [job, setJob] = useState<ImportJob | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const pollSeq = useRef(0);
  const { data: accts } = useApiQuery(() => listAccounts(), []);

  const reset = () => {
    pollSeq.current += 1; // 使进行中的轮询失效
    setFiles([]);
    setSource(DEFAULT_SOURCE);
    setTargetAccountId("");
    setStage("idle");
    setJob(null);
    setBatchId(null);
    setUploadError(null);
  };

  /** 统一的关闭入口：按钮直关与 Esc/遮罩关闭都重置表单（文件/来源/账户/错误） */
  const close = () => {
    setOpen(false);
    reset();
  };

  const startUpload = async () => {
    if (files.length === 0) return;
    const mySeq = ++pollSeq.current;
    setStage("working");
    setUploadError(null);
    setJob(null);
    try {
      const batch = await uploadImport(files, source, targetAccountId || null);
      if (pollSeq.current !== mySeq) return;
      setBatchId(batch.id);
      // 轮询任务状态：约 1.5s 一次，直到终态
      let current = await getImportStatus(batch.id);
      while (!TERMINAL.includes(current.status)) {
        if (pollSeq.current !== mySeq) return;
        setJob(current);
        await sleep(1500);
        current = await getImportStatus(batch.id);
      }
      if (pollSeq.current !== mySeq) return;
      setJob(current);
      setStage("done");
      onDone();
    } catch (e) {
      if (pollSeq.current !== mySeq) return;
      setUploadError(errMsg(e));
      setStage("idle");
    }
  };

  const finalStatus = job?.status;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <FileUp className="h-4 w-4" /> 上传账单
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader title="上传账单" desc={`支持微信支付、支付宝与银行账单（${IMPORT_FILE_HINT}），可多选`} />
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <p className="mb-1.5 text-xs text-ink-soft">选择来源</p>
              <Select value={source} onValueChange={setSource}>
                <SelectTrigger />
                <SelectContent>
                  {SOURCE_OPTIONS.map((s) => (
                    <SelectItem key={s} value={s}>{s}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <p className="mb-1.5 text-xs text-ink-soft">目标账户（选填）</p>
              <Select
                value={targetAccountId || NO_ACCOUNT}
                onValueChange={(v) => setTargetAccountId(v === NO_ACCOUNT ? "" : v)}
              >
                <SelectTrigger />
                <SelectContent>
                  <SelectItem value={NO_ACCOUNT}>不指定，逐条补选</SelectItem>
                  {(accts ?? []).map((a) => (
                    <SelectItem key={a.id} value={a.id} disabled={!canWriteAccount(a)}>
                      {a.name}{accountOptionSuffix(a)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <label
            className="flex cursor-pointer flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-slate-900/15 px-6 py-8 text-center transition hover:border-slate-900/25"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const fs = Array.from(e.dataTransfer.files);
              if (fs.length > 0) setFiles(fs);
            }}
          >
            <input
              type="file"
              multiple
              accept={IMPORT_FILE_ACCEPT}
              className="hidden"
              onChange={(e) => {
                const fs = Array.from(e.target.files ?? []);
                if (fs.length > 0) setFiles(fs);
                e.target.value = "";
              }}
            />
            {files.length > 0 ? (
              <>
                <FileText className="h-6 w-6 text-accent" />
                <p className="text-sm font-medium text-ink">已选 {files.length} 个文件</p>
                <p className="max-w-72 truncate text-xs text-ink-faint">
                  {files.map((f) => f.name).join("、")}
                </p>
              </>
            ) : (
              <>
                <Upload className="h-6 w-6 text-ink-faint" />
                <p className="text-sm text-ink-soft">拖放文件到此处，或点击选择（可多选）</p>
                <p className="text-xs text-ink-faint">{source} · {IMPORT_FILE_HINT}</p>
              </>
            )}
          </label>

          {stage === "working" && (
            <div className="space-y-1.5 rounded-xl bg-accent-soft px-3 py-2.5 text-xs text-accent-ink motion-safe:animate-fade-in">
              <p>{job ? job.message || statusMeta[job.status].label : "上传文件中…"}</p>
              {job && (
                <>
                  <div className="h-1.5 overflow-hidden rounded-full bg-white/50">
                    <div
                      className="h-full rounded-full bg-accent transition-all"
                      style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }}
                    />
                  </div>
                  <p className="text-accent-ink/80">
                    已处理 {job.filesProcessed}/{job.fileCount} 个文件 · 候选 {job.candidateCount} 条
                  </p>
                </>
              )}
            </div>
          )}

          {stage === "done" && job && (
            <div className="space-y-1.5 rounded-xl bg-slate-900/[0.03] px-3 py-2.5 text-xs motion-safe:animate-fade-in">
              <p className="flex items-center gap-2 text-ink">
                处理结果：
                <Badge tone={statusMeta[job.status].tone}>{statusMeta[job.status].label}</Badge>
              </p>
              {job.message && <p className="text-ink-soft">{job.message}</p>}
              <p className="text-ink-faint">
                候选 {job.candidateCount} 条
                {job.needsOcrCount > 0 && ` · ${job.needsOcrCount} 个文件需要人工识别`}
                {job.failedFileCount > 0 && ` · ${job.failedFileCount} 个文件解析失败`}
              </p>
              {finalStatus === "needs_ocr" && (
                <p className="text-warn">PDF / 图片需要先完成 OCR 识别，这不代表导入成功。</p>
              )}
            </div>
          )}

          {uploadError && <p role="alert" className="text-xs text-danger">{uploadError}</p>}

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>
              {stage === "done" ? "完成" : "取消"}
            </Button>
            {stage === "idle" && (
              <Button disabled={files.length === 0} onClick={() => void startUpload()}>
                开始上传
              </Button>
            )}
            {stage === "working" && <Button disabled>处理中…</Button>}
            {stage === "done" && batchId && finalStatus === "reviewing" && (
              <Link
                to={`/imports/${batchId}`}
                onClick={close}
                className="inline-flex h-10 items-center justify-center gap-1.5 rounded-xl bg-gradient-to-b from-accent to-accent-strong px-4 text-sm font-medium text-white transition hover:brightness-[1.04]"
              >
                去确认
              </Link>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

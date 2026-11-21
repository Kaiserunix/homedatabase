import { useState } from "react";
import { ChevronDown, ChevronUp, FileSpreadsheet, Lock } from "lucide-react";
import { Badge, Button, Card } from "@/components/ui";
import type { SourceFile } from "@/data/types";
import { SOURCE_FILE_STATUS_META, summarizeSourceFiles } from "@/domain/imports";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export interface SourceFilesCardProps {
  files: readonly SourceFile[];
  /** 默认展开；手机端可设为仅存在异常时默认展开 */
  defaultExpanded?: boolean;
  compact?: boolean;
}

/**
 * 纯展示组件：源文件加密归档与逐文件解析诊断面板。
 * 展示后端 SourceFileOut 返回的解析状态、识别行数、拒绝行数、重复源文件拦截与 OCR 提示。
 */
export function SourceFilesCard({
  files,
  defaultExpanded,
  compact = false,
}: SourceFilesCardProps) {
  const stats = summarizeSourceFiles(files);
  const [expanded, setExpanded] = useState(defaultExpanded ?? stats.issueCount > 0);

  if (files.length === 0) return null;

  return (
    <Card className={compact ? "mt-2 p-3" : "mb-4 p-4"}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-soft">
          <FileSpreadsheet className="h-4 w-4 shrink-0 text-accent" />
          <span className="font-medium text-ink">源文件诊断（{stats.total} 个文件）</span>
          {stats.parsedCount > 0 && <Badge tone="ok">已解析 {stats.parsedCount}</Badge>}
          {stats.needsOcrCount > 0 && <Badge tone="warn">待 OCR {stats.needsOcrCount}</Badge>}
          {stats.duplicateCount > 0 && <Badge tone="warn">重复文件 {stats.duplicateCount}</Badge>}
          {stats.totalRejectedRows > 0 && (
            <Badge tone="danger">异常跳过 {stats.totalRejectedRows} 行</Badge>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          {expanded ? "收起文件明细" : "查看文件明细"}
        </Button>
      </div>

      {expanded && (
        <div className="mt-3 divide-y divide-slate-900/5 border-t border-slate-900/5">
          {files.map((file) => {
            const status = SOURCE_FILE_STATUS_META[file.parseStatus];
            return (
              <div key={file.id} className="flex flex-wrap items-start justify-between gap-2 py-2.5 text-xs">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate font-medium text-ink">{file.filename}</span>
                    <Badge tone={status.tone}>{status.label}</Badge>
                    {file.encrypted && (
                      <span className="inline-flex items-center gap-1 text-[11px] text-ink-faint">
                        <Lock className="h-3 w-3" /> 加密归档
                      </span>
                    )}
                  </div>
                  <p className="text-ink-faint">
                    大小 {formatFileSize(file.sizeBytes)} · 原始记录 {file.recordCount} 行 · 生成候选{" "}
                    {file.candidateCount} 条
                    {file.rejectedCount > 0 && (
                      <span className="text-danger"> · 拒绝 {file.rejectedCount} 行</span>
                    )}
                  </p>
                  {file.parseMessage && (
                    <p
                      className={
                        file.parseStatus === "failed" || file.parseStatus === "rejected"
                          ? "text-danger"
                          : file.parseStatus === "needs_ocr" || file.parseStatus === "duplicate"
                            ? "text-warn"
                            : "text-ink-soft"
                      }
                    >
                      {file.parseMessage}
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

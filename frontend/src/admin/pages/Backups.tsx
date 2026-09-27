import { useEffect, useState } from "react";
import { Cloud, DatabaseBackup, Download, Globe, HardDrive, ShieldCheck } from "lucide-react";
import { Badge, Button, Card, EmptyState } from "@/components/ui";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { useApiQuery } from "@/api/useApi";
import { createBackup, downloadBackup, listBackups, verifyBackup } from "@/api/resources";
import { ApiError } from "@/api/client";
import type { BackupLocationKind, BackupRecord } from "@/data/types";
import { fmtDateTime } from "@/lib/utils";
import { ledgerDateKey } from "@/lib/ledger-date";
import { AdminHeader } from "../components/AdminHeader";

/** 存放位置说明：只描述位置类型，状态一律以后端返回为准（不宣称任何云厂商已接入） */
const locationMeta: Record<BackupLocationKind, { name: string; short: string; role: string; icon: typeof Cloud }> = {
  tencent: { name: "腾讯云 COS", short: "腾讯云", role: "云端对象存储副本", icon: Cloud },
  local: { name: "本地电脑", short: "本地", role: "本机保留一份加密副本", icon: HardDrive },
  overseas: { name: "海外密文仓库", short: "海外", role: "仅保存密文的副本", icon: Globe },
};

const locationStatusMeta = {
  ok: { label: "正常", tone: "ok", dot: "bg-emerald-500" },
  syncing: { label: "同步中", tone: "accent", dot: "bg-accent" },
  failed: { label: "失败", tone: "danger", dot: "bg-danger" },
} as const;

/** 备份记录自身状态：创建中 / 就绪 / 失败，如实展示 */
const recordStatusMeta: Record<BackupRecord["status"], { label: string; tone: "accent" | "ok" | "danger" }> = {
  creating: { label: "备份中", tone: "accent" },
  ready: { label: "就绪", tone: "ok" },
  failed: { label: "失败", tone: "danger" },
};

const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : "操作失败，请稍后重试");

/** 骨架：三张存放状态卡 + 备份记录表格行 */
function BackupsSkeleton() {
  return (
    <div aria-busy="true" aria-label="加载中">
      <div className="mb-4 grid grid-cols-3 gap-4">
        {[0, 1, 2].map((i) => (
          <Card key={i} className="space-y-2 p-4">
            <div className="flex items-center justify-between gap-2">
              <Skeleton className="h-4 w-28" />
              <Skeleton className="h-5 w-12 rounded-full" />
            </div>
            <Skeleton className="h-3 w-40" />
          </Card>
        ))}
      </div>
      <Card className="space-y-3 p-4">
        {[0, 1].map((i) => (
          <div key={i} className="flex items-center gap-4">
            <Skeleton className="h-3.5 w-32 shrink-0" />
            <Skeleton className="h-3.5 w-16 shrink-0" />
            <Skeleton className="h-5 w-12 shrink-0 rounded-full" />
            <div className="flex-1" />
            <Skeleton className="h-3.5 w-28 shrink-0" />
            <Skeleton className="h-8 w-16 shrink-0 rounded-xl" />
          </div>
        ))}
      </Card>
    </div>
  );
}

export default function BackupsPage() {
  const { data, loading, error, retry } = useApiQuery(() => listBackups(), []);
  const [creating, setCreating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [verifyBusyId, setVerifyBusyId] = useState<string | null>(null);
  const [verifyResult, setVerifyResult] = useState<Record<string, BackupRecord>>({});
  const [verifyError, setVerifyError] = useState<Record<string, string>>({});
  const [downloadBusyId, setDownloadBusyId] = useState<string | null>(null);

  const rows = data ?? [];
  const latest = rows[0];

  // 有备份处于 creating 时每 3s 刷新列表，直到状态落定
  useEffect(() => {
    if (!rows.some((b) => b.status === "creating")) return;
    const t = window.setInterval(() => retry(), 3000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows.some((b) => b.status === "creating")]);

  const doCreate = async () => {
    setCreating(true);
    setActionError(null);
    try {
      await createBackup();
      retry();
    } catch (e) {
      setActionError(errMsg(e));
    } finally {
      setCreating(false);
    }
  };

  const doVerify = async (id: string) => {
    setVerifyBusyId(id);
    setVerifyError((m) => ({ ...m, [id]: "" }));
    try {
      const next = await verifyBackup(id);
      setVerifyResult((m) => ({ ...m, [id]: next }));
      retry();
    } catch (e) {
      setVerifyError((m) => ({ ...m, [id]: errMsg(e) }));
    } finally {
      setVerifyBusyId(null);
    }
  };

  const doDownload = async (b: BackupRecord) => {
    setDownloadBusyId(b.id);
    setActionError(null);
    try {
      const blob = await downloadBackup(b.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `homedatabase-backup-${ledgerDateKey(b.createdAt)}-${b.id}.enc`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setActionError(errMsg(e));
    } finally {
      setDownloadBusyId(null);
    }
  };

  return (
    <>
      <AdminHeader
        title="备份与恢复"
        desc="数据库加密备份；各存放位置状态以后端返回为准"
        action={
          <Button disabled={creating || loading || rows.some((b) => b.status === "creating")} onClick={() => void doCreate()}>
            <DatabaseBackup className="h-4 w-4" /> {creating ? "备份中…" : "立即备份"}
          </Button>
        }
      />

      {actionError && (
        <p role="alert" className="mb-4 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
          {actionError}
        </p>
      )}

      {loading ? (
        <BackupsSkeleton />
      ) : error ? (
        <Card>
          <ErrorState title="备份记录加载失败" desc={error.message} onRetry={retry} />
        </Card>
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState
            icon={<DatabaseBackup className="h-8 w-8" />}
            title="还没有备份"
            hint="点击右上角「立即备份」创建第一份加密备份"
          />
        </Card>
      ) : (
        <>
          {latest && (
            <div className="mb-4 grid grid-cols-3 gap-4">
              {latest.locations.map((loc) => {
                const meta = locationMeta[loc.kind];
                const st = locationStatusMeta[loc.status];
                const Icon = meta.icon;
                return (
                  <Card key={loc.kind} className="p-4">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Icon className="h-4 w-4 text-ink-soft" />
                        <p className="text-sm font-semibold text-ink">{meta.name}</p>
                      </div>
                      <Badge tone={st.tone}>{st.label}</Badge>
                    </div>
                    <p className="mt-1.5 text-xs text-ink-faint">{meta.role}</p>
                  </Card>
                );
              })}
            </div>
          )}

          <Card className="overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-900/10 text-xs text-ink-faint">
                  <th className="px-4 py-2.5 text-left font-medium">时间</th>
                  <th className="px-4 py-2.5 text-right font-medium">大小</th>
                  <th className="px-4 py-2.5 text-left font-medium">状态</th>
                  <th className="px-4 py-2.5 text-left font-medium">存放位置</th>
                  <th className="px-4 py-2.5 text-left font-medium">最近校验</th>
                  <th className="px-4 py-2.5 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((b) => {
                  const st = recordStatusMeta[b.status];
                  const verified = verifyResult[b.id];
                  const shown = verified ?? b;
                  return (
                    <tr key={b.id} className="border-b border-slate-900/5 last:border-0 align-top">
                      <td className="px-4 py-3 font-num tabular-nums text-ink">{fmtDateTime(b.createdAt)}</td>
                      <td className="px-4 py-3 text-right font-num tabular-nums text-ink">{b.sizeMb.toFixed(1)} MB</td>
                      <td className="px-4 py-3">
                        <Badge tone={st.tone}>{st.label}</Badge>
                        {b.encrypted && <Badge tone="accent" className="ml-1">已加密</Badge>}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          {b.locations.map((loc) => (
                            <span key={loc.kind} className="flex items-center gap-1 text-xs text-ink-soft">
                              <span className={`h-1.5 w-1.5 rounded-full ${locationStatusMeta[loc.status].dot}`} />
                              {locationMeta[loc.kind].short}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs text-ink-soft">
                        {shown.lastVerifiedAt ? (
                          <>
                            {fmtDateTime(shown.lastVerifiedAt)}
                            {shown.sha256 && (
                              <span className="block max-w-44 truncate font-num text-ink-faint" title={shown.sha256}>
                                sha256 {shown.sha256.slice(0, 12)}…
                              </span>
                            )}
                          </>
                        ) : (
                          "—"
                        )}
                        {verifyError[b.id] && (
                          <span role="alert" className="block text-danger">{verifyError[b.id]}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex justify-end gap-1.5">
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={verifyBusyId === b.id || b.status !== "ready"}
                            onClick={() => void doVerify(b.id)}
                          >
                            <ShieldCheck className="h-3.5 w-3.5" />
                            {verifyBusyId === b.id ? "校验中…" : "校验"}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={downloadBusyId === b.id || b.status !== "ready"}
                            onClick={() => void doDownload(b)}
                          >
                            <Download className="h-3.5 w-3.5" />
                            {downloadBusyId === b.id ? "下载中…" : "下载"}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
        </>
      )}

      <Card className="mt-4 border-l-2 border-l-warn p-4">
        <p className="text-sm font-semibold text-ink">重要提示</p>
        <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-5 text-ink-soft">
          <li>原始账单文件（CSV / PDF）不进入备份，备份只包含加密后的数据库。</li>
          <li>备份可能存放在多个位置，各位置的同步状态以本页后端返回为准；同步失败会如实显示。</li>
          <li>「校验」只是解密备份并检查 SQLite 完整性（PostgreSQL 部署为 pg_restore 目录可读性），不会在隔离环境真正恢复数据，也不校验借贷平衡。</li>
        </ul>
      </Card>
    </>
  );
}

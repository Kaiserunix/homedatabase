import type { CSSProperties } from "react";
import { PageHeader } from "../components/PageHeader";
import { Amount, Badge, Card, SectionTitle } from "@/components/ui";
import { CardGridSkeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { dataIcon } from "@/components/icons";
import { useApiQuery } from "@/api/useApi";
import { listAccounts } from "@/api/resources";
import { sumCents } from "@/lib/decimal";
import type { Account } from "@/data/types";

function AccountCard({ a, style }: { a: Account; style?: CSSProperties }) {
  const Icon = dataIcon(a.icon);
  return (
    <Card className="flex items-center gap-3 p-4" style={style}>
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-slate-900/5 text-ink-soft">
        <Icon className="h-5 w-5" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink">{a.name}</p>
        <div className="mt-1 flex flex-wrap gap-1">
          {a.scope === "family" ? <Badge tone="accent">家庭共享</Badge> : <Badge tone="neutral">私有</Badge>}
          {a.permission === "manage" ? (
            <Badge tone="ok">可管理</Badge>
          ) : (
            <Badge tone="warn">仅查看</Badge>
          )}
          {a.currency !== "CNY" && <Badge tone="neutral">外币 {a.currency}</Badge>}
        </div>
      </div>
      <Amount cents={a.balanceCents} currency={a.currency} className="shrink-0 text-base font-medium" />
    </Card>
  );
}

export default function AccountsPage() {
  const { data: accts, loading, error, retry } = useApiQuery(() => listAccounts(), []);

  const groups: { title: string; list: Account[] }[] = [
    { title: "资产（个人）", list: (accts ?? []).filter((a) => a.kind === "asset" && a.scope === "personal") },
    { title: "资产（家庭共享）", list: (accts ?? []).filter((a) => a.kind === "asset" && a.scope === "family") },
    { title: "负债", list: (accts ?? []).filter((a) => a.kind === "liability") },
  ];
  // 合计只统计人民币（CNY）账户，不做外币折算；外币账户在上方按原币单列；BigInt 安全求和
  const totalAssetCents = sumCents(
    (accts ?? []).filter((a) => a.kind === "asset" && a.currency === "CNY").map((a) => a.balanceCents),
  );
  // 负债余额为正数债务：负债合计直接展示正数，净资产 = 资产 − 负债（只减一次）
  const totalLiabilityCents = sumCents(
    (accts ?? []).filter((a) => a.kind === "liability" && a.currency === "CNY").map((a) => a.balanceCents),
  );
  const netCents = totalAssetCents - totalLiabilityCents; // bigint − bigint：合法大数合计不溢出
  const hasForeign = (accts ?? []).some((a) => a.currency !== "CNY");

  return (
    <div className="px-4">
      <PageHeader back title="账户与资产负债" />

      {loading ? (
        <CardGridSkeleton count={6} className="mt-5" />
      ) : error ? (
        <ErrorState title="账户加载失败" desc={error.message} onRetry={retry} className="mt-5" />
      ) : (
        <>
      {groups.map((g) => (
        <section key={g.title} className="mt-5 first:mt-0">
          <SectionTitle title={g.title} />
          <div className="stagger space-y-3">
            {g.list.map((a, i) => (
              <AccountCard key={a.id} a={a} style={{ "--stagger-i": i } as CSSProperties} />
            ))}
          </div>
        </section>
      ))}

      <Card strong className="mt-6 p-4">
        <div className="space-y-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-ink-soft">资产合计</span>
            <Amount cents={totalAssetCents} />
          </div>
          <div className="flex items-center justify-between">
            <span className="text-ink-soft">负债合计</span>
            <Amount cents={totalLiabilityCents} />
          </div>
          <div className="flex items-center justify-between border-t border-slate-900/5 pt-2">
            <span className="font-medium text-ink">净资产</span>
            <Amount cents={netCents} className="text-base font-semibold" />
          </div>
        </div>
        <p className="mt-3 text-[11px] text-ink-faint">
          {hasForeign
            ? "合计仅统计人民币（CNY）账户；外币账户按原币单列，另计，未计入以上合计。"
            : "合计仅统计人民币（CNY）账户。"}
        </p>
      </Card>
        </>
      )}
    </div>
  );
}

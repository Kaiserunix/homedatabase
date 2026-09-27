import { Badge, Card, MemberAvatar } from "@/components/ui";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { useApiQuery } from "@/api/useApi";
import { listMembers } from "@/api/resources";
import { useAuth } from "@/auth/AuthContext";
import { AdminHeader } from "../components/AdminHeader";

/** 成员卡骨架：头像行 + 两行信息 */
function MemberCardsSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="加载中">
      {Array.from({ length: count }, (_, i) => (
        <Card key={i} className="space-y-3 p-4">
          <div className="flex items-center gap-3">
            <Skeleton className="h-9 w-9 shrink-0 rounded-full" />
            <Skeleton className="h-4 w-24" />
          </div>
          {[0, 1].map((j) => (
            <div key={j} className="flex items-center justify-between gap-4 border-t border-slate-900/5 pt-2.5">
              <div className="space-y-1.5">
                <Skeleton className="h-3.5 w-20" />
                <Skeleton className="h-3 w-48" />
              </div>
              <Skeleton className="h-5 w-14 shrink-0 rounded-full" />
            </div>
          ))}
        </Card>
      ))}
    </div>
  );
}

export default function MembersPage() {
  const { member: me } = useAuth();
  const { data: memberList, loading, error, retry } = useApiQuery(() => listMembers(), []);

  return (
    <>
      <AdminHeader title="成员与权限" desc="成员固定为爸爸、妈妈、儿子；身份与管理员权限由后端管理" />

      {loading ? (
        <MemberCardsSkeleton />
      ) : error ? (
        <Card>
          <ErrorState title="成员加载失败" desc={error.message} onRetry={retry} />
        </Card>
      ) : (
        <div className="space-y-3">
          {(memberList ?? []).map((m) => (
            <Card key={m.id} className="p-4">
              <div className="flex items-center gap-3">
                <MemberAvatar name={m.name} color={m.color} />
                <p className="flex-1 text-sm font-semibold text-ink">{m.name}</p>
                {m.id === me?.id && <Badge tone="accent">当前用户</Badge>}
              </div>
              <div className="mt-3 space-y-2.5 border-t border-slate-900/5 pt-3">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="text-sm text-ink">家庭成员</p>
                    <p className="mt-0.5 text-xs text-ink-faint">身份：可见家庭公共账户、可被分摊</p>
                  </div>
                  <Badge tone="ok">家庭成员</Badge>
                </div>
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="text-sm text-ink">管理员</p>
                    <p className="mt-0.5 text-xs text-ink-faint">权限：可进入管理台、管理导入/备份/期间锁定</p>
                  </div>
                  <Badge tone={m.isAdmin ? "accent" : "neutral"}>{m.isAdmin ? "管理员" : "成员"}</Badge>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Card className="mt-4 p-4">
        <p className="text-sm font-semibold text-ink">权限说明</p>
        <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-5 text-ink-soft">
          <li>「身份」表示是家庭中的一员，「权限」表示管理能力，两者分开。</li>
          <li>家庭成员默认只能看到自己的私有账户与家庭公共账户，看不到他人未授权的私有账户。</li>
          <li>成员与管理员权限的变更需在服务端进行，本页面只读展示当前状态。</li>
        </ul>
      </Card>
    </>
  );
}

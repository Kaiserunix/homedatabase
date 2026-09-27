import * as React from "react";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui";

/** 骨架屏：加载中的一律用骨架块（不用 spinner），外框沿用玻璃 Card。 */

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden
      className={cn("rounded-lg bg-slate-900/[0.06] motion-safe:animate-pulse", className)}
      {...props}
    />
  );
}

/** 流水列表骨架：圆图标块 + 两行条 + 右侧金额条 */
export function TxnListSkeleton({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <Card className={cn("divide-y divide-slate-900/5 px-4", className)} aria-busy="true" aria-label="加载中">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3 py-3">
          <Skeleton className="h-10 w-10 shrink-0 rounded-full" />
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-3.5 w-2/5" />
            <Skeleton className="h-3 w-3/5" />
          </div>
          <Skeleton className="h-4 w-16 shrink-0" />
        </div>
      ))}
    </Card>
  );
}

/** 卡片网格骨架（账户等卡片列表页通用） */
export function CardGridSkeleton({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <div className={cn("grid gap-4 sm:grid-cols-2", className)} aria-busy="true" aria-label="加载中">
      {Array.from({ length: count }, (_, i) => (
        <Card key={i} className="space-y-3 p-4">
          <div className="flex items-center gap-3">
            <Skeleton className="h-9 w-9 shrink-0 rounded-full" />
            <Skeleton className="h-3.5 w-1/3" />
          </div>
          <Skeleton className="h-5 w-1/2" />
          <Skeleton className="h-3 w-2/3" />
        </Card>
      ))}
    </div>
  );
}

/** 桌面端统计卡骨架：默认 4 卡 */
export function StatCardsSkeleton({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <div className={cn("grid grid-cols-2 gap-4 lg:grid-cols-4", className)} aria-busy="true" aria-label="加载中">
      {Array.from({ length: count }, (_, i) => (
        <Card key={i} className="space-y-2 p-4">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-3 w-20" />
        </Card>
      ))}
    </div>
  );
}

/** 整页骨架：标题条 + 内容区 */
export function PageSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn("space-y-4", className)} aria-busy="true" aria-label="加载中">
      <div className="space-y-2">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-3.5 w-64" />
      </div>
      <Card className="space-y-3 p-4">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-11/12" />
        <Skeleton className="h-4 w-4/5" />
        <Skeleton className="h-4 w-3/5" />
      </Card>
    </div>
  );
}

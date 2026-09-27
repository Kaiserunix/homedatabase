import { cn } from "@/lib/utils";

/** 桌面端页面头部 */
export function AdminHeader({
  title,
  desc,
  action,
  className,
}: {
  title: string;
  desc?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-6 flex items-end justify-between gap-4", className)}>
      <div>
        <h1 className="text-xl font-semibold text-ink">{title}</h1>
        {desc && <p className="mt-1 text-sm text-ink-soft">{desc}</p>}
      </div>
      {action}
    </div>
  );
}
